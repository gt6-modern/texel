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
#: 列出某个 namespace 下的部署(带分页)。用在"没有保存的 id"时按 deploymentsName 找回。
CENTRAL_DEPLOYMENTS = ("https://central.sonatype.com/api/v1/publisher/deployments"
                       "?namespace={namespace}&page={page}&size={size}{name_filter}")
#: 本仓库在 Central 上的 namespace。
CENTRAL_NAMESPACE = "io.github.gt6-modern"

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


def authorization_header(auth):
    """Central Portal 的 ``Authorization`` 头(**只**给 Portal 用)。

    官方契约是 **`Bearer <base64(username:portal-token)>`**(另有一个非标准的
    `UserToken` 兼容写法),**不是** HTTP Basic。

    ``auth is None`` 表示**不认证**——这是刻意的:公开端点(repo1 / PyPI)的探测走同一个
    ``fetch``,如果这里从环境变量兜底,Portal token 就会被发给 repo1.maven.org
    (审查 P1)。需要认证的调用方必须显式传凭据(见 ``_portal_auth``)。
    """
    if auth is None:
        return None
    token = base64.b64encode(("%s:%s" % auth).encode("utf-8")).decode("ascii")
    return "Bearer " + token


def _portal_auth(auth=None):
    """Portal API 的凭据:显式传入优先,否则读环境。

    **只有** ``central_upload`` / ``central_deployment_state`` / ``central_deployments``
    这些 Portal 调用可以走这条兜底;``fetch`` 本身永远是"没传就不认证"。
    """
    return auth if auth is not None else central_auth()


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
        url, method="POST", auth=_portal_auth(auth), body=body,
        headers={"Content-Type": "multipart/form-data; boundary=" + CENTRAL_UPLOAD_BOUNDARY})
    if status not in (200, 201):
        raise ProbeError("Central 上传返回 HTTP %d" % status)
    deployment_id = response.decode("utf-8", "replace").strip()
    if not deployment_id:
        raise ProbeError("Central 接受了 bundle 却没有给出 deployment id")
    return deployment_id


def central_deployments(name=None, namespace=CENTRAL_NAMESPACE, fetch=None, auth=None,
                        page_size=50, max_pages=20):
    """列出 Portal 上的部署(按 ``name`` 过滤),用来找回"丢了 id"的那一次。

    上传接口一旦返回,部署就已经在 Portal 上存在了;如果 runner 在此之前被取消、artifact
    没来得及落盘,重跑手里就没有 id。这时按**固定的 deploymentName** 找回它,比盲目重传
    安全(审查 P1)。
    """
    fetch = _resolved(fetch)
    matches = []
    # 服务端过滤是**子串**匹配且参数名没有本地可验证的来源:先带上,遇到 4xx 就退回全量
    # 分页(本地仍做精确同名匹配,所以语义不变,只是多读几页)。
    name_filter = ("&deploymentName=" + urllib.parse.quote(name)) if name else ""
    page = 0
    while page < max_pages:
        url = CENTRAL_DEPLOYMENTS.format(namespace=urllib.parse.quote(namespace),
                                         page=page, size=page_size, name_filter=name_filter)
        status, body = fetch(url, auth=_portal_auth(auth))
        if status in (400, 415, 422) and name_filter:
            sys.stderr.write("release-probe: 部署列表不接受 deploymentName 过滤(%d)，退回全量分页\n"
                             % status)
            name_filter = ""
            continue        # 同一页重试,不消耗页预算
        if status != 200:
            raise ProbeError("Central 部署列表返回 HTTP %d" % status)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as failure:
            raise ProbeError("Central 部署列表返回的不是 JSON: %s" % failure)
        entries = payload.get("deployments") if isinstance(payload, dict) else payload
        if not isinstance(entries, list):
            raise ProbeError("Central 部署列表响应格式不对")
        for entry in entries:
            if not isinstance(entry, dict):
                raise ProbeError("Central 部署列表里有非对象条目")
            deployment_id = entry.get("deploymentId")
            deployment_name = entry.get("deploymentName")
            if not isinstance(deployment_id, str) or not deployment_id:
                raise ProbeError("Central 部署列表里有缺少 deploymentId 的条目")
            if name is None or deployment_name == name:
                matches.append({"id": deployment_id,
                                "name": deployment_name,
                                "state": entry.get("deploymentState")})
        if _last_page(payload, entries, page, page_size):
            return matches
        page += 1
    # 页数用尽而末页仍是满页:无法证明后面没有更多记录。返回一个"看起来完整"的结果会让
    # 调用方把"没找到"当成"不存在"、把"一个"当成"唯一"（审查 P2）——只能关闭失败。
    raise ProbeError("Central 部署列表读了 %d 页仍未结束，无法确认同名部署是否唯一"
                     % max_pages)


