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
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from pathlib import Path

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

#: Central Portal 的部署状态查询(POST + `id`)。状态机见发布手册 §6:
#: PENDING → VALIDATING → PUBLISHING → PUBLISHED,或任意时刻 → FAILED。
CENTRAL_STATUS = "https://central.sonatype.com/api/v1/publisher/status?id={deployment_id}"
#: 上传 bundle。响应体就是 deployment id。
CENTRAL_UPLOAD = ("https://central.sonatype.com/api/v1/publisher/upload"
                  "?publishingType=AUTOMATIC&name={name}")

#: 视为"版本尚未发布"的唯一 HTTP 状态。
NOT_FOUND = 404

PUBLISHED = "published"
MISSING = "missing"
#: 部署还在路上(PENDING/VALIDATING/VALIDATED/PUBLISHING):既不是完成,也不是"没有"。
IN_PROGRESS = "in-progress"
#: 部署明确失败(FAILED):不是"没有",而是一个**已知坏的**部署。
FAILED = "failed"

#: 明确表示"还没到 Maven Central"的进行中状态。
CENTRAL_PENDING_STATES = ("PENDING", "VALIDATING", "VALIDATED", "PUBLISHING")


class ProbeError(Exception):
    """探测本身失败(传输错误、权限、限流、5xx、半发布)。

    这类失败**必须**让 job 立刻红:#20/#21 的核心就是"不知道"不等于"没有"。
    """


def central_auth(environ=None):
    """Central Portal 的凭据(用户名:Portal token)。没有凭据时返回 None。"""
    environment = os.environ if environ is None else environ
    user = environment.get("CENTRAL_USERNAME")
    password = environment.get("CENTRAL_PASSWORD")
    if not user or not password:
        return None
    return (user, password)


def authorization_header(auth=None):
    """Central Portal 的 ``Authorization`` 头。

    官方契约是 **`Bearer <base64(username:portal-token)>`**(另有一个非标准的
    `UserToken` 兼容写法),**不是** HTTP Basic。上传与状态查询必须用同一个构造,
    否则状态轮询会被 401 拒绝、每次 Central 发布都卡在等待阶段(审查 P1)。
    """
    credentials = central_auth() if auth is None else auth
    if credentials is None:
        return None
    token = base64.b64encode(("%s:%s" % credentials).encode("utf-8")).decode("ascii")
    return "Bearer " + token


def fetch(url, timeout=30, method="GET", auth=None, body=None, headers=None):
    """请求一个 URL,返回 ``(status, body)``;404 也返回,其它异常一律 ProbeError。"""
    request = urllib.request.Request(url, data=body, method=method)
    header = authorization_header(auth)
    if header is not None:
        request.add_header("Authorization", header)
    for name, value in (headers or {}).items():
        request.add_header(name, value)
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


def central_state(version, artifacts=CENTRAL_ARTIFACTS, fetch=None, wait=0.0, interval=20.0,
                  sleep=None, clock=None):
    """Central 上的发布是否**完整**:每个 artifact 的 POM 与主 JAR 都要在。

    ``wait>0`` 时,若四个文件全都不在,会在 ``wait`` 秒内反复重查:刚被 Portal 接受、
    还在 VALIDATING/PUBLISHING 的 bundle 在 repo1 上暂时全都 404,直接判 MISSING 会让
    重跑再传一遍同一版本(审查 P1)。等窗口只影响"没看见文件"这一侧,看见就是看见。
    """
    fetch = _resolved(fetch)
    sleep = sleep if sleep is not None else time.sleep
    clock = clock if clock is not None else time.monotonic
    deadline = clock() + max(0.0, float(wait))
    while True:
        state, detail, present, absent = _central_files_state(version, artifacts, fetch)
        if state != MISSING or clock() >= deadline:
            if state == MISSING and wait > 0:
                return MISSING, detail + "(已在 %.0f 秒内等待文件出现)" % float(wait)
            return state, detail
        sleep(max(0.0, float(interval)))


