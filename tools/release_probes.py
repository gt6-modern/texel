#!/usr/bin/env python3
"""注册表"这个版本发布完了吗"的探测(审查 #20 / #21)。

两条判据,一条都不能省:

* **只有 404 才算"还没发布"**(#21)。`curl -fsS` 把 TLS/DNS/连接失败、403、429、5xx
  和真正的 404 全塞进同一个 `else`,于是基础设施故障会被当成"版本不存在",发布流程
  接着去做**不可幂等**的上传,错误归因还会指向上传而不是探测。
* **"已发布"必须按完整资产集验证**(#20)。PyPI 只看版本 JSON 端点是不是 200,就会把
  "wheel 传上去了、sdist 失败了"这种半发布当成完成;Central 只看某一个 artifact 的 POM,
  就会把"另一个 artifact 不在"或"POM 在、主 JAR 不在"当成完成。现在的判据是:PyPI 的
  wheel 与 sdist **都**要在,Central 的 `texel-core` 与 `texel-cli` **每个**都要有 POM
  与主 JAR。缺任何一件都让 job 红,而不是跳过上传、留下缺件的版本。

用法(工作流直接消费 stdout 的 ``skip=`` 行):

    python tools/release_probes.py pypi --project texel-importer --version 0.2.0
    python tools/release_probes.py central --version 0.2.0
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

PYPI_JSON = "https://pypi.org/pypi/{project}/{version}/json"
#: Central 上 artifact 目录下的**某个文件**:`.../<artifact>/<version>/<name>`。
CENTRAL_FILE = ("https://repo1.maven.org/maven2/io/github/gt6-modern/{artifact}/"
                "{version}/{name}")

#: Central 上必须**全部**存在才算发布完成(releasing.md 的发布契约)。
CENTRAL_ARTIFACTS = ("texel-core", "texel-cli")

#: 每个 artifact 都必须有的文件(名字模板)。只看 POM 会把"POM 上传了、主 JAR 没上传"
#: 的半发布当成完成,工作流随后跳过上传,那个版本就永久缺件(审查 P1)。
CENTRAL_FILES = (
    ("POM", "{artifact}-{version}.pom"),
    ("主 JAR", "{artifact}-{version}.jar"),
)

#: PyPI 上必须**两种**都存在:wheel 与 sdist。
PYPI_KINDS = (("wheel", ".whl"), ("sdist", ".tar.gz"))

#: 视为"版本尚未发布"的唯一 HTTP 状态。
NOT_FOUND = 404

PUBLISHED = "published"
MISSING = "missing"


class ProbeError(Exception):
    """探测本身失败(传输错误、权限、限流、5xx、半发布)。

    这类失败**必须**让 job 立刻红:#20/#21 的核心就是"不知道"不等于"没有"。
    """


def fetch(url, timeout=30):
    """GET 一个 URL,返回 ``(status, body)``;404 也返回,其它异常一律 ProbeError。"""
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", None) or response.getcode()
            return status, response.read()
    except urllib.error.HTTPError as failure:
        return failure.code, failure.read()
    except (urllib.error.URLError, OSError, ValueError) as failure:
        # DNS、TLS、连接被重置、超时:全都不是"版本不存在"。
        raise ProbeError("无法访问 %s: %s" % (url, failure))


def classify(status, what):
    """把一个 HTTP 状态翻译成 "missing" 或 ProbeError。"""
    if status == NOT_FOUND:
        return MISSING
    raise ProbeError("%s 返回 HTTP %d:既不是 200(已发布)也不是 404(未发布)"
                     % (what, status))


def _resolved(fetch):
    """``fetch=None`` 表示"用模块里当前的实现"。

    不能写成默认参数 ``fetch=fetch``:那会在**定义时**绑定函数对象,测试替换
    ``release_probes.fetch`` 就换不掉它,注入式的判据会变成哑门禁。
    """
    return fetch if fetch is not None else globals()["fetch"]


def pypi_state(project, version, kinds=PYPI_KINDS, fetch=None):
    """返回 ``(state, detail)``;``state`` 是 ``PUBLISHED`` 或 ``MISSING``。

    200 只说明**版本页存在**。要确认发布完整,得看文件列表里 wheel 与 sdist 都在;
    少任何一个都是半发布,必须报错而不是"已发布"。
    """
    url = PYPI_JSON.format(project=project, version=version)
    status, body = _resolved(fetch)(url)
    if status == NOT_FOUND:
        return MISSING, "%s %s 不在 PyPI 上" % (project, version)
    if status != 200:
        raise ProbeError("PyPI 探测 %s %s 返回 HTTP %d" % (project, version, status))
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as failure:
        raise ProbeError("PyPI 返回的不是 JSON(%s %s): %s" % (project, version, failure))
    filenames = [entry.get("filename", "") for entry in payload.get("urls") or []]
    missing = [label for label, suffix in kinds
               if not any(name.endswith(suffix) for name in filenames)]
    if missing:
        raise ProbeError("%s %s 是**半发布**:缺 %s(已上传: %r)"
                         % (project, version, "/".join(missing), filenames))
    return PUBLISHED, "%s %s 已在 PyPI 上(wheel+sdist)" % (project, version)


def central_state(version, artifacts=CENTRAL_ARTIFACTS, fetch=None):
    """Central 上的发布是否**完整**:每个 artifact 的 POM 与主 JAR 都要在。"""
    fetch = _resolved(fetch)
    present = []
    absent = []
    for artifact in artifacts:
        for label, pattern in CENTRAL_FILES:
            name = pattern.format(artifact=artifact, version=version)
            url = CENTRAL_FILE.format(artifact=artifact, version=version, name=name)
            status, _body = fetch(url)
            item = "%s/%s" % (artifact, label)
            if status == 200:
                present.append(item)
            elif status == NOT_FOUND:
                absent.append(item)
            else:
                raise ProbeError("Central 探测 %s 的 %s 返回 HTTP %d"
                                 % (artifact, label, status))
    if not absent:
        return PUBLISHED, ("Central 上 %s %s 齐全(POM+主 JAR)"
                           % ("/".join(artifacts), version))
    if not present:
        return MISSING, "Central 上还没有 %s %s" % ("/".join(artifacts), version)
    raise ProbeError("%s %s 是**半发布**:已有 %s,缺 %s"
                     % ("/".join(artifacts), version,
                        ", ".join(present), ", ".join(absent)))


def main(argv=None):
    parser = argparse.ArgumentParser(description="注册表发布状态探测")
    subparsers = parser.add_subparsers(dest="command", required=True)
    pypi = subparsers.add_parser("pypi")
    pypi.add_argument("--project", required=True)
    pypi.add_argument("--version", required=True)
    central = subparsers.add_parser("central")
    central.add_argument("--version", required=True)
    arguments = parser.parse_args(argv)

    try:
        if arguments.command == "pypi":
            state, detail = pypi_state(arguments.project, arguments.version)
        else:
            state, detail = central_state(arguments.version)
    except ProbeError as failure:
        # 关闭失败:不知道就不许继续上传。
        sys.stderr.write("release-probe: %s\n" % failure)
        return 1
    # 工作流把 stdout 重定向进 $GITHUB_OUTPUT:那里只放 `name=value` 行,说明走 stderr。
    sys.stdout.write("skip=%s\n" % ("true" if state == PUBLISHED else "false"))
    sys.stderr.write("release-probe: %s\n" % detail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