def _last_page(payload, entries, page, page_size):
    """这一页是不是最后一页。

    Sonatype 的部署列表把分页元数据放在**顶层**,字段是
    ``page`` / ``pageSize`` / ``pageCount`` / ``totalResultCount``——不是 ``totalPages``,
    也不是嵌套的 ``page.totalPages``。认错字段的后果是:服务端已经说"这是最后一页"时还在
    继续翻页,于是"结果正好占满 max_pages"会被误判成"读不全"而抛错,发布永久卡在恢复步骤
    (审查 P2)。``totalPages`` / ``total_pages`` 只作为兼容兜底保留。
    """
    if isinstance(payload, dict):
        page_count = payload.get("pageCount")
        if isinstance(page_count, int) and page_count > 0:
            return page + 1 >= page_count
        total_results = payload.get("totalResultCount")
        if isinstance(total_results, int) and total_results >= 0:
            size = payload.get("pageSize")
            size = size if isinstance(size, int) and size > 0 else page_size
            return (page + 1) * size >= total_results
        for holder in (payload, payload.get("page")):
            if not isinstance(holder, dict):
                continue
            for key in ("totalPages", "total_pages"):
                total = holder.get(key)
                if isinstance(total, int) and total > 0:
                    return page + 1 >= total
    return len(entries) < page_size


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
    status, body = fetch(url, method="POST", auth=_portal_auth(auth))
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


