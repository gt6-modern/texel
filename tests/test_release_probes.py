"""审查 #20 / #21:注册表发布状态探测的判据。

两个方向的错误都要挡住:

* **#21** 只有 404 才是"还没发布"。`curl -fsS ... else` 把 DNS/TLS/403/429/5xx 全当成
  未发布,发布流程接着做不可幂等的上传,错误归因还指向上传。
* **#20** "已发布"必须按完整资产集确认。PyPI 只看版本端点 200、Central 只看 texel-core
  的 POM,都会把半发布当完成。

这里用注入的 `fetch` 跑真判据,不发任何网络请求。
"""

import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"

if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import release_probes as probes  # noqa: E402

VERSION = "0.2.0"

#: 用模块自己的模板拼出探测 URL:测试里不写字面 URL,免得发布路径被
#: `tests/test_packaging.py` 的"引用了不存在的路径"门禁当成仓库内路径。
SAMPLE_URL = probes.PYPI_JSON.format(project="texel-importer", version=VERSION)


def pypi_body(*filenames):
    return ('{"info": {"version": "%s"}, "urls": [%s]}'
            % (VERSION, ", ".join('{"filename": "%s"}' % name for name in filenames))
            ).encode("utf-8")


def fetcher(responses):
    """按 URL 子串匹配假响应;未匹配即断言失败(探测不许静默跳过 URL)。"""
    def fetch(url):
        for needle, response in responses.items():
            if needle in url:
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError("测试没有为 URL 准备响应: " + url)
    return fetch


class PypiProbeTest(unittest.TestCase):

    def test_404_means_not_published(self):
        state, _detail = probes.pypi_state(
            "texel-importer", VERSION, fetch=fetcher({"pypi.org": (404, b"{}")}))
        self.assertEqual(probes.MISSING, state)

    def test_complete_publication_is_published(self):
        body = pypi_body("texel_importer-0.2.0-py3-none-any.whl",
                         "texel_importer-0.2.0.tar.gz")
        state, _detail = probes.pypi_state(
            "texel-importer", VERSION, fetch=fetcher({"pypi.org": (200, body)}))
        self.assertEqual(probes.PUBLISHED, state)

    def test_wheel_only_publication_is_a_failure(self):
        body = pypi_body("texel_importer-0.2.0-py3-none-any.whl")
        with self.assertRaises(probes.ProbeError) as caught:
            probes.pypi_state("texel-importer", VERSION,
                              fetch=fetcher({"pypi.org": (200, body)}))
        self.assertIn("半发布", str(caught.exception))
        self.assertIn("sdist", str(caught.exception))

    def test_sdist_only_publication_is_a_failure(self):
        body = pypi_body("texel_importer-0.2.0.tar.gz")
        with self.assertRaises(probes.ProbeError):
            probes.pypi_state("texel-importer", VERSION,
                              fetch=fetcher({"pypi.org": (200, body)}))

    def test_infrastructure_errors_are_failures_not_missing(self):
        for status in (401, 403, 429, 500, 502, 503):
            with self.subTest(status=status):
                with self.assertRaises(probes.ProbeError):
                    probes.pypi_state("texel-importer", VERSION,
                                      fetch=fetcher({"pypi.org": (status, b"")}))

    def test_transport_errors_are_failures(self):
        # 真 `fetch`:DNS/TLS/连接失败必须变成 ProbeError,不能变成"没发布"。
        import urllib.error

        def boom(*args, **kwargs):
            raise urllib.error.URLError("temporary failure in name resolution")

        original = probes.urllib.request.urlopen
        probes.urllib.request.urlopen = boom
        try:
            with self.assertRaises(probes.ProbeError):
                probes.fetch(SAMPLE_URL)
        finally:
            probes.urllib.request.urlopen = original

    def test_fetch_returns_404_instead_of_raising(self):
        import urllib.error

        def not_found(*args, **kwargs):
            raise urllib.error.HTTPError("url", 404, "Not Found", {}, io.BytesIO(b"{}"))

        original = probes.urllib.request.urlopen
        probes.urllib.request.urlopen = not_found
        try:
            status, body = probes.fetch(SAMPLE_URL)
        finally:
            probes.urllib.request.urlopen = original
        self.assertEqual(404, status)
        self.assertEqual(b"{}", body)

    def test_garbage_body_is_a_failure(self):
        with self.assertRaises(probes.ProbeError):
            probes.pypi_state("texel-importer", VERSION,
                              fetch=fetcher({"pypi.org": (200, b"<html>")}))


