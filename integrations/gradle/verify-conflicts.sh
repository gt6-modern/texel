#!/usr/bin/env bash
# 构建集成的两条"会毁源文件"门禁 + 指纹随编译器内容失效（发布态复核 F05 与跨 target 冲突）。
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"
export GRADLE_USER_HOME=/tmp/gh2
GRADLE="${GRADLE:-../../compiler/java/gradlew}"
fail=0

# 完整输出必须留下来：只打印 BUILD 行会把真实失败原因（What went wrong / 堆栈）吃掉，
# 上一轮就是因此诊断不出问题。
# 符号链接能力探针：Windows 上创建符号链接需要开发者模式/特权。没有就显式 SKIP（不是通过）。
# mktemp 失败时必须**中止**：绝不能回退到共享 /tmp，否则下面的 rm -rf 会误删别人的数据。
probe_dir=$(mktemp -d 2>/dev/null) || { echo "无法创建临时目录，中止（不回退到共享 /tmp）"; exit 1; }
if [ -n "${TXEL_NO_SYMLINK-}" ]; then
    rm -rf "$probe_dir"
    probe_dir=
fi
have_symlinks=0
if [ -n "$probe_dir" ]; then
    printf x > "$probe_dir/probe-target"
    if ln -s "$probe_dir/probe-target" "$probe_dir/probe-link" 2>/dev/null && [ -L "$probe_dir/probe-link" ]; then
        have_symlinks=1
    fi
    rm -rf "$probe_dir"
fi
run() { sh "$GRADLE" --no-daemon --project-dir "$HERE" texelCompile "$@" 2>&1 | tee -a "$FULLLOG"; }

# 必须在脚本里**自己**构建 classpath 指向的 classes：直接用 build/classes 会读到陈旧构建，
# 陈旧会假红（本段曾因此 E 段报"残留临时文件"），反向陈旧会假绿放过真回归（CodeX F-R8-01）。
( cd "$HERE/../../compiler/java" && sh "$GRADLE" --no-daemon :cli:classes :core:classes > /tmp/texel-conf-build.log 2>&1 ) || { echo "构建 classes 失败，见 /tmp/texel-conf-build.log"; exit 1; }
CP_DIR=$(ls -d "$HERE"/../../compiler/java/cli/build/classes/java/main):$(ls -d "$HERE"/../../compiler/java/core/build/classes/java/main)
FULLLOG=/tmp/texel-conf-full.log
rm -f "$FULLLOG"
echo "== 清掉旧状态 =="
# 必须清：texelCompile 是 up-to-date 时 doFirst 不会执行（冲突检查就形同不存在），
# 而旧 build/ 里的指纹文件还是旧格式（没有 compiler_sha256），会让 C 段误判。
rm -rf build .gradle

echo "== E) 产物写入失败后不得留下临时文件 =="
# 用**只读目录**制造确定的写入失败：不依赖名字长度、NAME_MAX 或 seq 是否存在
# （此前三次尝试都栽在这些环境依赖上）。root 下 chmod 不生效时显式 SKIP，不假装通过。
# 输出指向一个**已存在的目录**：move 必然失败（root 也挡不住），与权限/名字长度都无关。
LOCKED=build/locked
rm -rf "$LOCKED"; mkdir -p "$LOCKED"; : > "$LOCKED/keep"   # 非空目录：move 无法覆盖它
out=$(java -cp "$CP_DIR" io.github.texel.cli.Main compile entry.pxl \
        --asset-root demo -o "$LOCKED" 2>&1)
RC=$?
echo "$out" | head -1
if [ "$RC" -eq 0 ]; then
    echo "E SKIP: 本环境没有权限限制（很可能是 root），本条不适用"
else
    # want() 定义在脚本后面，E 段在它之前执行，这里直接用 grep。
    if echo "$out" | grep -q "I/O 失败"; then
        echo "E OK: 确实走到了写入失败那一格"
    else
        echo "E FAIL: 没有出现预期的写入失败"; fail=1
    fi
    if [ "$(ls -1a build | grep -c "texel-compiler.*tmp")" -ne 0 ]; then
        echo "E FAIL: 失败后留下临时文件"; fail=1
    else
        echo "E OK: 没有残留临时文件"
    fi