def central_recover(name, version, deployment_id=None, namespace=CENTRAL_NAMESPACE,
                    fetch=None, auth=None, wait_timeout=1800.0, wait_visible=300.0,
                    interval=20.0, sleep=None, clock=None):
    """重跑判据:**这个版本还需不需要上传**。

    返回 ``(PUBLISHED, detail)`` = "已经发布,别再传";``(MISSING, detail)`` = "确实没有,
    该传"。拿不准的一律抛 ``ProbeError``(关闭失败),因为"不知道"被当成"没有"就会重复部署。

    判断顺序(每一格都有对应门禁):

    1. 有保存的 deployment id → 问它的状态:已发布 → 别再传;进行中 → 等它 PUBLISHED;
       明确 FAILED → 这一份没救了,继续往下判。
    2. 没 id → 按固定的 deploymentName 问 Portal:有 PUBLISHED 的同名部署 → 别再传;
       恰好一个进行中的 → 等它;多个进行中或状态无法识别 → 抛错;只剩 FAILED 或没有 →
       继续往下判。
    3. 最后问 repo1(给刚上传的部署一点可见性时间):四个文件齐了 → 已发布;否则 → 该传。
    """
    fetch = _resolved(fetch)

    if deployment_id:
        state, detail = central_deployment_state(deployment_id, fetch=fetch, auth=auth)
        if state == PUBLISHED:
            return PUBLISHED, detail
        if state == IN_PROGRESS:
            central_wait(deployment_id, timeout=wait_timeout, interval=interval,
                         fetch=fetch, auth=auth, sleep=sleep, clock=clock)
            return PUBLISHED, "部署 %s 已 PUBLISHED" % deployment_id
        # FAILED:继续判"能不能重传"。

    matches = central_deployments(name=name, namespace=namespace, fetch=fetch, auth=auth)
    states = [(item["id"], str(item.get("state") or "").upper()) for item in matches]
    published = [item for item in states if item[1] == "PUBLISHED"]
    if published:
        return PUBLISHED, "Central 上已有 PUBLISHED 的同名部署 %s" % published[0][0]
    resumable = [item for item in states if item[1] in CENTRAL_PENDING_STATES]
    unknown = [item for item in states
               if item[1] not in CENTRAL_PENDING_STATES and item[1] not in ("FAILED", "PUBLISHED")]
    if unknown:
        raise ProbeError("同名部署里有无法识别的状态，拒绝猜测: %r" % (unknown,))
    if len(resumable) > 1:
        raise ProbeError("同名部署有 %d 个仍在进行中，无法判断该续哪一个: %r"
                         % (len(resumable), [item[0] for item in resumable]))
    if resumable:
        central_wait(resumable[0][0], timeout=wait_timeout, interval=interval,
                     fetch=fetch, auth=auth, sleep=sleep, clock=clock)
        return PUBLISHED, "部署 %s 已 PUBLISHED" % resumable[0][0]
    # 只剩 FAILED 的历史记录(或什么都没有):允许重新上传。

    return central_state(version, fetch=fetch, wait=wait_visible, interval=interval,
                         sleep=sleep, clock=clock)


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
        state, detail = central_deployment_state(deployment_id, fetch=fetch, auth=_portal_auth(auth))
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
    recover = subparsers.add_parser("central-recover", help="重跑判据:决定还需要不需要上传")
    recover.add_argument("--name", required=True)
    recover.add_argument("--version", required=True)
    recover.add_argument("--deployment-id", default="")
    recover.add_argument("--namespace", default=CENTRAL_NAMESPACE)
    recover.add_argument("--wait-timeout", type=float, default=1800.0)
    recover.add_argument("--wait-visible", type=float, default=300.0)
    recover.add_argument("--interval", type=float, default=20.0)
    find = subparsers.add_parser("central-find", help="按 deploymentName 找回已有部署")
    find.add_argument("--name", required=True)
    find.add_argument("--namespace", default=CENTRAL_NAMESPACE)
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
        if arguments.command == "central-recover":
            # 整个重跑判据是一段被测代码:`skip=` 行给工作流,说明走 stderr。
            state, detail = central_recover(
                arguments.name, arguments.version,
                deployment_id=(arguments.deployment_id or "").strip() or None,
                namespace=arguments.namespace, wait_timeout=arguments.wait_timeout,
                wait_visible=arguments.wait_visible, interval=arguments.interval)
            sys.stdout.write("skip=%s\n" % ("true" if state == PUBLISHED else "false"))
            sys.stderr.write("release-probe: %s\n" % detail)
            return 0
        if arguments.command == "central-find":
            # 0 = 找到唯一一个(打印 id)/ 3 = 确实没有 / 1 = 有歧义或查不出来(关闭失败)
            matches = central_deployments(name=arguments.name,
                                          namespace=arguments.namespace)
            if not matches:
                sys.stderr.write("release-probe: Portal 上没有名为 %s 的部署\n"
                                 % arguments.name)
                return 3
            if len(matches) > 1:
                sys.stderr.write("release-probe: 名为 %s 的部署有 %d 个,无法判断该续哪一个: %r\n"
                                 % (arguments.name, len(matches),
                                    [item["id"] for item in matches]))
                return 1
            sys.stdout.write(matches[0]["id"] + "\n")
            sys.stderr.write("release-probe: 找到部署 %s(状态 %s)\n"
                             % (matches[0]["id"], matches[0]["state"]))
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
