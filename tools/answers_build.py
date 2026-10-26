#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
答案流水线：编译校验 + 渲染
- 读 答案/_src/chNN.py 里的 ANSWERS 字典
- 从题干自动抽取 typedef / #define / 平台函数原型，拼成 g++ 语法检查用 harness
- g++ -fsyntax-only 校验每份答案
- 渲染 答案/第N章.md、答案/全部答案.md、答案/answers.json、答案/校验报告.md

ANSWERS 每项可带的键:
    code        答案代码（必填，例题可为空）
    note        思路说明
    skip        跳过作答的说明（例题）
    extra       插在题干 typedef 之后、原型之前的补充声明
    pre_extra   插在 prelude 之后、题干 typedef 之前的补充（#define / KeyType 等）
    drop        list，从 prelude 里删掉的类型（如 ["ElemType"]）
    doc_fix     list of [old, new]，修正题干里的类型笔误（只影响校验，不影响答案）

用法:
    python answers_build.py            # 校验 + 渲染
    python answers_build.py --check    # 只校验
"""
import argparse
import glob
import importlib.util
import json
import os
import re
import subprocess
import tempfile

import paths

ROOT = str(paths.data_root())
SRC = os.path.join(ROOT, "答案", "_src")
OUT = os.path.join(ROOT, "答案")
QB = json.load(open(os.path.join(ROOT, "题库", "题库.json"), encoding="utf-8"))
BY_EID = {str(q["eid"]): q for q in QB}

GPP = subprocess.run(["which", "g++"], capture_output=True, text=True).stdout.strip() or "g++"

PRELUDE_LINES = [
    "#include <stdio.h>",
    "#include <stdlib.h>",
    "#include <string.h>",
    "#include <math.h>",
    "",
    "typedef int Status;",
    "typedef int ElemType;",
    "typedef char TElemType;",
    "#define OK 1",
    "#define ERROR 0",
    "#define TRUE 1",
    "#define FALSE 0",
    "#define OVERFLOW -2",
    "#define EOVERFLOW -3",
    "#define INFEASIBLE -1",
    "#define MAXINT 2147483647",
]
DROP_LINE = {
    "ElemType": "typedef int ElemType;",
    "TElemType": "typedef char TElemType;",
    "Status": "typedef int Status;",
}


def _strip_comment(line):
    s = re.sub(r"/\*.*?\*/", "", line)
    s = re.sub(r"//.*$", "", s)
    return s.rstrip()


def _declarator_names(block):
    """从一段 typedef 语句里取出被声明的类型名"""
    names = set()
    for m in re.finditer(r"\}\s*\**\s*([A-Za-z_]\w*(?:\s*,\s*\**\s*[A-Za-z_]\w*)*)\s*;", block):
        names |= set(re.findall(r"[A-Za-z_]\w*", m.group(1)))
    for m in re.finditer(r"^\s*typedef\s+[A-Za-z_][\w\s]*?\s+\**\s*([A-Za-z_]\w*)\s*;", block, re.M):
        names.add(m.group(1))
    return names


def extract_decls(doc):
    """从题干抽取 typedef 块 / #define / 函数原型"""
    lines = (doc or "").replace("\r\n", "\n").split("\n")
    texts, protos, defined = [], [], set()
    i = 0
    while i < len(lines):
        code = _strip_comment(lines[i]).strip()
        if not code:
            i += 1
            continue
        if code.startswith("#define"):
            texts.append(code)
            i += 1
            continue
        if code.startswith("typedef"):
            block, depth = [], 0
            while i < len(lines):
                cur = _strip_comment(lines[i]).strip()
                block.append(cur)
                depth += cur.count("{") - cur.count("}")
                i += 1
                if depth == 0 and cur.endswith(";"):
                    break
            b = "\n".join(x for x in block if x)
            b = re.sub(r"^\s*(\.\.\.|…).*$", "", b, flags=re.M)      # 省略号字段
            b = re.sub(r"\n\s*\n", "\n", b)
            defined |= _declarator_names(b)
            texts.append(b)
            continue
        if ("(" in code and code.endswith(";")
                and "[" not in code and "]" not in code
                and not code.startswith(("//", "*", "return", "printf", "if", "for", "while"))
                and re.match(r"^[A-Za-z_][\w\s\*]*[\*&\s]\s*\**\w+\s*\(", code)):
            protos.append(code)
            i += 1
            continue
        i += 1
    return list(dict.fromkeys(texts)), list(dict.fromkeys(protos)), defined


def build_harness(doc, code, extra="", pre_extra="", drop=None):
    texts, protos, defined = extract_decls(doc)
    dropped = set(DROP_LINE) & (defined | set(drop or []))
    pre = "\n".join(l for l in PRELUDE_LINES
                    if not any(l == DROP_LINE[d] for d in dropped))
    main = "" if re.search(r"\bint\s+main\s*\(", code) else "\nint main(void){return 0;}\n"
    return f"{pre}\n{pre_extra}\n{chr(10).join(texts)}\n{extra}\n{chr(10).join(protos)}\n{code}\n{main}"