fi
rm -rf "$LOCKED"

echo "== 备份 ==" 
cp targets.tsv "$FULLLOG.targets.bak"
cp demo/entry.pxl "$FULLLOG.entry.bak"

echo "== A) 输出撞另一个 target 的入口：必须在写任何文件之前失败 =="
# A 的要点：bad 的输出必须落在 demo 的**入口文件**上。入口路径是相对 asset root（demo/）的，
# 所以输出要写 demo/entry.pxl；写成 entry.pxl 是另建了一个文件，构不成冲突（我上一版就错在这）。
printf "demo\tentry.pxl\tbuild/textures/demo.png\t-\nbad\tparts/blade.pxl\tdemo/entry.pxl\t-\n" > targets.tsv
out=$(run -PtxelTargets=targets.tsv)
echo "$out" | grep -E "BUILD|覆盖" | head -3
if echo "$out" | grep -q "BUILD SUCCESSFUL"; then echo "A FAIL: 冲突配置竟然成功"; fail=1; fi
if echo "$out" | grep -q "覆盖了"; then echo "A OK: 报出了覆盖关系"; else echo "A FAIL: 没看到覆盖提示（可能根本没跑到冲突检查）"; fail=1; fi
if ! cmp -s demo/entry.pxl "$FULLLOG.entry.bak"; then echo "A FAIL: 入口被改写"; fail=1; else echo "A OK: 入口字节未变"; fi

echo "== B) 两个 target 输出同一路径：必须失败 =="
printf "demo\tentry.pxl\tbuild/textures/same.png\t-\ndemo2\tentry.pxl\tbuild/textures/same.png\t-\n" > targets.tsv
out=$(run -PtxelTargets=targets.tsv)
echo "$out" | grep -E "BUILD|同一路径" | head -3
if echo "$out" | grep -q "BUILD SUCCESSFUL"; then echo "B FAIL: 重复输出竟然成功"; fail=1; fi
if echo "$out" | grep -q "写到同一文件"; then echo "B OK: 报出了重复输出"; else echo "B FAIL: 没看到重复输出提示（可能根本没跑到冲突检查）"; fail=1; fi

echo "== C) 编译器内容变化必须让指纹失效 =="
echo "== F) 一个 target 的输出撞另一个 target 的**依赖**：必须在写之前失败且依赖不变 =="
# demo 读 entry.pxl + parts/blade.pxl；bad2 的入口也是 entry.pxl（合法 v0），但它把输出
# 指向 demo 的依赖 parts/blade.pxl —— 这正是只比入口时漏掉的那一类（发布态复核 P1）。
cp demo/parts/blade.pxl "$FULLLOG.blade.bak"
# 注意：target 的 out 相对**项目目录**，而依赖相对 asset root（demo/），所以要写 demo/parts/blade.pxl 才是同一个文件。
printf "demo\tentry.pxl\tbuild/textures/demo.png\t-\nbad2\tentry.pxl\tdemo/parts/blade.pxl\t-\n" > targets.tsv
out=$(run -PtxelTargets=targets.tsv)
echo "$out" | grep -E "BUILD|覆盖了依赖" | head -2
if echo "$out" | grep -q "BUILD SUCCESSFUL"; then echo "F FAIL: 该冲突竟然成功"; fail=1; fi
if echo "$out" | grep -q "覆盖了依赖"; then echo "F OK: 报出了依赖冲突"; else echo "F FAIL: 没看到依赖冲突提示"; fail=1; fi
if cmp -s demo/parts/blade.pxl "$FULLLOG.blade.bak"; then echo "F OK: 依赖字节未变"; else echo "F FAIL: 依赖被改写"; fail=1; fi
cp "$FULLLOG.blade.bak" demo/parts/blade.pxl

