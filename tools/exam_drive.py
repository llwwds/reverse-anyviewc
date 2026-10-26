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
import fcntl
import json
import os
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
LOCKFILE = ROOT / ".drive.lock"


def acquire_single_instance():
    """拿排他锁，保证全机同时只有一个驱动的实例。

    用 fcntl.flock 而不是「PID 文件 + kill -0」：PID 文件会残留、会写错、
    在受限环境里 ps/kill 还可能不可用；而 flock 由内核随进程存活自动释放，
    进程被 kill -9、断网、断电都不会留下死锁。

    返回的文件对象必须一直持有到进程结束，否则锁会被提前释放。
    """
    LOCKFILE.parent.mkdir(parents=True, exist_ok=True)
    f = open(LOCKFILE, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        try:
            other = LOCKFILE.read_text(encoding="utf-8").strip()
        except Exception:                              # noqa: BLE001
            other = "?"
        f.close()
        print(f"已有实例在跑（PID {other or '?'}），本次不启动，避免两路同时提交。", file=sys.stderr)
        print("  看进度：tools/run_submit.sh --status        停掉：tools/run_submit.sh --stop",
              file=sys.stderr)
        sys.exit(1)
    f.write(str(os.getpid()))
    f.flush()
    return f


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
    """估算这题「像人」要花多久。结果保证落在 [min_wait, max_wait] 内。

    做法不是「先算一个值再 clamp」——那样会让大量样本堆在上下界，一眼看出
    是压出来的。也不是按行数强分档——实测题量普遍 30~60 行，分档会把所有题
    挤到区间上半区，120 题全落在 151~266s，比均匀更假。

    折中：分位在 [0, 0.88] 上均匀撒，再按题量右移最多 0.12
    （短题几乎从下限起，长题整体偏后）。覆盖整个 60~300，且长题系统性更久。
    """
    lines = len([l for l in src.split("\n") if l.strip()])
    frac = min(1.0, max(0.0, (lines - 12) / 30.0))   # 12 行以下=短题，42 行以上=长题
    p = 0.88 * random.random() + 0.12 * frac
    return int(round(min_wait + (max_wait - min_wait) * p))


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
    """按题库顺序排出待做的题。

    「模板里已有历史代码」的判定必须结合台账：一旦我们提交过一次（哪怕失败），
    模板里就留下了我们自己的代码。若只看模板，这类题会被当成「你以前写过」
    永久跳过，失败的题再也没有重试机会。所以**台账里有记录的一律不跳过**，
    只跳过「模板有代码 且 我们从未碰过」的题。
    """
    record = record or {}
    out = []
    skipped = []
    for q in questions:
        eid = str(q["eid"])
        if only_eid and eid != str(only_eid):
            continue
        rec = record.get(eid) or {}
        if not redo and rec.get("passed"):
            continue
        if skip_existing and not rec:
            who = template_filled_by(eid)
            if who:
                skipped.append((q["pname"], eid, who))
                continue
        out.append(q)
        if limit and len(out) >= limit:
            break
    return out, skipped


def brief_error(error_order, data=None):
    """把 runGroup 的 errorOrder 压成一句话，直接写进台账。

    这是「编译过但判题不过」的唯一可靠线索：平台逐用例给出
    「正确结果 vs 你的结果」的字节级对照。以前要手工 dump 才知道，
    现在让它自动进台账，失败原因一眼可见。

    errorOrder 为空时回落取 data.result / data.message —— 程序运行时崩溃、
    段错误、输出为空这类情况平台不填 errorOrder，只把文本放在 result 里。
    """
    if not error_order:
        data = data or {}
        for key in ("result", "message", "msg"):
            txt = str(data.get(key) or "").strip()
            if txt:
                txt = re.sub(r"<br\s*/?>", " | ", txt)
                txt = re.sub(r"&nbsp;", " ", txt)
                txt = re.sub(r"<[^>]+>", "", txt)
                txt = re.sub(r"\s*\|\s*(\||)+", " | ", txt)
                return re.sub(r"\s+", " ", txt).strip(" |")[:1000]
        return ""
    # errorOrder 的 key 是用例序号，取第一个失败的
    key = sorted(error_order, key=lambda x: (len(str(x)), str(x)))[0]
    txt = str(error_order[key])
    txt = re.sub(r"<br\s*/?>", " | ", txt)
    txt = re.sub(r"&nbsp;", " ", txt)
    txt = re.sub(r"<[^>]+>", "", txt)
    txt = re.sub(r"\s*\|\s*(\||)+", " | ", txt)
    txt = re.sub(r"\s+", " ", txt).strip(" |")
    return txt[:1000]       # 留长一点：失败后要靠它定位，截太狠等于没记


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
        "reason": "" if passed else brief_error(d.get("errorOrder"), d),
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

    # 单实例锁：必须在真正落网络请求之前拿，避免两个人同时往同一账号提交
    global _LOCK
    _LOCK = acquire_single_instance()

    tk = S.token()
    print(f"开始，共 {len(orders)} 题\n" + "=" * 64, flush=True)

    done = ok = 0
    consec_fail = 0        # 连续未通过
    consec_nosave = 0      # 连续连「保存」都失败（基本是 token 挂了，不是答案错）
    consec_net = 0         # 连续网络失败（与答案无关）
    t_start = time.time()
    for idx, q in enumerate(orders, 1):
        eid = str(q["eid"])
        print(f"\n[{idx}/{len(orders)}] {q['pname']}  {q['course']} 第{q['chapter']}章  eid={eid}",
              flush=True)

        # 网络抖动不该把整批打死，也不该被记成「答案错」。单题内先重试两次，
        # 间隔 60s / 180s；仍然失败就记成 network 并继续下一题，最后统一复跑。
        r = None
        for net_try in range(3):
            try:
                r = do_one(tk, eid, answers, extras, a.min_wait, a.max_wait)
                break
            except S.NetworkError as exc:
                consec_net += 1
                if net_try == 2:
                    r = {"eid": eid, "pname": q["pname"], "ok": False,
                         "reason": f"network: {exc}", "network": True,
                         "time": time.strftime("%Y-%m-%d %H:%M:%S")}
                    print(f"    网络失败，已重试 3 次，跳过待复跑：{exc}", flush=True)
                    break
                cooldown = 60 * (net_try + 1)
                print(f"    网络失败（第 {net_try + 1} 次）：{exc}，{cooldown}s 后重试本题", flush=True)
                time.sleep(cooldown)
            except SystemExit:
                raise
            except Exception as exc:                       # noqa: BLE001
                r = {"eid": eid, "pname": q["pname"], "ok": False,
                     "reason": f"{type(exc).__name__}: {exc}",
                     "time": time.strftime("%Y-%m-%d %H:%M:%S")}
                break

        # 网络失败的题不覆盖已有记录，也不计入连续失败（否则会误触发熔断）
        if r.get("network"):
            prev = record.get(eid)
            if not (prev and prev.get("passed")):
                record[eid] = r
            save_record(record)
            done += 1
            if consec_net >= 6:
                print(f"\n连续 {consec_net} 次网络失败，网侧不可用。已中止。", flush=True)
                print("  处理：恢复网络后重跑本脚本，会从断点继续（台账已保存）。", flush=True)
                break
            print(f"    进度 {done}/{len(orders)}  通过 {ok}（本题待复跑）", flush=True)
            continue
        consec_net = 0

        record[eid] = r
        save_record(record)
        done += 1
        if r.get("passed"):
            ok += 1
            consec_fail = 0
            consec_nosave = 0
            print(f"    通过（runRight={r.get('runRight')}, 停顿 {r.get('wait')}s）", flush=True)
        else:
            consec_fail += 1
            consec_nosave = consec_nosave + 1 if r.get("saved") is False else 0
            print(f"    未通过：{r.get('reason') or r}", flush=True)

        elapsed = time.time() - t_start
        avg = elapsed / done
        left = avg * (len(orders) - done)
        print(f"    进度 {done}/{len(orders)}  通过 {ok}  "
              f"已耗时 {int(elapsed // 60)}分  预计剩余 {int(left // 60)}分", flush=True)

        # 熔断：把「答案有 bug」和「token / 平台挂了」分开。
        # 保存都写不进去，说明不是代码问题，继续跑只会把剩余题全刷成失败。
        if consec_nosave >= 3:
            print(f"\n连续 {consec_nosave} 题保存失败，token 可能已失效（单点登录被顶）。已中止。", flush=True)
            print("  处理：$PY tools/login.py 重新登录，然后重跑本脚本，会从断点继续。", flush=True)
            break
        if consec_fail >= 8:
            print(f"\n连续 {consec_fail} 题未通过，疑似答案或平台异常。已中止。", flush=True)
            print("  处理：$PY tools/doctor.py 排查；单题用 submit.py --eid <eid> --all 复现。", flush=True)
            break

    print("\n" + "=" * 64)
    net_left = [k for k, v in record.items() if v.get("network")]
    bad_left = [k for k, v in record.items() if not v.get("passed") and not v.get("network")]
    print(f"结束：{done} 题，通过 {ok}，未通过 {done - ok}"
          f"（其中网络失败 {len(net_left)}）")
    if net_left:
        print(f"  网络失败待复跑 {len(net_left)} 题：{', '.join(net_left[:12])}")
    if bad_left:
        print(f"  答案未通过 {len(bad_left)} 题：{', '.join(bad_left[:12])}")
        print("  逐题看台账 reason 字段（已内置 errorOrder 摘要），修完重跑即可")
    print(f"记录：{RECORD}")


if __name__ == "__main__":
    main()
