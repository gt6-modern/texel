#!/usr/bin/env pwsh
# 变异自检：往实现里注入一处缺陷，确认**对应用例真的会失败**，然后把文件还原。
#
# 为什么要这一步：一整套绿着的测试可能什么都没测到。判据有两种失败方式——
# 实现错了，或者判据本身不咬人。"再跑一次还是绿"区分不了这两者；注入缺陷能。
#
# 每一步都先跑**未变异的基线**并要求它绿：
#   基线红            → [失效]（环境/过滤/语料本身有问题，这一条不构成证据）
#   基线绿 + 变异红    → [逮住]
#   基线绿 + 变异绿    → [漏网]（判据无效）
# 少了基线这一步，"用了一个天然失败的过滤用例"就会被当成"逮住了"——审查实测过。
#
# 用法：  pwsh compiler\java\tools\mutation-check.ps1
# 退出码：全部逮住 → 0；任何一条失效或漏网 → 1。

$ErrorActionPreference = 'Stop'

# 本机用户目录下的 .gradle 对沙箱只读，Gradle 解原生库会失败；仓库约定用
# <repo>/.gradle-home。脚本自己兜底，免得"忘了设环境变量"被误读成"编译不过"。
if (-not $env:GRADLE_USER_HOME) {
    $localHome = Join-Path (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path '.gradle-home'
    if (Test-Path $localHome) { $env:GRADLE_USER_HOME = $localHome }
}
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$core = Join-Path $repo 'compiler/java'
# JAVA_HOME 未设时不能把空串丢给 Test-Path（PowerShell 会因空 Path 直接报错，
# 脚本在回退前就终止）。先判空，再回退到 PATH 上的 java。
# 这里**不写死任何安装路径**：写死会让脚本只在作者那台机器上可用，
# 也会把私有目录结构带进仓库（tests/test_public_release.py 会拦）。
$java = ''
if ($env:JAVA_HOME) {
    $candidate = Join-Path $env:JAVA_HOME 'bin\java.exe'
    if (Test-Path $candidate) { $java = $candidate }
}
if (-not $java) {
    $onPath = Get-Command java.exe -ErrorAction SilentlyContinue
    if ($onPath) { $java = $onPath.Source }
}
if (-not $java) {
    Write-Host '找不到可用的 java（设 JAVA_HOME，或把 JDK 21 的 bin 放进 PATH）'
    exit 1
}

$cpCore = @(
    (Join-Path $core 'core\build\classes\java\main')
    (Join-Path $core 'core\build\classes\java\test')
) -join ';'

function Invoke-Suite {
    param([string]$Expect)
    if ($Expect -eq 'UNIT') {
        $output = (& $java '-Dfile.encoding=UTF-8' -cp $cpCore io.github.texel.core.UnitTestMain 2>&1 | Out-String)
    } else {
        $filter = $Expect.Substring($Expect.IndexOf(':') + 1)
        $output = (& $java '-Dfile.encoding=UTF-8' "-Dtxel.repo=$repo" '-Dtxel.python=python' `
            -cp $cpCore io.github.texel.core.ConformanceMain $filter 2>&1 | Out-String)
    }
    $code = $LASTEXITCODE
    $executed = 0
    if ($output -match 'CHECKS=(\d+)') {
        $executed = [int]$Matches[1]
    } elseif ($output -match 'UNITS=(\d+)') {
        $executed = [int]$Matches[1]
    }
    return [pscustomobject]@{ Exit = $code; Executed = $executed; Output = $output }
}
function Invoke-Compile {
    Push-Location $core
    try {
        $output = (& .\gradlew.bat --offline :core:testClasses --console=plain 2>&1 | Out-String)
        $script:lastCompileOutput = $output
        $script:lastCompileCode = $LASTEXITCODE
        return $LASTEXITCODE
    } finally {
        Pop-Location
    }
}

$mutations = @(
    @{
        Name   = 'blend 用取整后的 ao 当归一分母（§6.3 的上界证明）'
        File   = 'core\src\main\java\io\github\texel\core\Raster.java'
        From   = 'int half = aNum / 2;'
        To     = 'int half = (((aNum + 127) / 255) * 255) / 2;'
        Extra  = @{ From = '+ half) / aNum;'; To = '+ half) / (((aNum + 127) / 255) * 255);' }
        Expect = 'UNIT'
    },
    @{
        Name   = 'mirror 越界源改成"夹到边缘"而不是读透明（§5.6）'
        File   = 'core\src\main\java\io\github\texel\core\Raster.java'
        From   = '                if (sourceX >= 0 && sourceX < width && sourceY >= 0 && sourceY < height) {'
        To     = @'
                if (true) {
                    sourceX = Math.max(0, Math.min(width - 1, sourceX));
                    sourceY = Math.max(0, Math.min(height - 1, sourceY));
'@
        Extra  = $null
        Expect = 'UNIT'
    },
    @{
        Name   = 'token 宽度整除检查被跳过（§3.4 / 阶段 5）'
        File   = 'core\src\main\java\io\github\texel\core\Validator.java'
        From   = 'if (codePoints % tokenWidth != 0) {'
        To     = 'if (false) {'
        Extra  = $null
        Expect = 'CONFORMANCE:neg-grid-row-not-multiple'
    },
    @{
        Name   = '面积校验改成 32 位乘法（§5.0.1 的回绕）'
        File   = 'core\src\main\java\io\github\texel\core\Validator.java'
        From   = 'long area = (long) width * (long) height;'
        To     = 'long area = (long) ((int) width * (int) height);'
        Extra  = $null
        Expect = 'CONFORMANCE:neg-size-area-overflow-32bit'
    },
    @{
        Name   = 'PNG 编码改用 filter 1（§10 要求恒为 0）'
        File   = 'core\src\main\java\io\github\texel\core\PngEncoder.java'
        From   = 'scanlines[destination++] = 0;   // 过滤器：恒为 None'
        To     = 'scanlines[destination++] = 1;   // 故意写错：filter 1'
        Extra  = $null
        Expect = 'CONFORMANCE:syn-1x1-opaque'
    }
)

# 先编译一次：否则第一条基线会跑在"上一轮崩掉留下的变异字节码"上，
# 结论会以 [失效] 的形式丢掉一整轮。
Invoke-Compile | Out-Null

$failures = 0
foreach ($mutation in $mutations) {
    $baseline = Invoke-Suite -Expect $mutation.Expect
    if ($baseline.Exit -ne 0 -or $baseline.Executed -lt 1) {
        Write-Host ("[失效] " + $mutation.Name + " —— 基线不绿(exit=" + $baseline.Exit + ", CHECKS=" + $baseline.Executed + ")，这一条不构成证据")
        $failures++
        continue
    }

    $path = Join-Path $core $mutation.File
    $original = [System.IO.File]::ReadAllText($path)
    if (-not $original.Contains($mutation.From)) {
        Write-Host "[失效] $($mutation.Name) —— 锚点文本不存在，变异无法注入（这条自检已过期）"
        $failures++
        continue
    }
    $mutated = $original.Replace($mutation.From, $mutation.To)
    if ($mutation.Extra) {
        if (-not $mutated.Contains($mutation.Extra.From)) {
            Write-Host "[失效] $($mutation.Name) —— 第二处锚点不存在"
            $failures++
            continue
        }
        $mutated = $mutated.Replace($mutation.Extra.From, $mutation.Extra.To)
    }

    try {
        [System.IO.File]::WriteAllText($path, $mutated)
        if ((Invoke-Compile) -ne 0) {
            Write-Host "[失效] $($mutation.Name) —— 变异后编译不过, exitCode=$($script:lastCompileCode)"; Write-Host ($script:lastCompileOutput -split "`n" | Select-Object -Last 6)
            $failures++
            continue
        }
        $result = Invoke-Suite -Expect $mutation.Expect
        if ($result.Exit -ne 0 -and $result.Executed -ge 1) {
            Write-Host "[逮住] $($mutation.Name)"
        } elseif ($result.Exit -ne 0) {
            Write-Host "[失效] $($mutation.Name) —— 失败了但一条用例都没执行，不算证据"
            $failures++
        } else {
            Write-Host "[漏网] $($mutation.Name) —— 注入缺陷后测试仍然通过，这条判据无效"
            $failures++
        }
    } finally {
        # 还原源码还不够:class 里仍是变异后的字节码。不重编译的话，**下一条**的基线
        # 会拿变异产物跑，于是"基线不绿"——顺序依赖会让结论完全失真。
        [System.IO.File]::WriteAllText($path, $original)
        Invoke-Compile | Out-Null
    }
}

Write-Host ''
if ($failures -eq 0) {
    Write-Host "变异自检:$($mutations.Count) 处缺陷全部被逮住"
    exit 0
}
Write-Host "变异自检失败:$failures / $($mutations.Count) 未通过"
exit 1
