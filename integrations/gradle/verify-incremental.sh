#!/usr/bin/env bash
# 构建集成的增量门禁（PRD §20 阶段 5）：在干净产物上跑一遍，再验"该失效的失效、不该失效的不失效"。
#
# 为什么要脚本而不是手工点：这三条门禁只有在**真的跑两次 gradle**时才成立，
# 而 CI 里必须能用一条命令重复出来。
#
# **所有会改动文件的步骤都在临时副本里做**：早前第 4 步直接往仓库里的
# demo/unrelated.txt 追加一行，跑完就把工作树弄脏了（审查 P3）。现在整份
# build.gradle + settings.gradle + targets.tsv + demo/ 都复制到临时目录下再跑，
# 仓库里的 demo/ 一个字节都不碰；退出时 trap 清掉临时树。
set -euo pipefail

cd "$(dirname "$0")"
SCRIPT_DIR=$(pwd)
REPO=$(cd "$SCRIPT_DIR/../.." && pwd)
GRADLE="$(cd "$REPO/compiler/java" && pwd)/gradlew"

# 前置产物自己构建：上一次 clean 把 cli/build/install 删掉后，本脚本会以
# "找不到 compiler 的 installDist 产物" 在第一步失败（实测踩到）。
( cd "$REPO/compiler/java" && sh "$GRADLE" --no-daemon :cli:installDist > /tmp/texel-verify-install.log 2>&1 ) \
    || { echo "构建 installDist 失败，见 /tmp/texel-verify-install.log"; exit 1; }

want() { grep -q "$1"; }

# 临时根（用 printenv 而不是花括号默认值，脚本里不出现任何需要转义的写法）。
BASE=$(printenv TMPDIR || true)
if [ -z "$BASE" ]; then
    BASE=/tmp
fi
TMPBASE=$(mktemp -d "$BASE/texel-verify-XXXXXXXX")

# ---- 把这一层集成复制到临时目录里跑 -------------------------------------------------
#
# 位置有讲究：build.gradle 里 repoRoot = file('../..')，所以副本必须落在
# 「假仓库根」往下两层，('../..') 才正好指回假根；再把真仓库的 compiler/ 以符号链接
# 挂进假根，:cli:installDist 的产物（compiler/java/cli/build/install）就照旧可见。
FAKE_ROOT="$TMPBASE/root"
PROJECT="$FAKE_ROOT/x/gradle"
mkdir -p "$PROJECT"
cp "$SCRIPT_DIR/build.gradle" "$SCRIPT_DIR/settings.gradle" "$SCRIPT_DIR/targets.tsv" "$PROJECT/"
# targets.tsv 是**生成物**：仓库里那份可能被别的脚本（例如 verify-conflicts 的 L/M 段）改过，
# 直接复制会让本脚本以别人的 target 集合开局（实测踩到：reader/entry-link 的残留）。这里自己写一份。
printf "demo\tentry.pxl\tbuild/textures/demo.png\t-\n" > "$PROJECT/targets.tsv"

cp -R "$SCRIPT_DIR/demo" "$PROJECT/demo"
ln -s "$REPO/compiler" "$FAKE_ROOT/compiler"
trap 'rm -rf "$TMPBASE"' EXIT

gradle_run() { ( cd "$PROJECT" && sh "$GRADLE" --no-daemon "$@" ); }

echo "== 0) 副本位置 =="
echo "$PROJECT（仓库里的 demo/ 不会被改动）"

echo "== 1) 首次编译 =="
out=$(gradle_run texelCompile 2>&1 || true)
echo "$out" | grep -E "BUILD|Task :texelCompile" || true
echo "$out" | want "BUILD SUCCESSFUL" || { echo "首次编译失败"; echo "$out" | tail -25; exit 1; }

echo "== 2) 再跑一次，必须 UP-TO-DATE =="
out=$(gradle_run texelCompile 2>&1)
echo "$out" | want "Task :texelCompile UP-TO-DATE" || { echo "第二次运行没有 UP-TO-DATE"; exit 1; }

echo "== 3) 改真实依赖，必须重跑 =="
sed -i.bak "s/E0A060FF/FFAA00FF/" "$PROJECT/demo/parts/blade.pxl"
rm -f "$PROJECT/demo/parts/blade.pxl.bak"
out=$(gradle_run texelCompile 2>&1)
echo "$out" | want "Task :texelCompile UP-TO-DATE" && { echo "改依赖后仍 UP-TO-DATE"; exit 1; } || true
echo "$out" | want "BUILD SUCCESSFUL" || { echo "改依赖后编译失败"; exit 1; }

echo "== 4) 改无关文件，必须 UP-TO-DATE（只改副本）=="
echo "x" >> "$PROJECT/demo/unrelated.txt"
out=$(gradle_run texelCompile 2>&1)
echo "$out" | want "Task :texelCompile UP-TO-DATE" || { echo "改无关文件后失效了"; exit 1; }

echo "== 4b) 产物被截断后必须重跑 =="
# 必须写**副本**里的产物：脚本自身 cwd 是仓库，写相对路径会碰到仓库里的 build/。
printf 'x' > "$PROJECT/build/textures/demo.png"
out=$(gradle_run texelCompile 2>&1)
if echo "$out" | want "Task :texelCompile UP-TO-DATE"; then
    echo "产物损坏后仍 UP-TO-DATE"; echo "$out" | tail -20; exit 1
fi
echo "产物损坏 -> 重跑 OK"

echo "== 5) 缓存开关不改变产物字节 =="
before=$(sha256sum "$PROJECT/build/textures/demo.png" | cut -d" " -f1)
rm -rf "$PROJECT/build/textures"
gradle_run --build-cache texelCompile > /dev/null 2>&1
after=$(sha256sum "$PROJECT/build/textures/demo.png" | cut -d" " -f1)
[ "$before" = "$after" ] || { echo "缓存开关改变了产物字节"; exit 1; }

echo "==================================================================="
echo "INCREMENTAL GATES OK"
echo "仓库工作树未被本次运行改动（所有写入都在临时副本内）"