def check(code, doc, extra="", pre_extra="", drop=None, doc_fix=None):
    for old, new in (doc_fix or []):
        doc = doc.replace(old, new)
    harness = build_harness(doc, code, extra, pre_extra, drop)
    with tempfile.NamedTemporaryFile("w", suffix=".cpp", delete=False, encoding="utf-8") as f:
        f.write(harness)
        path = f.name
    try:
        p = subprocess.run([GPP, "-std=c++11", "-fsyntax-only", "-w", path],
                           capture_output=True, text=True, timeout=60)
        err = "\n".join(l for l in p.stderr.split("\n") if l.strip())[:600]
        return p.returncode == 0, err
    except Exception as e:  # noqa: BLE001
        return False, repr(e)
    finally:
        os.unlink(path)


def load_answers():
    merged = {}
    for fp in sorted(glob.glob(os.path.join(SRC, "ch*.py"))):
        name = os.path.basename(fp)[:-3]
        spec = importlib.util.spec_from_file_location(name, fp)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        merged.update({str(k): v for k, v in mod.ANSWERS.items()})
    return merged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    ans = load_answers()
    print(f"已写答案 {len(ans)} / {len(BY_EID)} 题")

    report, fails = [], 0
    for eid, v in sorted(ans.items(), key=lambda x: int(x[0])):
        q = BY_EID.get(eid)
        if q is None:
            print(f"  [!!] 未知 eid {eid}")
            continue
        if v.get("skip"):
            v["ok"], v["err"] = True, ""
            continue
        ok, err = check(v["code"], q["doc"], v.get("extra", ""), v.get("pre_extra", ""),
                        v.get("drop"), v.get("doc_fix"))
        v["ok"], v["err"] = ok, err
        if not ok:
            fails += 1
            print(f"  [fail] {q['pname']} ({eid})\n         " +
                  err.replace("\n", "\n         ")[:400])
        report.append((q["pname"], eid, ok, err))

    total_code = sum(1 for v in ans.values() if not v.get("skip"))
    print(f"编译校验：{total_code - fails}/{total_code} 通过")
    if args.check:
        return

    chapters = {}
    for eid, v in ans.items():
        q = BY_EID[eid]
        chapters.setdefault((q["course"], q["chapter"]), []).append((q, v))

    os.makedirs(OUT, exist_ok=True)
    all_md = ["# AnyView 数据结构题库 · 全部答案", "",
              f"共 {len(ans)} 题 ｜ 通过 g++ 语法校验 "
              f"{sum(1 for v in ans.values() if v.get('ok'))} 题", ""]
    rows = []
    for (course, ch), items in sorted(chapters.items(),
                                      key=lambda x: (x[0][0], int(x[0][1]) if x[0][1].isdigit() else 0)):
        items.sort(key=lambda x: x[0]["pname"])
        title = f"第{int(ch)}章" if ch.isdigit() else f"第{ch}章"
        buf = [f"# {course} · {title} 答案", ""]
        for q, v in items:
            buf += [f"### {q['pname']}" + (f" · {q['remark']}" if q.get("remark") else ""), ""]
            buf += [f"> 难度 {q.get('difficulty') or '—'} ｜ eid `{q['eid']}`"
                    + (" ｜ ⚠️ 未通过编译校验" if not v.get("ok") and not v.get("skip") else ""), ""]
            if q.get("remark"):
                buf += [f"**题目**：{q['remark']}", ""]
            if v.get("note"):
                buf += [f"**思路**：{v['note']}", ""]
            if v.get("skip"):
                buf += [v.get("skip") or "", ""]
            else:
                buf += ["```c", v["code"].strip(), "```", ""]
            buf += ["---", ""]
            rows.append({"eid": q["eid"], "pname": q["pname"], "course": course,
                         "chapter": ch, "compile_ok": bool(v.get("ok")),
                         "note": v.get("note"), "code": v.get("code")})
        safe = re.sub(r'[\\/:*?"<>|]', "_", course)
        os.makedirs(os.path.join(OUT, safe), exist_ok=True)
        open(os.path.join(OUT, safe, f"{title}.md"), "w", encoding="utf-8").write("\n".join(buf))
        all_md += [f"## {course} · {title}", ""] + buf[4:]

    open(os.path.join(OUT, "全部答案.md"), "w", encoding="utf-8").write("\n".join(all_md))
    json.dump(rows, open(os.path.join(OUT, "answers.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    rep = ["# 答案编译校验报告", "",
           f"编译器：`{GPP}`　标准：`-std=c++11 -fsyntax-only`", "",
           "| 题号 | eid | 结果 |", "|---|---|---|"]
    for pname, eid, ok, _ in report:
        rep.append(f"| {pname} | {eid} | {'✅ 通过' if ok else '❌ 失败'} |")
    rep += ["", "> 校验方式：把题干里的 typedef / 平台函数原型抽出，拼成只含声明的最小程序，",
            "> 与答案一起交给编译器做语法检查。验证的是「能否编译」，不验证算法逻辑正确性。"]
    open(os.path.join(OUT, "校验报告.md"), "w", encoding="utf-8").write("\n".join(rep))

    print(f"渲染完成 -> {OUT}")


if __name__ == "__main__":
    main()