class CentralProbeTest(unittest.TestCase):

    def both(self, core, cli):
        return fetcher({"texel-core": core, "texel-cli": cli})

    def test_all_artifacts_present_is_published(self):
        state, _detail = probes.central_state(
            VERSION, fetch=self.both((200, b"<pom/>"), (200, b"<pom/>")))
        self.assertEqual(probes.PUBLISHED, state)

    def test_all_artifacts_absent_means_not_published(self):
        state, _detail = probes.central_state(
            VERSION, fetch=self.both((404, b""), (404, b"")))
        self.assertEqual(probes.MISSING, state)

    def test_missing_main_jar_is_a_partial_publication(self):
        """POM 全在、主 JAR 缺一个，也是半发布：只看 POM 会跳过上传，版本永久缺件。"""
        def fetch(url):
            return (404, b"") if ".jar" in url else (200, b"<pom/>")

        with self.assertRaises(probes.ProbeError) as caught:
            probes.central_state(VERSION, fetch=fetch)
        detail = str(caught.exception)
        self.assertIn("半发布", detail)
        self.assertIn("主 JAR", detail)

    def test_every_artifact_needs_both_files(self):
        def fetch(url):
            return (200, b"<x/>")

        state, _detail = probes.central_state(VERSION, fetch=fetch)
        self.assertEqual(probes.PUBLISHED, state)

    def test_partial_publication_is_a_failure(self):
        # 审查 #20 的原始复现:texel-core 的 POM 200,texel-cli 的 POM 404。
        with self.assertRaises(probes.ProbeError) as caught:
            probes.central_state(VERSION, fetch=self.both((200, b"<pom/>"), (404, b"")))
        self.assertIn("半发布", str(caught.exception))
        self.assertIn("texel-cli", str(caught.exception))

    def test_any_other_status_is_a_failure(self):
        for status in (403, 429, 500):
            with self.subTest(status=status):
                with self.assertRaises(probes.ProbeError):
                    probes.central_state(
                        VERSION, fetch=self.both((200, b"<pom/>"), (status, b"")))


class CentralDeploymentStateTest(unittest.TestCase):
    """Portal 的部署状态机:PENDING/VALIDATING/PUBLISHING → PUBLISHED,或 → FAILED。

    上传 201 只代表"接受了 bundle";状态没到 PUBLISHED 就不算发布完成（审查 P1）。
    """

    def state(self, payload, status=200):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        return probes.central_deployment_state(
            "abc-123", fetch=lambda url, method="GET", auth=None: (status, body))

    def test_published(self):
        self.assertEqual(probes.PUBLISHED,
                         self.state({"deploymentId": "abc-123", "deploymentState": "PUBLISHED"})[0])

    def test_every_in_flight_state_is_in_progress(self):
        for value in probes.CENTRAL_PENDING_STATES:
            with self.subTest(state=value):
                self.assertEqual(probes.IN_PROGRESS,
                                 self.state({"deploymentState": value})[0])

    def test_failed_is_reported_not_hidden(self):
        state, detail = self.state({"deploymentState": "FAILED",
                                    "errors": ["bad signature"]})
        self.assertEqual(probes.FAILED, state)
        self.assertIn("bad signature", detail)

    def test_unknown_state_and_http_errors_are_probe_errors(self):
        for payload, status in (({"deploymentState": "WAT"}, 200),
                                ({"deploymentState": ""}, 200),
                                (b"", 404),
                                (b"", 500)):
            with self.subTest(payload=payload, status=status):
                with self.assertRaises(probes.ProbeError):
                    self.state(payload, status=status)