def _central_files_state(version, artifacts, fetch):
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
                           % ("/".join(artifacts), version)), present, absent
    if not present:
        return MISSING, ("Central 上还没有 %s %s" % ("/".join(artifacts), version)), present, absent
    raise ProbeError("%s %s 是**半发布**:已有 %s,缺 %s"
                     % ("/".join(artifacts), version,
                        ", ".join(present), ", ".join(absent)))


#: multipart 的固定 boundary:上传体要可复现,不需要随机。
CENTRAL_UPLOAD_BOUNDARY = "texel-central-bundle-boundary"


def central_upload(bundle_path, name, fetch=None, auth=None):
    """把 bundle 上传给 Central Portal,返回 deployment id。

    上传放在这里、而不是工作流里的 `curl --user`,有两个理由:

    * Portal 要的是 `Authorization: Bearer <base64(user:token)>`,而 `curl --user` 发 Basic;
    * 不能让凭据出现在 stdout 里。把"构造好的头"打到 stdout 再让 shell 取用,CodeQL 会
      正确地报"clear-text logging of sensitive information"——那是真的会把 base64 凭据写进
      构建日志(它不等于 secret 原文,GitHub 的掩码盖不住)。所以只有 id 出去。
    """
    fetch = _resolved(fetch)
    payload = Path(bundle_path).read_bytes()
    filename = Path(bundle_path).name
    body = b"".join([
        b"--", CENTRAL_UPLOAD_BOUNDARY.encode("ascii"), b"\r\n",
        b'Content-Disposition: form-data; name="bundle"; filename="',
        filename.encode("utf-8"), b'"\r\n',
        b"Content-Type: application/octet-stream\r\n\r\n",
        payload,
        b"\r\n--", CENTRAL_UPLOAD_BOUNDARY.encode("ascii"), b"--\r\n",
    ])
    url = CENTRAL_UPLOAD.format(name=urllib.parse.quote(name))
    status, response = fetch(
        url, method="POST", auth=auth, body=body,
        headers={"Content-Type": "multipart/form-data; boundary=" + CENTRAL_UPLOAD_BOUNDARY})
    if status not in (200, 201):
        raise ProbeError("Central 上传返回 HTTP %d" % status)
    deployment_id = response.decode("utf-8", "replace").strip()
    if not deployment_id:
        raise ProbeError("Central 接受了 bundle 却没有给出 deployment id")
    return deployment_id


def central_deployment_state(deployment_id, fetch=None, auth=None):
    """问 Central Portal:这个 deployment 走到哪一步了。

    上传接口返回 201 只代表**接受了 bundle**;之后还要经过 PENDING → VALIDATING →
    PUBLISHING 才到达 PUBLISHED,也可能停在 FAILED。把"上传成功"当成"已发布"会让
    GitHub Release 在 Central 最终失败之前就发出去(审查 P1)。
    """
    if not deployment_id or not str(deployment_id).strip():
        raise ProbeError("缺少 deployment id:无法查询 Central 部署状态")
    fetch = _resolved(fetch)
    url = CENTRAL_STATUS.format(deployment_id=urllib.parse.quote(str(deployment_id).strip()))
    status, body = fetch(url, method="POST", auth=auth)
    if status != 200:
        raise ProbeError("Central 部署状态查询返回 HTTP %d(部署 %s)"
                         % (status, deployment_id))
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as failure:
        raise ProbeError("Central 部署状态返回的不是 JSON(部署 %s): %s"
                         % (deployment_id, failure))
    if not isinstance(payload, dict):
        raise ProbeError("Central 部署状态响应格式不对(部署 %s)" % deployment_id)
    state = str(payload.get("deploymentState", "")).strip().upper()
    if state == "PUBLISHED":
        return PUBLISHED, "Central 部署 %s 已 PUBLISHED" % deployment_id
    if state == "FAILED":
        errors = payload.get("errors") or payload.get("error") or ""
        return FAILED, "Central 部署 %s FAILED:%s" % (deployment_id, errors)
    if state in CENTRAL_PENDING_STATES:
        return IN_PROGRESS, "Central 部署 %s 处于 %s" % (deployment_id, state)
    raise ProbeError("Central 部署 %s 的状态无法识别:%r" % (deployment_id, state))


