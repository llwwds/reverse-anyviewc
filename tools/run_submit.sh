#!/usr/bin/env bash
# 一键启动批量提交：逐题「打开 → 停顿 → 保存 → 编译 → 判题」，题与题之间 60~300 秒抖动。
#
# 用法:
#   ./tools/run_submit.sh --plan          # 只看计划与预估时长，不跑
#   ./tools/run_submit.sh                 # 前台跑（Ctrl-C 可停，进度已落台账，可续跑）
#   ./tools/run_submit.sh --daemon        # 后台跑（在自己的终端里用；某些沙箱环境会回收子进程）
#   ./tools/run_submit.sh --status        # 是否在跑 + 台账进度 + 日志尾巴
#   ./tools/run_submit.sh --stop          # 停掉在跑的实例
#
# 可选参数:
#   --min 60            题间停顿下限（秒）
#   --max 300           题间停顿上限（秒）
#   --limit 5           只跑前 N 题（试跑用）
#   --eid 11875442,11875559   只跑指定题（逗号分隔可多选）
#   --include-existing  连模板里已有历史代码的题也做（默认跳过）
#
# 单实例靠 local-data/.drive.lock 的内核文件锁保证：进程怎么死锁都会自动释放，
# 不依赖 PID 文件，也不依赖 ps / pgrep（受限环境里常被禁）。
#
# 环境变量 PY 可指定解释器；不设则自动找能 import rsa 的那个。
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1

MIN_WAIT=60
MAX_WAIT=300
LIMIT=0
EIDS=""
MODE="run"
INCLUDE_EXISTING=0        # 不用数组：bash 3.2 在 set -u 下取空数组长度会报 unbound variable

usage() { sed -n '2,21p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
    case "$1" in
        --plan)   MODE="plan"; shift ;;
        --run)    MODE="run"; shift ;;
        --daemon) MODE="daemon"; shift ;;
        --status) MODE="status"; shift ;;
        --stop)   MODE="stop"; shift ;;
        --min)    MIN_WAIT="${2:?--min 需要一个数字}"; shift 2 ;;
        --max)    MAX_WAIT="${2:?--max 需要一个数字}"; shift 2 ;;
        --limit)  LIMIT="${2:?--limit 需要一个数字}"; shift 2 ;;
        --eid)    EIDS="${2:?--eid 需要题号}"; shift 2 ;;
        --include-existing) INCLUDE_EXISTING=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "未知参数：$1（-h 看用法）" >&2; exit 2 ;;
    esac
done

if [ "$MIN_WAIT" -ge "$MAX_WAIT" ]; then
    echo "参数错误：--min ($MIN_WAIT) 必须小于 --max ($MAX_WAIT)" >&2
    exit 2
fi

LOG_DIR="$REPO/local-data/提交日志"
mkdir -p "$LOG_DIR"

# ---------------------------------------------------------------- 解释器
pick_python() {
    local c
    for c in "${PY:-}" \
             "$REPO/.venv/bin/python" \
             "$HOME/.workbuddy/binaries/python/envs/default/bin/python" \
             "$(command -v python3 2>/dev/null)" \
             "$(command -v python 2>/dev/null)"; do
        [ -n "$c" ] || continue
        [ -x "$c" ] || continue
        if "$c" -c "import rsa" >/dev/null 2>&1; then
            printf '%s' "$c"
            return 0
        fi
    done
    return 1
}

PY="$(pick_python)" || {
    echo "找不到能 import rsa 的 python。装一个隔离环境：" >&2
    echo "  python3 -m venv .venv && .venv/bin/pip install rsa" >&2
    exit 1
}

# ---------------------------------------------------------------- 运行状态
# 试拿一下 exam_drive 的排他锁：拿得到 = 没人在跑，立刻释放；拿不到 = 正在跑。
# 输出：正在跑则打印持锁 PID，否则无输出。
lock_holder() {
    "$PY" - "$REPO" <<'PY'
import fcntl, pathlib, sys
p = pathlib.Path(sys.argv[1]) / "local-data" / ".drive.lock"
if not p.exists():
    raise SystemExit
try:
    f = open(p, "a")
except OSError:
    raise SystemExit
try:
    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    try:
        print(p.read_text().strip() or "?")
    except Exception:
        print("?")
else:
    fcntl.flock(f, fcntl.LOCK_UN)
finally:
    f.close()
PY
}

count_record() {
    "$PY" - "$REPO" <<'PY'
import json, sys, pathlib
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root / "tools"))
import paths
p = paths.record_file()
if not p.exists():
    print("台账：空")
    raise SystemExit
