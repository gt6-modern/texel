# R03 原始驻留内存夹具

来源：2026-11-10 复核包 `20261110-03/r03-original`；原始基线为
`a56d688492175d11b20a4f768246c7630a0b9f76`。本目录保留 19 份 PXL 的原始字节，
`SHA256.json` 与复核包中的摘要清单一致。

`entry.pxl` 是 1×1 画布，引用 `a0.pxl` 至 `a17.pxl`。每个叶子都是独立路径的
2048×2048 画布，只有一条零面积 `rect`（`w=0,h=0,slot=0`）；入口的全部 stamp
都在 `x=2,y=0`，完全落在画布外。没有 fill，没有 PNG 依赖；语义光栅工作为零。
成功编译的像素必须为 `00000000`。

这份夹具用于验证 PXL-v1 §10.2.1 的部署边界，**不进入语言 conformance**。
同时持有 18 份 RGBA 缓冲时仅叶子像素就占约 288 MiB；解析、图层和编码还会占用额外内存。
不得改成全幅 fill：18×2^22 会先超过全局光栅预算，无法测试相同条件。

从仓库根目录先构建：

```text
compiler/java/gradlew.bat -p compiler/java :core:classes :cli:classes
```

Windows PowerShell（从仓库根目录运行）：

```powershell
$fixture = (Resolve-Path tests/fixtures/r03-original).Path
$cp = "compiler/java/core/build/classes/java/main;compiler/java/cli/build/classes/java/main"
java '-Dfile.encoding=UTF-8' -Xmx256m -cp $cp io.github.texel.cli.Main check entry.pxl --asset-root $fixture
$LASTEXITCODE
java '-Dfile.encoding=UTF-8' -Xmx512m -cp $cp io.github.texel.cli.Main check entry.pxl --asset-root $fixture
$LASTEXITCODE
$env:PYTHONPATH = (Resolve-Path compiler/python).Path
python -X utf8 -m texel_compiler check entry.pxl --asset-root $fixture
$LASTEXITCODE
```

Linux/macOS 构建时使用 `compiler/java/gradlew -p compiler/java`，并把 classpath 分隔符换成 `:`。Python 的 256 MiB 地址空间限制仅在支持
`resource.RLIMIT_AS` 的宿主上施加，不能由 Windows 的无限额运行冒充。

判据：

- 内存足够：退出 0，编译成功；
- 宿主内存耗尽：退出 4、stderr 含 `CLI001`，无 Java 堆栈或 Python traceback；
- 不允许用 `PXL011` 或其它 PXL 码将资源不足改判成非法文档；
- 256 MiB 下失败、512 MiB 下成功是原复核环境的观察结果，不是规范对所有实现的要求。
  未来实现若减少驻留，256 MiB 下成功也符合合同。

摘要校验（仓库根目录）：

```python
from pathlib import Path
import hashlib
import json

root = Path("tests/fixtures/r03-original")
expected = json.loads((root / "SHA256.json").read_text(encoding="utf-8"))
assert len(expected) == 19
for name, digest in expected.items():
    assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest, name
```
