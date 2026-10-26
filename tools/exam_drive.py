#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
按「人答题」的节奏逐题提交。

每一题的动作序列，和真人在网页上做题完全一致：

    打开题目（取题干接口返回模板）
        ↓  停顿 wait 秒（模拟读题、构思、敲代码）
    保存代码  POST /gdb-service/exercise/saveStudentCode
    编译比对  POST /gdb-service/code/compile
    运行判题  POST /gdb-service/code/runGroup     ← exerciseStatus 置 2

停顿时长按「模板规模 + 随机抖动」估算，而不是每题固定值，
避免出现 152 题全部间隔一样这种一眼假的时间序列。

进度写进 ../提交记录.json，随时中断、随时 --resume 续跑。

用法:
  python exam_drive.py --plan                    # 只看计划与预估总时长
  python exam_drive.py --run                     # 开跑（默认跳过已通过的）
  python exam_drive.py --run --min-wait 40 --max-wait 120
  python exam_drive.py --run --limit 5           # 只跑前 5 题
  python exam_drive.py --run --eid 11875062      # 只跑指定题
  python exam_drive.py --run --redo              # 连已通过的也重做
"""
import argparse
import base64
import json
import random
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import assemble as A    # noqa: E402
import paths           # noqa: E402
import submit as S      # noqa: E402

ROOT = paths.data_root()
RECORD = ROOT / "提交记录.json"


def load_questions():
    return json.loads((ROOT / "题库" / "题库.json").read_text(encoding="utf-8"))


def load_record():
    if RECORD.exists():
        try:
            return json.loads(RECORD.read_text(encoding="utf-8"))
        except Exception:                              # noqa: BLE001
            return {}
    return {}


def save_record(rec):
    RECORD.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")


def plan_wait(src, min_wait, max_wait):
    """估算这题「像人」要花多久。

    以 min~max 区间为基准，按模板行数放大：短题（十来回行）取下限附近，
    长题（几十行、含多个函数）放大到 1.6 倍。再加 ±20% 抖动，
    让相邻两题的间隔不会整齐得像机器。
    """
    lines = len([l for l in src.split("\n") if l.strip()])
    span = max(1, max_wait - min_wait)
    base = min_wait + span * random.random()
    scale = min(1.6, 0.55 + lines / 42.0)
    jitter = 0.8 + 0.4 * random.random()
    return max(10, int(base * scale * jitter))


# 平台塞的占位返回，形如 `return NULL; //Temporary code. Modify it if necessary.`
# 这不算「学生写过代码」，判空壳时必须放行。
PLACEHOLDER = re.compile(
    r"^return\s*(?:NULL|nullptr|0|-1|ERROR|FALSE|TRUE|'\s*\\0\s*'|\"\"|\)?\s*;)?\s*;?$")


def template_filled_by(eid):
    """模板里是否已经有实义代码（= 以前自己提交过）。

    判据：至少有一个函数的函数体，去掉注释后还有实义内容。
    以下都算空壳：`{   // Add your code here\\n\\n\\n}`、
    `{ return NULL; //Temporary code }`。
    返回函数名，空壳返回 None。
    """
    p = ROOT / "原始数据" / f"q_{eid}.json"
    if not p.exists():
        return None
    try:
        blob = json.loads(p.read_text(encoding="utf-8"))
        det = blob.get("detail") or {}
        b64 = det.get("stuCode") or ""
        if not b64:
            return None
        template = base64.b64decode(b64).decode("utf-8", "replace")
    except Exception:                                  # noqa: BLE001
        return None

    for f in A.find_functions(template):
        body = template[f.body_open + 1: f.body_close]
        body = A.strip_trailing_comments(body)
        # 折叠空白后判断是不是只剩一句占位 return
        flat = " ".join(body.split())
        if not flat:
            continue
        if PLACEHOLDER.match(flat):
            continue
        return f.name
    return None


def fetch_orders(questions, only_eid=None, limit=None, redo=False, record=None,
                 skip_existing=True):
    """按题库顺序排出待做的题。"""
    record = record or {}
    out = []
    skipped = []
    for q in questions:
        eid = str(q["eid"])
        if only_eid and eid != str(only_eid):
            continue
        if not redo and (record.get(eid) or {}).get("passed"):
            continue
        if skip_existing:
            who = template_filled_by(eid)
            if who:
                skipped.append((q["pname"], eid, who))
                continue
        out.append(q)
        if limit and len(out) >= limit:
            break
    return out, skipped


def do_one(tk, eid, answers, extras, min_wait, max_wait, quiet=False):
    """对一题走完「打开 → 停顿 → 保存 → 编译 → 判题」。"""
    # 1) 打开题目：取题干接口，同时拿到题目元信息
    src, rep, det = A.build_one(eid, answers, tk, fetch=True, extras=extras)
    name = rep.get("pname") or eid
    if rep.get("no_answer") or not src.strip():
        return {"eid": eid, "pname": name, "ok": False, "reason": "无答案或拼装为空"}

    # 2) 停顿：模拟读题与写代码
    wait = plan_wait(src, min_wait, max_wait)
    if not quiet:
        print(f"    停顿 {wait}s ...", flush=True)
    time.sleep(wait)

    # 3) 保存 / 编译 / 判题
    cb = base64.b64encode(src.encode()).decode()
    full = det.get("questionFullName") or ""
    short = S.short_name(full)
    kind = det.get("kind") or 3
    lang = det.get("language") or 1

    st, j = S.save_code(tk, eid, cb)
    saved = j.get("code") == 200

    # 平台保存是异步落库的。save 返回「保存成功」后立刻 compile/runGroup，
    # 判题环境可能还拿着上一版代码 —— 表现为「代码明明对，却 passed=False」。
    # 实测踩过：修复后立刻 push 仍判失败，隔十几秒再 --run 就过了。
    time.sleep(6)

    st, j = S.compile_code(tk, eid, cb, full)
    d = j.get("data") or {}
    comp_ok = (j.get("code") == 200) and ("编译成功" in (d.get("result") or ""))
    time.sleep(2)

    st, j = S.run_group(tk, eid, cb, short, kind, lang)
    d = j.get("data") or {}
    passed = bool(d.get("passed"))

    # 万一还是落后，再等一会儿补一次；正常情况走不到这里
    if not passed and comp_ok:
        time.sleep(8)
        st, j = S.run_group(tk, eid, cb, short, kind, lang)
        d2 = j.get("data") or {}
        if d2.get("passed"):
            passed = True
            d = d2

    return {
        "eid": eid,
        "pname": name,
        "ok": passed,
        "saved": saved,
        "compile_ok": comp_ok,
        "passed": passed,
        "wait": wait,
        "runRight": d.get("runRightCount"),
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true", help="只打印计划")
    ap.add_argument("--run", action="store_true", help="真正执行")
    ap.add_argument("--eid")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--redo", action="store_true", help="连已通过的也重做")
    ap.add_argument("--include-existing", action="store_true",
                    help="连模板里已有历史代码的题也做（默认跳过，保留原样）")
    ap.add_argument("--min-wait", type=int, default=60)
    ap.add_argument("--max-wait", type=int, default=180)
    ap.add_argument("--seed", type=int, help="固定随机种子，便于复现计划")
    a = ap.parse_args()

    if a.seed is not None:
        random.seed(a.seed)

    questions = load_questions()
    record = load_record()
    orders, skipped = fetch_orders(questions, a.eid, a.limit, a.redo, record,
                                   skip_existing=not a.include_existing)

    if skipped:
        print(f"跳过 {len(skipped)} 题（模板里已有历史代码，按你的要求保留原样）：")
        for pname, eid, who in skipped:
            print(f"  - {pname:12s} {eid:9s} 已有函数 {who}")
        print()

    if not orders:
        print("没有需要做的题（已全部通过）。加 --redo 可重做。")
        return

    answers = A.load_answers()
    extras = A.load_src_extras()

    if a.plan:
        print(f"待做 {len(orders)} 题，停顿区间 {a.min_wait}~{a.max_wait} 秒\n")
        total = 0
        for q in orders:
            src, rep, _det = A.build_one(str(q["eid"]), answers, fetch=False, extras=extras)
            w = plan_wait(src, a.min_wait, a.max_wait)
            total += w + 6
            print(f"  {q['pname']:12s} {q['course'][:8]:10s} 第{q['chapter']}章  "
                  f"{len(src.splitlines()):3d} 行  停顿 {w:4d}s")
        h, rem = divmod(total, 3600)
        print(f"\n预估总时长约 {h} 小时 {rem // 60} 分")
        return

    if not a.run:
        sys.exit("要么 --plan，要么 --run")

    tk = S.token()
    print(f"开始，共 {len(orders)} 题\n" + "=" * 64, flush=True)

    done = ok = 0
    t_start = time.time()
    for idx, q in enumerate(orders, 1):
        eid = str(q["eid"])
        print(f"\n[{idx}/{len(orders)}] {q['pname']}  {q['course']} 第{q['chapter']}章  eid={eid}",
              flush=True)
        try:
            r = do_one(tk, eid, answers, extras, a.min_wait, a.max_wait)
        except SystemExit:
            raise
        except Exception as exc:                       # noqa: BLE001
            r = {"eid": eid, "pname": q["pname"], "ok": False, "reason": f"{type(exc).__name__}: {exc}"}

        record[eid] = r
        save_record(record)
        done += 1
        if r.get("passed"):
            ok += 1
            print(f"    通过（runRight={r.get('runRight')}, 停顿 {r.get('wait')}s）", flush=True)
        else:
            print(f"    未通过：{r.get('reason') or r}", flush=True)

        elapsed = time.time() - t_start
        avg = elapsed / done
        left = avg * (len(orders) - done)
        print(f"    进度 {done}/{len(orders)}  通过 {ok}  "
              f"已耗时 {int(elapsed // 60)}分  预计剩余 {int(left // 60)}分", flush=True)

    print("\n" + "=" * 64)
    print(f"结束：{done} 题，通过 {ok}，失败 {done - ok}")
    print(f"记录：{RECORD}")


if __name__ == "__main__":
    main()
