#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量失败题的体检器：只编译、不判题，把平台的报错原文洗干净打出来。

用途：`exam_drive.py` 跑完后，先把所有未通过的题集中过一遍编译，
区分两类失败 ——

    编译失败   拼装出来的代码本身过不了编译器（重复定义、缺定义、类型不符）
    编译通过   代码能跑但输出与标准答案逐字节不一致（逻辑/边界/格式问题）

编译接口不改变题目状态（exerciseStatus 不受影响），所以可以随时反复调，
不像 runGroup 会留下判题痕迹。

用法:
    python triage.py --failed              # 台账里所有未通过的题（只编译）
    python triage.py --eid 11875315        # 指定题
    python triage.py --failed --show-src   # 顺带打印拼装后的源码
    python triage.py --failed --errors     # 不编译，直接读平台上留存的完整 errorOrder

`--errors` 值得单独说：平台把最近一次判题失败逐用例的「正确结果 vs 你的结果」
存在状态接口 `detail/<eid>` 的 `data.exercise.errorOrder` 里，**是完整文本**。
以前要重新判一遍题才能看到，其实只读 GET 一次就有 —— 而且不影响题目状态。
台账里的 `reason` 是这份文本的前 1000 字，长用例会被截断，看细节就用这个。
"""
import argparse
import base64
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import assemble as A    # noqa: E402
import paths           # noqa: E402
import submit as S      # noqa: E402


def clean(html):
    """把编译器输出里的 HTML 实体和标签洗掉。"""
    t = str(html or "")
    t = re.sub(r"<br\s*/?>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = t.replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">")
    t = t.replace("&amp;", "&").replace("&quot;", '"')
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def compile_only(tk, eid, answers, extras):
    src, rep, det = A.build_one(eid, answers, tk, fetch=True, extras=extras)
    name = rep.get("pname") or eid
    if not src.strip():
        return name, None, "无答案或拼装为空", src
    full = det.get("questionFullName") or ""
    st, j = S.compile_code(tk, eid, base64.b64encode(src.encode()).decode(), full)
    d = j.get("data") or {}
    txt = clean(d.get("result"))
    ok = (j.get("code") == 200) and ("编译成功" in txt)
    return name, ok, txt, src


def dump_errors(tk, eid):
    """读平台上留存的完整 errorOrder —— 只 GET，不判题、不改变任何状态。"""
    st, j = S.call("GET", f"/scheme-service/scheme/list/student/detail/{eid}", tk)
    ex = ((j.get("data") or {}).get("exercise")) or {}
    eo = ex.get("errorOrder")
    if isinstance(eo, str) and eo.strip().startswith("{"):
        try:
            eo = json.loads(eo)
        except Exception:                            # noqa: BLE001
            eo = {}
    if not isinstance(eo, dict):
        eo = {}
    return ex, eo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eid", action="append", help="可重复")
    ap.add_argument("--failed", action="store_true", help="台账里所有未通过的题")
    ap.add_argument("--show-src", action="store_true")
    ap.add_argument("--errors", action="store_true",
                    help="不编译，直接读平台上留存的完整 errorOrder（只 GET）")
    a = ap.parse_args()

    rec = json.loads(paths.record_file().read_text(encoding="utf-8"))
    if a.failed:
        eids = [k for k, v in rec.items() if not v.get("passed")]
    elif a.eid:
        eids = a.eid
    else:
        sys.exit("要么 --failed，要么 --eid <eid>")
    if not eids:
        print("没有未通过的题。")
        return

    tk = S.token()

    if a.errors:
        for eid in eids:
            ex, eo = dump_errors(tk, eid)
            print("=" * 70)
            print(f"{rec.get(eid, {}).get('pname') or eid}  eid={eid}  "
                  f"runErrCount={ex.get('runErrCount')}  "
                  f"lastPastTime={ex.get('lastPastTime')}")
            if not eo:
                print("  平台上没有 errorOrder（可能最近一次是编译失败或还没判过）")
                continue
            for k in sorted(eo, key=lambda x: (len(str(x)), str(x))):
                body = clean(eo[k]).strip()
                print(f"  ---- 用例 {k} ----")
                for line in body.splitlines()[:12]:
                    print(f"    {line}")
        return

    answers = A.load_answers()
    extras = A.load_src_extras()

    bad = []
    print(f"体检 {len(eids)} 题（只编译，不判题）\n" + "=" * 68)
    for eid in eids:
        name, ok, txt, src = compile_only(tk, eid, answers, extras)
        head = "编译失败" if ok is False else ("编译通过" if ok else "异常")
        print(f"\n[{head}] {name}  eid={eid}  {len(src.splitlines())} 行")
        if txt:
            for line in txt.splitlines()[:14]:
                print(f"    {line}")
        if a.show_src:
            print("    ---- 源码 ----")
            for line in src.splitlines():
                print(f"    | {line}")
        if ok is not True:
            bad.append((name, eid))

    print("\n" + "=" * 68)
    print(f"编译失败 {len(bad)} 题：{', '.join(n for n, _ in bad) if bad else '无'}")


if __name__ == "__main__":
    main()