def central_wait(deployment_id, timeout=900.0, interval=20.0, fetch=None, auth=None,
                 sleep=None, clock=None):
    """等部署到 PUBLISHED 才算成功;FAILED 立刻失败,超时也算失败。

    返回值是 ``(PUBLISHED, detail)``;任何"还没完成"的结果都抛 ``ProbeError``,
    因为"等待超时"对发布流程来说就是"不知道",不能当成成功。
    """
    sleep = sleep if sleep is not None else time.sleep
    clock = clock if clock is not None else time.monotonic
    deadline = clock() + max(0.0, float(timeout))
    last = "尚未查询"
    while True:
        state, detail = central_deployment_state(deployment_id, fetch=fetch, auth=auth)
        if state == PUBLISHED:
            return PUBLISHED, detail
        if state == FAILED:
            raise ProbeError(detail)
        last = detail
        if clock() >= deadline:
            raise ProbeError("%s:等待 %.0f 秒仍未 PUBLISHED（最后一次: %s）"
                             % (deployment_id, float(timeout), last))
        sleep(max(0.0, float(interval)))


def main(argv=None):
    parser = argparse.ArgumentParser(description="注册表发布状态探测")
    subparsers = parser.add_subparsers(dest="command", required=True)
    pypi = subparsers.add_parser("pypi")
    pypi.add_argument("--project", required=True)
    pypi.add_argument("--version", required=True)
    central = subparsers.add_parser("central")
    central.add_argument("--version", required=True)
    central.add_argument("--wait-visible", type=float, default=0.0,
                         help="四个文件全 404 时,在这个秒数内重查(给刚上传的部署一点时间)")
    central.add_argument("--interval", type=float, default=20.0)
    wait = subparsers.add_parser("central-wait", help="等 Portal 部署到 PUBLISHED")
    wait.add_argument("--deployment-id", required=True)
    wait.add_argument("--timeout", type=float, default=900.0)
    wait.add_argument("--interval", type=float, default=20.0)
    status = subparsers.add_parser("central-status", help="只查一次部署状态")
    status.add_argument("--deployment-id", required=True)
    upload = subparsers.add_parser("central-upload", help="上传 bundle,只把 deployment id 打到 stdout")
    upload.add_argument("--bundle", required=True)
    upload.add_argument("--name", required=True)
    arguments = parser.parse_args(argv)

    try:
        if arguments.command == "central-upload":
            # **只**把 deployment id 打到 stdout:凭据留在请求头里,不进日志。
            credentials = central_auth()
            if credentials is None:
                sys.stderr.write("release-probe: 缺少 CENTRAL_USERNAME/CENTRAL_PASSWORD\n")
                return 1
            deployment_id = central_upload(arguments.bundle, arguments.name, auth=credentials)
            sys.stdout.write(deployment_id + "\n")
            sys.stderr.write("release-probe: Central 接受了 bundle:%s\n" % deployment_id)
            return 0
        if arguments.command == "central-status":
            # 退出码就是给 shell 用的协议:0 已发布 / 3 进行中 / 4 明确失败 / 1 查不出来。
            state, detail = central_deployment_state(arguments.deployment_id,
                                                     auth=central_auth())
            sys.stderr.write("release-probe: %s\n" % detail)
            return {"published": 0, "in-progress": 3, "failed": 4}.get(state, 1)
        if arguments.command == "central-wait":
            # 上传接口 201 只代表"接受了 bundle";必须等到 PUBLISHED 才算发布完成。
            central_wait(arguments.deployment_id, timeout=arguments.timeout,
                         interval=arguments.interval, auth=central_auth())
            sys.stderr.write("release-probe: 部署 %s 已 PUBLISHED\n" % arguments.deployment_id)
            return 0
        if arguments.command == "pypi":
            state, detail = pypi_state(arguments.project, arguments.version)
        else:
            state, detail = central_state(arguments.version,
                                          wait=arguments.wait_visible,
                                          interval=arguments.interval)
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