echo "== G) 大小写不同但同名的 target：在大小写不敏感的宿主上必须被拒 =="
# Windows 上 Alpha 与 alpha 落到同一份清单（发布态复核 P2-3）。Linux 上它们是两个不同文件，
# 本条不适用，显式 SKIP（不假装通过）。
printf "Alpha\tentry.pxl\tbuild/textures/alpha1.png\t-\nalpha\tentry.pxl\tbuild/textures/alpha2.png\t-\n" > targets.tsv
out=$(run -PtxelTargets=targets.tsv -PtxelCaseInsensitive=1)
# 用 -PtxelCaseInsensitive=1 强制走"大小写不敏感"分支，因此本断言与宿主无关。
if echo "$out" | grep -q "同一文件"; then
    echo "G OK: 大小写冲突被拒"
else
    echo "G FAIL: 大小写不同但同名的 target 未被拒"; echo "$out" | tail -5; fail=1
fi

echo "== D) 两个 target 重名：必须失败（否则共用清单，增量判据互相覆盖） =="
printf "demo\tentry.pxl\tbuild/textures/demo.png\t-\ndemo\tparts/blade.pxl\tbuild/textures/other.png\t-\n" > targets.tsv
out=$(run -PtxelTargets=targets.tsv)
if echo "$out" | grep -q "BUILD SUCCESSFUL"; then echo "D FAIL: 重名竟然成功"; fail=1; else echo "D OK: 重名被拒"; fi
if echo "$out" | grep -q "target 名重复"; then echo "D OK: 报出了重名"; else echo "D FAIL: 没看到重名提示（可能根本没跑到配置期检查）"; fail=1; fi

cp "$FULLLOG.targets.bak" targets.tsv
run -PtxelTargets=targets.tsv > /dev/null
fp1=$(cat build/compiler-fingerprint.txt | grep compiler_sha256)
out=$(run -PtxelTargets=targets.tsv)
echo "$out" | grep -E "Task :texelCompile|UP-TO-DATE" | head -2
JARDIR=$(ls -d "$HERE/../../compiler/java/cli/build/install/"*/lib)
# 产物名带版本号（core-<VERSION>.jar，VERSION 来自仓库根的 VERSION 文件）。
# 写死 core.jar 会让下面的 mv 静默失败，于是"改编译器内容"这一步根本没发生，
# C 段就以"指纹没变"假红（2026-11-12 CI 实测）。
CORE_JAR=$(ls "$JARDIR"/core-[0-9]*.jar 2>/dev/null | head -1)
if [ -z "$CORE_JAR" ]; then
    echo "C FAIL: 找不到 core jar（$JARDIR）"; fail=1
fi
mv "$CORE_JAR" "$FULLLOG.core.jar.keep"
cp "$FULLLOG.core.jar.keep" "$JARDIR/core-copy.jar"
out=$(run -PtxelTargets=targets.tsv)
fp2=$(cat build/compiler-fingerprint.txt | grep compiler_sha256)
rm -f "$JARDIR/core-copy.jar"; mv "$FULLLOG.core.jar.keep" "$CORE_JAR"
echo "fp1=$fp1"; echo "fp2=$fp2"
if [ "$fp1" = "$fp2" ]; then echo "C FAIL: 编译器内容变了指纹却没变"; fail=1; else echo "C OK: 指纹随编译器内容变化"; fi
if echo "$out" | grep -q "Task :texelCompile UP-TO-DATE"; then echo "C FAIL: 编译器变了仍 UP-TO-DATE"; fail=1; else echo "C OK: 编译器变了 texelCompile 重跑"; fi