rec = json.loads(p.read_text(encoding="utf-8"))
ok = sum(1 for v in rec.values() if v.get("passed"))
print(f"台账：{len(rec)} 题已尝试，{ok} 题通过，{len(rec) - ok} 题未通过")
PY
}

latest_log() { ls -t "$LOG_DIR"/run_*.log 2>/dev/null | head -1; }

case "$MODE" in
    status)
        holder="$(lock_holder)"
        if [ -n "$holder" ]; then
            echo "状态：运行中（PID ${holder}）"
        else
            echo "状态：未运行"
        fi
        count_record
        log="$(latest_log)"
        if [ -n "$log" ]; then
            echo "日志：$(basename "$log")　最后写入：$(date -r "$log" '+%H:%M:%S' 2>/dev/null)"
            echo "--- 最近 10 行 ---"
            tail -10 "$log"
        fi
        exit 0
        ;;
    stop)
        holder="$(lock_holder)"
        if [ -n "$holder" ] && [ "$holder" != "?" ]; then
            kill "$holder" 2>/dev/null
            for _ in 1 2 3 4 5 6 7 8 9 10; do
                [ -z "$(lock_holder)" ] && break
                sleep 1
            done
            if [ -z "$(lock_holder)" ]; then
                echo "已停止（PID ${holder}）。进度已落台账，重跑会从断点继续。"
            else
                kill -9 "$holder" 2>/dev/null
                echo "已强制停止（PID ${holder}）。"
            fi
        else
            echo "没有在运行的实例。"
        fi
        exit 0
        ;;
esac

# ---------------------------------------------------------------- 启动前检查
holder="$(lock_holder)"
if [ -n "$holder" ]; then
    echo "已有实例在跑（PID ${holder}），不重复启动。" >&2
    echo "  看进度：$0 --status      停掉：$0 --stop" >&2
    exit 1
fi

if [ ! -f "$REPO/local-data/题库/题库.json" ]; then
    echo "缺少 local-data/题库/题库.json，先跑 tools/crawl.py" >&2
    exit 1
fi

echo "解释器：$PY"
echo "数据根：$("$PY" -c 'import sys; sys.path.insert(0,"tools"); import paths; print(paths.data_root())')"

# token 探活：doctor.py 会真打一次只读接口，token 失效这里就能发现，
# 不至于开跑几小时才发现每题都失败。
if ! "$PY" tools/doctor.py 2>&1 | grep -q "结论：可用"; then
    echo "自检未通过，先修环境：" >&2
    "$PY" tools/doctor.py 2>&1 | sed 's/^/  /' >&2
    exit 1
fi
echo "自检：通过"

CMD=("$PY" tools/exam_drive.py --run --min-wait "$MIN_WAIT" --max-wait "$MAX_WAIT")
[ "$LIMIT" -gt 0 ] && CMD+=(--limit "$LIMIT")
[ -n "$EIDS" ] && CMD+=(--eid "$EIDS")
[ "$INCLUDE_EXISTING" -eq 1 ] && CMD+=(--include-existing)

if [ "$MODE" = "plan" ]; then
    PLAN_CMD=("$PY" tools/exam_drive.py --plan --min-wait "$MIN_WAIT" --max-wait "$MAX_WAIT")
    [ "$LIMIT" -gt 0 ] && PLAN_CMD+=(--limit "$LIMIT")
    [ -n "$EIDS" ] && PLAN_CMD+=(--eid "$EIDS")
    exec "${PLAN_CMD[@]}"
fi

LOG="$LOG_DIR/run_$(date +%Y%m%d_%H%M%S).log"
echo "间隔：${MIN_WAIT}~${MAX_WAIT} 秒抖动"
echo "日志：$LOG"

if [ "$MODE" = "daemon" ]; then
    nohup "${CMD[@]}" >>"$LOG" 2>&1 &
    child=$!
    sleep 3
    holder="$(lock_holder)"
    # 注意判非空而不是判退出码：命令替换的退出码来自 python 自身，
    # 即使输出为空也是 0，用 if holder=$(...) 会永远当真。
    if [ -n "$holder" ]; then
        echo "已在后台启动，PID $child"
        echo "  看进度：$0 --status"
        echo "  停掉：  $0 --stop"
    else
        echo "启动失败，日志末尾：" >&2
        tail -20 "$LOG" >&2
        exit 1
    fi
else
    "${CMD[@]}" 2>&1 | tee -a "$LOG"
fi
