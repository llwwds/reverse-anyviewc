#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 答案/ 里的「函数级答案」拼进平台模板，产出一份可直接提交的完整源文件。

拼装原则（为了让提交物看起来就是一次正常答题）:

  1. 模板第一行 `#include "allinclude.h"  //DO NOT edit this line` 原样保留。
  2. 模板里的函数签名行原样保留（含行尾注释），**只替换函数体内容**。
     学生答题就是在 `// Add your code here` 那几行里写代码，签名一个字都不会动。
  3. 答案里模板没有的辅助函数，追加到文件末尾（学生自己加 helper 是常见写法）。
  4. 答案里的思路说明/note/markdown 一律不出现——那些只在 答案/ 的 md 里给人看。
  5. 默认去掉答案代码的行尾注释，并统一缩进，避免出现明显的「讲解式注释」。

用法:
  python assemble.py --eid 11875062                  # 打印拼装结果
  python assemble.py --eid 11875062 --out a.c        # 写到文件
  python assemble.py --eid 11875062 --push           # 拼装 + 保存 + 编译 + 判题
  python assemble.py --all --push                    # 全部 152 题
  python assemble.py --all --check                   # 只拼装并做语法自检，不提交
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths         # noqa: E402  数据根定位
import submit as S  # noqa: E402  复用 curl 通道与三个写接口

ROOT = paths.data_root()
CTRL = {"if", "for", "while", "switch", "else", "do", "case", "return", "sizeof", "struct", "union", "enum"}
INDENT = "    "


# ------------------------------------------------------------------ 词法遮蔽

def mask(src, spans=None):
    """把注释、字符串、字符字面量替换成等长空格，保留换行。

    这样后续扫描括号时不会被注释里的 `{` 干扰，而所有下标仍与原文一一对应。
    传 spans 时把每个注释的 (start, end, is_line) 追加进去，供删除注释用。
    """
    out = list(src)
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        if ch == "/" and i + 1 < n and src[i + 1] == "/":
            s = i
            while i < n and src[i] != "\n":
                out[i] = " "
                i += 1
            if spans is not None:
                spans.append((s, i, True))
        elif ch == "/" and i + 1 < n and src[i + 1] == "*":
            s = i
            while i < n and not (src[i] == "*" and i + 1 < n and src[i + 1] == "/"):
                if src[i] != "\n":
                    out[i] = " "
                i += 1
            if i < n:
                out[i] = out[i + 1] = " "
                i += 2
            if spans is not None:
                spans.append((s, i, False))
        elif ch in "\"'":
            q = ch
            out[i] = " "
            i += 1
            while i < n and src[i] != q:
                if src[i] == "\\" and i + 1 < n:
                    out[i] = out[i + 1] = " "
                    i += 2
                    continue
                if src[i] != "\n":
                    out[i] = " "
                i += 1
            if i < n:
                out[i] = " "
                i += 1
        else:
            i += 1
    return "".join(out)


# ------------------------------------------------------------------ 函数识别

class Func:
    __slots__ = ("name", "line_start", "body_open", "body_close")

    def __init__(self, name, line_start, body_open, body_close):
        self.name = name
        self.line_start = line_start
        self.body_open = body_open
        self.body_close = body_close


def find_functions(src):
    """找出源码里所有最外层函数定义，返回 [Func]。

    { 往前推：跳过空白 → 必须是 ')' → 配平括号取参数表 → 取函数名。
    函数名落在 CTRL 里的（if/for/while...）不是函数，丢掉。
    """
    m = mask(src)
    n = len(m)
    found = []

    for i, ch in enumerate(m):
        if ch != "{":
            continue

        j = i - 1
        while j >= 0 and m[j] in " \t\r\n":
            j -= 1
        if j < 0 or m[j] != ")":
            continue

        depth, k = 0, j
        while k >= 0:
            if m[k] == ")":
                depth += 1
            elif m[k] == "(":
                depth -= 1
                if depth == 0:
                    break
            k -= 1
        if k < 0:
            continue

        e = k - 1
        while e >= 0 and m[e] in " \t\r\n":
            e -= 1
        s = e
        while s >= 0 and (m[s].isalnum() or m[s] == "_"):
            s -= 1
        name = m[s + 1: e + 1]
        if not name or name[0].isdigit() or name in CTRL:
            continue

        depth, t = 0, i
        while t < n:
            if m[t] == "{":
                depth += 1
            elif m[t] == "}":
                depth -= 1
                if depth == 0:
                    break
            t += 1
        if t >= n:
            continue

        line_start = src.rfind("\n", 0, s) + 1
        found.append(Func(name, line_start, i, t))

    found.sort(key=lambda f: (f.line_start, f.body_open))
    outer = []
    for f in found:
        if outer and f.body_close <= outer[-1].body_close:
            continue
        outer.append(f)
    return outer


