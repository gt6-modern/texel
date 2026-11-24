#!/usr/bin/env python3
"""Cross-check every public release version and capability surface.

每个"发布面"都在 ``SURFACES`` 里登记一次:路径 + 取值正则 + 人读名字。新增一个要跟
``VERSION`` 同步的值时,必须同时加进这张表,否则它就是一个**不在门禁里**的版本面 ——
``texel_compiler.__version__`` 曾经就是这样漏掉的(审查 #22)。

``PYPI_PAGES`` / ``MAVEN_PAGES`` 守的是另一种发布面:注册表上**给人看的那段正文**。同一份
0.1.0 曾经在三个地方落成三种东西 —— PyPI 的 importer 页 3,185 字符、PyPI 的 compiler 页
只有子目录 README 1,164 字符且链着 ``../../README.md``、Maven Central 只有 38 / 43 个字符的
POM description。版本号全对、资产全齐,三页却像三个项目:没有任何门禁读过这些正文。这里把
"正文必须存在、必须够长、必须有约定章节、不许有相对链接"钉住,并把 Maven 侧的 POM 正文一起查。
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def must(pattern, path):
    text = path.read_text(encoding="utf-8")
    match = re.search(pattern, text, re.MULTILINE)
    if not match:
        raise AssertionError("missing release metadata in " + str(path))
    return match.group(1)


#: (人读名字, 相对路径, 取值正则)。顺序按"最该先改的"排,报错时容易定位。
SURFACES = (
    ("importer pyproject", "pyproject.toml", r'^version = "([^"]+)"$'),
    ("compiler pyproject", "compiler/python/pyproject.toml", r'^version = "([^"]+)"$'),
    ("python package __version__", "compiler/python/texel_compiler/__init__.py",
     r'^__version__ = "([^"]+)"$'),
    ("python capability", "compiler/python/texel_compiler/capabilities.py",
     r'^COMPILER_VERSION = "([^"]+)"$'),
    ("java capability", "compiler/java/cli/src/main/java/io/github/texel/cli/Capabilities.java",
     r'compiler_version\\": \\"([^\\]+)\\"'),
)


def observe(root):
    """读出所有发布面的版本值;``capability fixture`` 按 JSON 读,其余按正则。"""
    root = Path(root)
    version = (root / "VERSION").read_text(encoding="ascii").strip()
    if not re.match(r"^0\.[0-9]+\.[0-9]+$", version):
        raise AssertionError("VERSION must be a pre-1.0 semantic version")
    observed = {}
    for name, relative, pattern in SURFACES:
        observed[name] = must(pattern, root / relative)
    fixture = json.loads((root / "spec/conformance/capabilities/pxl-v1.json")
                         .read_text(encoding="utf-8"))
    observed["capability fixture"] = fixture["compiler_version"]
    return version, observed


#: (人读名字, pyproject 相对路径, 必备二级标题, 正文字符下限)。
#:
#: PyPI 把 `readme` 指向的文件原样当 long_description 渲染 —— 注册表上那一页是**哪个文件**
#: 由 pyproject 说了算,所以这里登记 pyproject、路径现解析:写死路径的话,`readme` 一改,
#: 门禁就转去查一个没人发布的文件,而真正上网的那页无人过问。字符下限不是审美:它挡的是
#: "这一页又退化回子目录说明"—— `texel-compiler` 那页曾经是 1,164 字符的 `README.md`,
#: 而 importer 页是 3,185。
PYPI_PAGES = (
    ("importer page", "pyproject.toml",
     ("Components", "Five-minute workflow", "Registries", "Guarantees", "Community and security"),
     2500),
    ("python compiler page", "compiler/python/pyproject.toml",
     ("Components", "Install and use", "Registries", "Guarantees", "Community and security"),
     2500),
)

#: Maven 侧没有 `readme` 字段可依:Central 只渲染 POM,模块 README 是给人看的长文,路径直接登记。
MAVEN_PAGES = (
    ("java module page", "compiler/java/README.md",
     ("Modules and artifacts", "Build", "Registries", "Guarantees", "Community and security"),
     2500),
)

#: Maven Central **不渲染 README**:artifact 页只显示 POM 的 name、description、url、
#: licenses、developers 与 scm。所以这两段写在 Gradle 里,是 Maven 侧唯一可查的正文。
JAVA_BUILD = "compiler/java/build.gradle"
JAVA_SETTINGS = "compiler/java/settings.gradle"
POM_DESCRIPTION_MIN_CHARS = 120

#: PyPI 原样渲染 Markdown、不重写相对路径:页面挂在 `/project/<name>/` 下,
#: `../../README.md` 会被解析成 pypi.org 上的 404。发布页正文里只许绝对链接。
#: 行内写法 `](url)` 与引用式定义 `[label]: url` 都要查——后者是绕过门禁的现成路子。
PAGE_LINK = re.compile(r"\]\(([^)\s]+)\)")
PAGE_LINK_DEFINITION = re.compile(r"(?m)^ {0,3}\[[^\]\n]+\]:[ \t]*(\S+)")
PAGE_HEADING = re.compile(r"(?m)^## +(.+?)\s*$")
LINK_SKIP = ("http://", "https://", "mailto:", "#")


def resolve_readme(root, project):
    """`pyproject` 的 `readme` 指向哪个文件,注册表上那一页就是哪个文件。"""
    path = Path(root) / project
    if not path.is_file():
        raise AssertionError("%s is missing, so its registry page is undefined" % project)
    match = re.search(r'(?m)^readme = "([^"]+)"$', path.read_text(encoding="utf-8"))
    if match is None:
        raise AssertionError("%s declares no readme, so its registry page is undefined" % project)
    target = path.parent / match.group(1)
    if not target.is_file():
        raise AssertionError("%s declares readme %r, which does not exist"
                             % (project, match.group(1)))
    return target


def check_page(name, path, sections, minimum):
    """一个发布页正文:存在、够长、章节齐全、无相对链接。"""
    path = Path(path)
    if not path.is_file():
        raise AssertionError("%s is missing: %s" % (name, path.as_posix()))
    text = path.read_text(encoding="utf-8")
    if len(text) < minimum:
        raise AssertionError("%s is %d characters, below the %d-character floor: %s"
                             % (name, len(text), minimum, path.as_posix()))
    targets = PAGE_LINK.findall(text) + PAGE_LINK_DEFINITION.findall(text)
    broken = [target for target in targets if not target.startswith(LINK_SKIP)]
    if broken:
        raise AssertionError("%s has relative links that resolve nowhere on a registry: %r (%s)"
                             % (name, broken, path.as_posix()))
    headings = PAGE_HEADING.findall(text)
    missing = [section for section in sections if section not in headings]
    if missing:
        raise AssertionError("%s is missing sections %r (%s)" % (name, missing, path.as_posix()))


def check_pom_surfaces(root):
    """每个 Gradle 子项目都要有自己的 POM name 与够长的 POM description。"""
    root = Path(root)
    settings = (root / JAVA_SETTINGS).read_text(encoding="utf-8")
    modules = re.findall(r"(?m)^include '([^']+)'$", settings)
    if not modules:
        raise AssertionError("no Gradle subproject declared in " + JAVA_SETTINGS)
    build = (root / JAVA_BUILD).read_text(encoding="utf-8")
    for module in modules:
        quoted = re.escape(module)
        if re.search(r"'" + quoted + r"': '[^']+'", build) is None:
            raise AssertionError("ext.pomNames has no entry for module %r (%s)"
                                 % (module, JAVA_BUILD))
        entry = re.search(r"'" + quoted + r"': \[(.*?)\]\.join\(' '\)", build, re.S)
        if entry is None:
            raise AssertionError("ext.pomDescriptions has no joined entry for module %r (%s)"
                                 % (module, JAVA_BUILD))
        description = " ".join(re.findall(r"'([^']*)'", entry.group(1)))
        if len(description) < POM_DESCRIPTION_MIN_CHARS:
            raise AssertionError(
                "POM description for %r is %d characters, below the %d-character floor: %r"
                % (module, len(description), POM_DESCRIPTION_MIN_CHARS, description))


def check(root):
    """返回版本号;任何发布面不一致就 AssertionError。"""
    root = Path(root)
    version, observed = observe(root)
    wrong = dict((name, value) for name, value in observed.items() if value != version)
    if wrong:
        raise AssertionError("release versions differ from %s: %r" % (version, wrong))
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    if ("## [" + version + "]") not in changelog:
        raise AssertionError("CHANGELOG has no release section for " + version)
    for name, project, sections, minimum in PYPI_PAGES:
        check_page(name, resolve_readme(root, project), sections, minimum)
    for name, relative, sections, minimum in MAVEN_PAGES:
        check_page(name, root / relative, sections, minimum)
    check_pom_surfaces(root)
    return version


def main():
    print("RELEASE_METADATA_OK version=" + check(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