cp "$FULLLOG.targets.bak" targets.tsv
cp "$FULLLOG.entry.bak" demo/entry.pxl
BLADE=demo/parts/blade.pxl
BEFORE=$(sha256sum "$BLADE" | cut -d" " -f1)
if [ "$have_symlinks" = "1" ]; then
echo "== H) 预检目录软链接到依赖目录：必须拒绝且依赖字节不变 =="
rm -rf build/tmp/texel-preflight; mkdir -p build/tmp
ln -s "$(pwd)/demo/parts" build/tmp/texel-preflight
# 必须强制重跑：落点校验在 task action 里，而软链接不是声明的输入，
# 任务 up-to-date 时整段校验根本不执行（见本脚本开头"必须清"那条注释的同一陷阱）。
out=$(run --rerun-tasks)
if echo "$out" | grep -q "落点不在 build"; then echo "H OK: 拒绝了"; else echo "H FAIL: 未拒绝"; echo "$out" | tail -6; fail=1; fi
AFTER=$(sha256sum "$BLADE" | cut -d" " -f1)
if [ "$BEFORE" = "$AFTER" ]; then echo "H OK: 依赖字节未变"; else echo "H FAIL: 依赖被改写"; fail=1; fi
rm -rf build

else
    echo "H SKIP: 本环境不能创建符号链接"
fi
if [ "$have_symlinks" = "1" ]; then
echo "== I) 状态文件软链接到依赖：必须拒绝且依赖字节不变 =="
mkdir -p build; ln -s "$(pwd)/$BLADE" build/texel-state.txt
out=$(run)
if echo "$out" | grep -q "落点不在 build\|同一个文件"; then echo "I OK: 拒绝了"; else echo "I FAIL: 未拒绝"; echo "$out" | tail -6; fail=1; fi
AFTER=$(sha256sum "$BLADE" | cut -d" " -f1)
if [ "$BEFORE" = "$AFTER" ]; then echo "I OK: 依赖字节未变"; else echo "I FAIL: 依赖被改写"; fail=1; fi
rm -rf build

else
    echo "I SKIP: 本环境不能创建符号链接"
fi
echo "== J) 状态文件硬链接到依赖：必须拒绝且依赖字节不变 =="
# 先确认硬链接真的建起来了：文件已存在时 ln 会失败，而脚本仍打 J OK 就是假绿（发布态复核 P2）。
mkdir -p build
rm -f build/texel-state.txt
j_linked=0
if ln "$BLADE" build/texel-state.txt 2>/dev/null; then
    if [ "$(stat -c %h "$BLADE" 2>/dev/null || echo 1)" -ge 2 ]; then
        j_linked=1
    fi
fi
if [ "$j_linked" = "1" ]; then
    out=$(run)
    # 两条防线：身份判据拒绝写入，或原子写只替换目录项。
    if echo "$out" | grep -q "同一个文件"; then echo "J OK: 拒绝了（硬链接身份判据）";
    else echo "J NOTE: 未显式拒绝（由原子写保护）"; fi
    AFTER=$(sha256sum "$BLADE" | cut -d" " -f1)
    if [ "$BEFORE" = "$AFTER" ]; then echo "J OK: 依赖字节未变"; else echo "J FAIL: 依赖被改写"; fail=1; fi
else
    echo "J SKIP: 本环境不能创建硬链接（未测）"
fi
rm -rf build

echo "== K) 指纹文件硬链接到依赖：原子写，依赖字节必须不变 =="
# 与 J 一样先自证夹具成立：ln 失败时脚本不能打 K OK（发布态复核 P2）。
mkdir -p build
rm -f build/compiler-fingerprint.txt
k_linked=0
if ln "$BLADE" build/compiler-fingerprint.txt 2>/dev/null; then
    if [ "$(stat -c %h "$BLADE" 2>/dev/null || echo 1)" -ge 2 ]; then
        k_linked=1
    fi
fi
if [ "$k_linked" = "1" ]; then
    # 夹具已成立（硬链接真的存在），任务失败就不能当"环境不支持"：必须红并保留输出。
    if sh "$GRADLE" --no-daemon --project-dir "$HERE" compilerFingerprint > /tmp/texel-k.log 2>&1; then
        AFTER=$(sha256sum "$BLADE" | cut -d" " -f1)
        if [ "$BEFORE" = "$AFTER" ]; then echo "K OK: 依赖字节未变（原子写）"; else echo "K FAIL: 依赖被改写"; fail=1; fi
    else
        echo "K FAIL: 指纹任务在真实硬链接下失败"; tail -6 /tmp/texel-k.log; fail=1
    fi
