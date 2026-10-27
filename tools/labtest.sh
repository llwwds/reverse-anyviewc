#!/usr/bin/env bash
# 本地编译 + 限时运行一个实验工程。
#
#   bash tools/labtest.sh 141451
#
# 退出码：2 = 目录不存在；3 = 编译失败；4 = 运行超时；0 = 正常跑完
# 若目录里有 _stdin.txt，会被当作标准输入喂给程序。
# 编译产物与运行日志都在 /tmp，不污染题库目录。

set -uo pipefail

eid="${1:?用法: bash tools/labtest.sh <eid>}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dir="$root/local-data/实验答案/$eid"

if [ ! -d "$dir" ]; then
    echo "目录不存在: $dir" >&2
    exit 2
fi

cd "$dir" || exit 2
bin="/tmp/labtest_$eid"
log="/tmp/labtest_$eid.log"

if ! clang++ -std=c++11 -w ./*.cpp -o "$bin" 2>"/tmp/labtest_${eid}.err"; then
    echo "=== 编译失败 ==="
    cat "/tmp/labtest_${eid}.err"
    exit 3
fi

if [ -f _stdin.txt ]; then
    "$bin" < _stdin.txt > "$log" 2>&1 &
else
    "$bin" > "$log" 2>&1 &
fi
pid=$!

for _ in $(seq 1 60); do
    sleep 0.1
    kill -0 "$pid" 2>/dev/null || break
done

if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" 2>/dev/null
    wait "$pid" 2>/dev/null
    echo "=== 运行超时（6 秒），疑似死循环 ==="
    cat "$log"
    exit 4
fi

wait "$pid" 2>/dev/null
echo "=== 运行输出 ==="
cat "$log"