# ------------------------------------------------------------------ 代码整理

def strip_trailing_comments(text):
    """删掉所有注释（行注释 + 块注释），字符串里的不动。"""
    spans = []
    mask(text, spans)
    if not spans:
        return text

    out = []
    pos = 0
    for start, end, _is_line in spans:
        out.append(text[pos:start])
        # 行注释整段抹掉，后面紧跟的换行由下一段原样带回来
        pos = end
    out.append(text[pos:])
    return "".join(out)


def normalize_body(body, strip_comments=True, width=1):
    """把函数体整理成：去注释、去首尾空行、缩进归一到 width 级。"""
    if strip_comments:
        body = strip_trailing_comments(body)

    lines = [l.rstrip() for l in body.split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    if not lines:
        return ""

    indents = [len(l) - len(l.lstrip(" \t")) for l in lines if l.strip()]
    base = min(indents) if indents else 0

    out = []
    for l in lines:
        if not l.strip():
            out.append("")
        else:
            out.append(INDENT * width + l[base:] if len(l) > base else l.strip())
    return "\n".join(out)


# ------------------------------------------------------------------ 拼装

def assemble(template, answer, strip_comments=True, prelude_extra=""):
    """模板骨架 + 答案实现 → 完整提交源码。返回 (源码, 报告 dict)。

    prelude_extra 是答案库 _src 里那些「题目没给、但答案需要」的片段
    （typedef 或辅助函数），统一插到模板第一个函数之前。
    """
    tf = find_functions(template)
    af = find_functions(answer)
    amap = {f.name: f for f in af}

    report = {"filled": [], "appended": [], "kept": [], "fallback": False, "extra": bool(prelude_extra)}

    if not tf:
        # 模板里没有函数（极少数纯 include 模板）→ 直接把答案接到头部之后
        head = template
        if not answer.lstrip().startswith("#include"):
            body = normalize_body(answer, strip_comments, 0)
            report["fallback"] = True
            return head.rstrip("\n") + "\n" + body + "\n", report

    edits = []
    for f in tf:
        a = amap.get(f.name)
        if a is None:
            report["kept"].append(f.name)
            continue
        body = normalize_body(answer[a.body_open + 1: a.body_close], strip_comments, 1)
        edits.append((f.body_open + 1, f.body_close, "\n" + body + "\n"))
        report["filled"].append(f.name)

    out = template
    for start, end, body in sorted(edits, key=lambda x: -x[0]):
        out = out[:start] + body + out[end:]

    # 答案里模板没有的辅助函数 → 插到模板第一个函数之前
    # （C 要求先声明后使用；放在文件末尾会让主函数里的调用变成未声明）
    prefixes = []
    if prelude_extra.strip():
        blk = prelude_extra.strip("\n")
        if strip_comments:
            blk = strip_trailing_comments(blk)
        prefixes.append("\n".join(l.rstrip() for l in blk.split("\n")).strip("\n"))

    tf_names = {f.name for f in tf}
    for f in af:
        if f.name in tf_names:
            continue
        block = answer[f.line_start: f.body_close + 1]
        if strip_comments:
            block = strip_trailing_comments(block)
        block = "\n".join(l.rstrip() for l in block.split("\n")).strip("\n")
        if block.strip():
            prefixes.append(block)
            report["appended"].append(f.name)

    if prefixes:
        block = "\n\n".join(p for p in prefixes if p.strip()) + "\n\n"
        cur = find_functions(out)          # 替换过函数体，位置要重新算
        pos = cur[0].line_start if cur else len(out)
        out = out[:pos] + block + out[pos:]

    if not out.endswith("\n"):
        out += "\n"

    return out, report


# ------------------------------------------------------------------ 语法自检

def syntax_check(src, workdir=None):
    """用 g++ -fsyntax-only 粗检。模板带 allinclude.h，本地没有，
    所以塞一个最小 prelude 顶上。返回 (ok, 诊断)。"""
    prelude = """
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef int Status;
typedef int ElemType;
typedef int KeyType;
typedef char StrKeyType;
#define OK 1
#define ERROR 0
#define TRUE 1
#define FALSE 0
#define OVERFLOW -2
#define MAXINT 2147483647
"""
    body = re.sub(r'^[ \t]*#include[^\n]*\n', "", src, flags=re.M)
    probe = prelude + body
    fd, path = tempfile.mkstemp(suffix=".cpp", dir=workdir)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(probe)
    try:
        p = subprocess.run(["g++", "-std=c++11", "-fsyntax-only", "-w", path],
                           capture_output=True, text=True)
        return p.returncode == 0, (p.stderr or "").strip()[:400]
    finally:
        os.remove(path)


# ------------------------------------------------------------------ main

def load_src_modules():
    """加载 答案/_src/*.py，返回模块列表。"""
    import importlib.util

    mods = []
    for p in sorted((ROOT / "答案" / "_src").glob("*.py")):
        spec = importlib.util.spec_from_file_location(f"_ansrc_{p.stem}", p)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as exc:                      # noqa: BLE001
            print(f"!! 加载 {p.name} 失败: {exc}")
            continue
        mods.append(mod)
    return mods


def load_answers():
    """答案以 答案/_src 为权威源（改完立刻生效），answers.json 只补元信息。"""
    meta = {str(r["eid"]): r for r in json.loads(
        (ROOT / "答案" / "answers.json").read_text(encoding="utf-8"))}
    out = {}
    for mod in load_src_modules():
        for eid, spec in (getattr(mod, "ANSWERS", {}) or {}).items():
            eid = str(eid)
            m = meta.get(eid) or {}
            out[eid] = {
                "eid": eid,
                "pname": m.get("pname") or eid,
                "code": spec.get("code") or "",
                "note": spec.get("note") or "",
            }
    for eid, m in meta.items():
        out.setdefault(eid, m)
    return out


# 平台 allinclude.h 已经给出的基础 typedef。答案库里的 pre_extra 有时为了本地
# g++ 校验补了一份，直接拼进平台代码会变成重复定义（conflicting declaration）。
BASE_TYPEDEF = re.compile(
    r"^\s*typedef\s+(?:unsigned\s+)?(?:int|char|float|double|long|short)\s+\w+\s*;\s*$")


def strip_platform_typedefs(text):
    """删掉与平台重复的基础 typedef 行。"""
    keep = [ln for ln in text.split("\n") if not BASE_TYPEDEF.match(ln)]
    return "\n".join(keep).strip()


def load_src_extras():
    """从 答案/_src 收集每题的 pre_extra / extra 片段。

    这些片段是「题目模板里没给、但答案必须依赖」的东西：宏、typedef、
    或者辅助函数（如 countNodes / treeDepth）。拼装时要一并带上，
    否则拼出来的代码会缺声明。
    """
    out = {}
    for mod in load_src_modules():
        for eid, s in (getattr(mod, "ANSWERS", {}) or {}).items():
            pieces = []
            for key in ("pre_extra", "extra"):
                v = s.get(key)
                if v and str(v).strip():
                    pieces.append(str(v).strip())
            if pieces:
                merged = strip_platform_typedefs("\n".join(pieces))
                if merged.strip():
                    out[str(eid)] = merged
    return out


def build_one(eid, answers, tk=None, fetch=True, extras=None):
    """取模板 + 取答案 → 拼装。返回 (源码, report, 元信息)。"""
    det = S.get_question(tk, eid) if fetch else None
    if det is None:
        blob = json.loads((ROOT / "原始数据" / f"q_{eid}.json").read_text(encoding="utf-8"))
        det = blob.get("detail") or {}

    b64 = det.get("stuCode") or ""
    template = base64.b64decode(b64).decode("utf-8", "replace") if b64 else ""
    ans = answers.get(str(eid)) or {}
    answer = ans.get("code") or ""

    if not answer.strip():
        return template, {"filled": [], "appended": [], "kept": [], "fallback": False, "no_answer": True}, det

    prelude = (extras or {}).get(str(eid), "")
    src, rep = assemble(template, answer, prelude_extra=prelude)
    rep["pname"] = ans.get("pname") or det.get("questionFullName") or eid
    return src, rep, det


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eid")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--out-dir", help="全量拼装时导出目录（每题一个 .c）")
    ap.add_argument("--push", action="store_true", help="保存 + 编译 + 判题")
    ap.add_argument("--verify", action="store_true", help="只调平台 compile 验证，不保存不判题")
    ap.add_argument("--sleep", type=float, default=1.5, help="每题间隔秒数（默认 1.5）")
    ap.add_argument("--check", action="store_true", help="只做语法自检")
    ap.add_argument("--keep-comments", action="store_true", help="保留答案里的行尾注释")
    ap.add_argument("--no-fetch", action="store_true", help="用本地原始数据，不请求接口")
    a = ap.parse_args()

    answers = load_answers()
    extras = load_src_extras()
    tk = S.token()

    eids = [a.eid] if a.eid else [str(r["eid"]) for r in json.loads(
        (ROOT / "题库" / "题库.json").read_text(encoding="utf-8"))] if a.all else []
    if not eids:
        sys.exit("要么 --eid，要么 --all")

    ok_cnt = fail_cnt = 0
    local_bad = 0
    failures = []
    for eid in eids:
        src, rep, det = build_one(eid, answers, tk, fetch=not a.no_fetch, extras=extras)
        name = rep.get("pname") or eid
        if rep.get("no_answer"):
            print(f"!! {name} ({eid}) 没有答案，跳过")
            fail_cnt += 1
            continue

        # 本地 g++ 只作参考：假 prelude 缺平台私有类型，会误报，不能当判定
        local_ok, local_diag = syntax_check(src)
        if not local_ok:
            local_bad += 1
        print(f"[{name:12s}] {eid:9s} fill={rep['filled']} "
              f"append={rep['appended']} keep={rep['kept']}"
              f"{'' if local_ok else '  (本地 prelude 缺类型，忽略)'}")

        if a.out and not a.all:
            Path(a.out).write_text(src, encoding="utf-8")
            print(f"      -> {a.out}")

        if a.out_dir:
            d = Path(a.out_dir)
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{name}.c").write_text(src, encoding="utf-8")

        if not (a.push or a.verify):
            if local_ok:
                ok_cnt += 1
            else:
                fail_cnt += 1
                failures.append((name, eid, local_diag))
            continue

        full = det.get("questionFullName") or f"第{det.get('chapter') or 1}章-{name}"
        short = S.short_name(full)
        kind = det.get("kind") or 3
        lang = det.get("language") or 1
        cb = base64.b64encode(src.encode()).decode()

        if a.push:
            st, j = S.save_code(tk, eid, cb)
            print(f"      保存 HTTP {st} code={j.get('code')} {j.get('message')}")
            # 平台异步落库：立刻 compile/runGroup 会拿到上一版代码，等一会儿
            time.sleep(6)

        st, j = S.compile_code(tk, eid, cb, full)
        d = j.get("data") or {}
        raw = re.sub(r"<[^>]+>", " ", d.get("result") or "").strip()
        okc = (j.get("code") == 200) and ("编译成功" in raw)
        print(f"      编译 {'OK ' if okc else 'ERR'} cmp={d.get('cmpRightCount')}/{d.get('cmpCount')} "
              f"err={d.get('cmpErrorCount')} {raw[:70]!r}")
        if okc:
            ok_cnt += 1
        else:
            fail_cnt += 1
            failures.append((name, eid, raw[:300]))

        if a.push:
            time.sleep(2)
            st, j = S.run_group(tk, eid, cb, short, kind, lang)
            d = j.get("data") or {}
            print(f"      判题 passed={d.get('passed')} runRight={d.get('runRightCount')} "
                  f"end={d.get('end')}")

        if a.sleep and len(eids) > 1:
            time.sleep(a.sleep)

    judged = "平台编译" if (a.push or a.verify) else "本地语法自检"
    print(f"\n{judged}: {ok_cnt} 成功 / {fail_cnt} 失败"
          f"   （本地 prelude 误报 {local_bad} 题，仅供参考）")
    if failures:
        print("\n=== 失败明细 ===")
        for name, eid, diag in failures[:20]:
            print(f"\n-- {name} ({eid})")
            print(diag[:300])


if __name__ == "__main__":
    main()