class CentralWaitTest(unittest.TestCase):
    """等部署到 PUBLISHED:进行中要等,失败/超时/查不出都必须失败。"""

    def run_wait(self, states, timeout=60.0, interval=0.0):
        remaining = list(states)
        sleeps = []

        def fetch(url, method="GET", auth=None):
            value = remaining.pop(0) if len(remaining) > 1 else remaining[0]
            if isinstance(value, Exception):
                raise value
            return (200, json.dumps({"deploymentState": value}).encode("utf-8"))

        clock = iter(range(0, 10000))
        state, _detail = probes.central_wait("abc-123", timeout=timeout, interval=interval,
                                             fetch=fetch, sleep=sleeps.append,
                                             clock=lambda: next(clock))
        return state, sleeps

    def test_waits_through_in_flight_states(self):
        state, sleeps = self.run_wait(["PENDING", "VALIDATING", "PUBLISHING", "PUBLISHED"])
        self.assertEqual(probes.PUBLISHED, state)
        self.assertEqual(3, len(sleeps), "三个进行中状态各等一次")

    def test_failed_deployment_fails_the_wait(self):
        with self.assertRaises(probes.ProbeError) as caught:
            self.run_wait(["FAILED"])
        self.assertIn("FAILED", str(caught.exception))

    def test_timeout_is_a_failure_not_a_success(self):
        with self.assertRaises(probes.ProbeError) as caught:
            self.run_wait(["PUBLISHING"], timeout=2.0)
        self.assertIn("PUBLISHED", str(caught.exception))


class CentralVisibilityWaitTest(unittest.TestCase):
    """全 404 时给一点时间再判 MISSING:刚上传的部署在 repo1 上还看不见。"""

    def test_becomes_visible_within_the_window(self):
        calls = {"n": 0}

        def fetch(url, method="GET", auth=None):
            calls["n"] += 1
            # 前两轮四个文件全 404,之后全部 200
            return (404, b"") if calls["n"] <= 4 else (200, b"<x/>")

        state, _detail = probes.central_state(
            "0.2.0", fetch=fetch, wait=60.0, interval=0.0, sleep=lambda _s: None,
            clock=iter(range(0, 1000)).__next__)
        self.assertEqual(probes.PUBLISHED, state)

    def test_stays_missing_after_the_window(self):
        state, detail = probes.central_state(
            "0.2.0", fetch=lambda url, method="GET", auth=None: (404, b""),
            wait=2.0, interval=0.0, sleep=lambda _s: None,
            clock=iter(range(0, 1000)).__next__)
        self.assertEqual(probes.MISSING, state)
        self.assertIn("等待", detail)


class ReleaseDocParityTest(unittest.TestCase):
    """重跑说明必须与探测实现同一条规则。

    文档写旧规则（"只探 texel-core 的 POM"）会把排障引向错误的结论：漏掉"另一个
    artifact 不在"和"主 JAR 不在"这两种半发布（审查）。
    """

    DOC = ROOT / "docs" / "releasing.md"

    def section(self):
        text = self.DOC.read_text(encoding="utf-8")
        start = text.index("## 6. Re-running a failed release")
        rest = text[start + 1:]
        end = rest.find("## ")
        return rest if end < 0 else rest[:end]

    def test_central_bullet_names_every_artifact_and_file(self):
        section = self.section()
        for artifact in probes.CENTRAL_ARTIFACTS:
            self.assertIn(artifact, section, "文档没提 Central 上的 " + artifact)
        for label, pattern in probes.CENTRAL_FILES:
            suffix = pattern.split("{version}", 1)[1]
            self.assertIn(suffix, section, "文档没提 %s（%s）" % (label, suffix))

    def test_central_bullet_describes_the_deployment_wait(self):
        section = self.section()
        for needle in ("deployment", "PUBLISHED", "FAILED", "artifact"):
            self.assertIn(needle, section, "重跑说明没提 " + needle)

    def test_pypi_bullet_names_both_distributions(self):
        section = self.section()
        for label, _suffix in probes.PYPI_KINDS:
            self.assertIn(label, section, "文档没提 PyPI 的 " + label)