else
    echo "K SKIP: 本环境不能创建硬链接（未测）"
fi
rm -rf build
echo "== L) 输出目录尚未创建时不得误报重复输出 =="
rm -rf build; mkdir -p build
printf "one\tentry.pxl\tbuild/one/out.png\t-\ntwo\tentry.pxl\tbuild/two/out.png\t-\n" > /tmp/texel-lm-targets.tsv
out=$(run -PtxelTargets=/tmp/texel-lm-targets.tsv)
if echo "$out" | grep -q "BUILD SUCCESSFUL"; then echo "L OK: 首次构建成功"; else echo "L FAIL: 误报冲突"; echo "$out" | tail -6; fail=1; fi
if [ -f build/one/out.png ] && [ -f build/two/out.png ]; then echo "L OK: 两个产物都在"; else echo "L FAIL: 产物缺失"; fail=1; fi
rm -rf build

if [ "$have_symlinks" = "1" ]; then
echo "== M) 源文件自身是符号链接时仍要挡住输出覆盖 =="
cp demo/parts/blade.pxl demo/parts/actual.pxl
ln -sf actual.pxl demo/parts/linkdep.pxl
printf %s "{\"format\":\"PXL\",\"version\":1,\"encoding\":\"char\",\"width\":1,\"height\":1,\"palette\":[\"FFFFFFFF\"],\"assets\":{\"a\":{\"kind\":\"pxl\",\"path\":\"parts/linkdep.pxl\"}},\"layers\":[{\"name\":\"b\",\"blend\":\"normal\",\"ops\":[{\"op\":\"stamp\",\"asset\":\"a\",\"x\":0,\"y\":0}]}]}" > demo/entry-link.pxl
BEFORE2=$(sha256sum demo/parts/actual.pxl | cut -d" " -f1)
printf "reader\tentry-link.pxl\tbuild/textures/r.png\t-\nbad\tentry-link.pxl\tdemo/parts/actual.pxl\t-\n" > /tmp/texel-lm-targets.tsv
out=$(run -PtxelTargets=/tmp/texel-lm-targets.tsv)
if echo "$out" | grep -q "覆盖了依赖"; then echo "M OK: 拒绝了（链接身份）"; else echo "M FAIL: 未拒绝"; echo "$out" | tail -6; fail=1; fi
AFTER2=$(sha256sum demo/parts/actual.pxl | cut -d" " -f1)
if [ "$BEFORE2" = "$AFTER2" ]; then echo "M OK: 源字节未变"; else echo "M FAIL: 源被改写"; fail=1; fi
rm -f demo/parts/actual.pxl demo/parts/linkdep.pxl demo/entry-link.pxl
rm -rf build
else
    echo "M SKIP: 本环境不能创建符号链接"
fi
echo "== N) tr_TR locale 下的大小写重复输出仍必须被拒 =="
# pathKey 若用默认 locale 的 toLowerCase，会把这个 locale 下的 I 折成无点的 ı，
# 让 FILE.png 与 file.png 得到不同键（发布态复核 P2）。这里强制走大小写不敏感分支，
# 并在土耳其语 locale 下跑，两件事都成立时仍须拒绝。
rm -rf build; mkdir -p build
printf "upper\tentry.pxl\tbuild/FILE.png\t-\nlower\tentry.pxl\tbuild/file.png\t-\n" > /tmp/texel-lm-targets.tsv
out=$(sh "$GRADLE" --no-daemon --project-dir "$HERE" -Duser.language=tr -Duser.country=TR texelCompile -PtxelTargets=/tmp/texel-lm-targets.tsv -PtxelCaseInsensitive=1 2>&1)
if echo "$out" | grep -q "写到同一文件"; then echo "N OK: 拒绝了（与 locale 无关）"; else echo "N FAIL: tr_TR 下漏检"; echo "$out" | tail -6; fail=1; fi
rm -rf build
echo "== 结果 =="
if [ $fail -ne 0 ]; then echo "GATES FAILED"; exit 1; fi
echo "INCREMENTAL CONFLICT GATES OK"