class WorkflowWiringTest(unittest.TestCase):
    """判据真的接在工作流里了吗?—— 只测函数不测接线,门禁可以绿而流程照旧坏。"""

    def setUp(self):
        self.text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    def test_curl_probes_are_gone(self):
        # `curl -fsS ... else` 正是 #21 的病根:它把故障和 404 混成一个分支。
        self.assertNotIn("if curl -fsS", self.text)
        self.assertNotIn("curl -fsS \"https://pypi.org", self.text)

    def test_every_registry_probe_uses_the_tool(self):
        # 只数**命令行调用**,注释里提到文件名不算接线。
        # PyPI×2 + Central(repo1)×1 + central-status×1 + central-wait×2
        self.assertEqual(6, self.text.count("python tools/release_probes.py"),
                         "每一处注册表交互都必须走 release_probes.py")

    def test_central_publish_waits_for_published(self):
        # 上传接口 201 只代表"接受了 bundle":必须等部署到 PUBLISHED 才算发布完成，
        # 否则 github-release 会在 Central 最终 FAILED 之前就发布（审查 P1）。
        self.assertIn("central-wait --deployment-id", self.text)
        self.assertIn("deployment-id.txt", self.text, "deployment id 必须留存给重跑")
        self.assertIn("central-status --deployment-id", self.text,
                      "重跑必须先问已有部署的状态")

    def test_asset_set_is_verified_before_irreversible_steps(self):
        gate_at = self.text.find("tools/check_release_assets.py")
        self.assertGreater(gate_at, 0, "发布资产验证没有接进工作流")
        # 动作是**按 SHA 钉住**的（`uses: actions/upload-artifact@ea16… # v4`），
        # 所以按"动作名 + @"匹配，不按带版本号的写法匹配。
        for irreversible in ("attest-build-provenance@", "upload-artifact@"):
            position = self.text.find(irreversible)
            self.assertGreater(position, gate_at,
                               "资产验证必须排在 %s 之前" % irreversible)


class ProbeCommandLineTest(unittest.TestCase):
    """工作流消费的是 stdout 的 `skip=` 行;错误必须非零退出且不写 stdout。"""

    def run_main(self, argv, fetch=None):
        original = probes.fetch
        if fetch is not None:
            probes.fetch = fetch
        try:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = probes.main(argv)
        finally:
            probes.fetch = original
        return code, out.getvalue(), err.getvalue()

    def test_published_writes_skip_true(self):
        body = pypi_body("t-0.2.0-py3-none-any.whl", "t-0.2.0.tar.gz")
        code, out, _err = self.run_main(
            ["pypi", "--project", "texel-importer", "--version", VERSION],
            fetch=fetcher({"pypi.org": (200, body)}))
        self.assertEqual(0, code)
        self.assertEqual("skip=true\n", out)

    def test_missing_writes_skip_false(self):
        code, out, _err = self.run_main(
            ["pypi", "--project", "texel-importer", "--version", VERSION],
            fetch=fetcher({"pypi.org": (404, b"{}")}))
        self.assertEqual(0, code)
        self.assertEqual("skip=false\n", out)

    def test_failure_exits_nonzero_without_stdout(self):
        code, out, err = self.run_main(
            ["pypi", "--project", "texel-importer", "--version", VERSION],
            fetch=fetcher({"pypi.org": (503, b"")}))
        self.assertEqual(1, code)
        self.assertEqual("", out, "失败时绝不能写 skip= 行")
        self.assertIn("release-probe:", err)


if __name__ == "__main__":
    unittest.main()
