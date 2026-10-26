#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AnyView Stage 3: 把 原始数据/*.json 渲染成可读 Markdown 题库
输出: <数据根>/题库/
    README.md            使用说明
    index.md             总目录
    全题库.md             单文件合集
    题库.json             结构化全量数据
    <课程>/第XX章.md      按章节分文件
"""
import base64
import json
import os
import re
import time
from collections import OrderedDict

import paths

ROOT = str(paths.data_root())
RAW = str(paths.raw_dir())
OUT = str(paths.data_root() / "题库")

CODE_CHARS = re.compile(r"[;{}\(\)\[\]=#]")
PROSE_START = ("【", "要求", "说明", "注意", "例如", "提示", "本题", "已知", "请")


def is_prose(line):
    """True = 散文行, False = 代码行"""
    s = line.strip()
    if not s:
        return False
    if s.startswith(("//", "/*", "*")):
        return False
    body = s.rstrip("。：，")
    if s.startswith(PROSE_START) and not CODE_CHARS.search(body):
        return True
    if s.endswith(("。", "：", "，")) and not CODE_CHARS.search(body):
        return True
    if re.search(r"[\u4e00-\u9fff]", s) and not CODE_CHARS.search(s):
        return True
    return False


def render_doc(doc):
    lines = (doc or "").replace("\r\n", "\n").split("\n")
    out, buf, incode = [], [], False

    def flush_code():
        nonlocal buf, incode
        if buf:
            out.append("```c\n" + "\n".join(buf).rstrip() + "\n```")
            buf = []
        incode = False

    def flush_text():
        nonlocal buf
        if buf:
            out.append("\n".join(buf).strip())
            buf = []

    for ln in lines:
        if not ln.strip():
            buf.append("")
            continue
        if is_prose(ln):
            if incode:
                flush_code()
            buf.append(ln.strip())
        else:
            if not incode:
                flush_text()
                incode = True
            buf.append(ln.rstrip())

    if incode:
        flush_code()
    else:
        flush_text()
    txt = "\n\n".join(x for x in out if x.strip())
    return re.sub(r"\n{3,}", "\n\n", txt)


def stars(n):
    try:
        k = max(0, min(5, int(float(n))))
    except (TypeError, ValueError):
        return "—"
    return "★" * k + "☆" * (5 - k)


def safe(name):
    return re.sub(r'[\\/:*?"<>|]', "_", (name or "").strip())[:60]


def decode_code(b64):
    if not b64:
        return ""
    try:
        return base64.b64decode(b64).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return ""


def question_block(q, idx=None):
    cat, det = q["catalog"], q["detail"]
    prog = q.get("progress") or {}
    remark = (prog.get("remark") or cat.get("pmemo") or "").strip()
    pname = cat.get("pname") or det.get("questionFullName", "")
    num = f"{idx}. " if idx is not None else ""
    head = f"### {num}{pname}" + (f" · {remark}" if remark else "")

    meta = [f"难度 {stars(prog.get('difficulty'))}", f"eid `{cat.get('eid')}`"]
    parts = [head, "", "> " + " ｜ ".join(meta), ""]

    doc = render_doc(det.get("doc"))
    if doc:
        parts += [doc, ""]

    comp = det.get("completes") or []
    if comp:
        vals = "、".join(f"`{c.get('value')}`" for c in comp)
        parts += [f"**填空项（{len(comp)}）**：{vals}", ""]

    code = decode_code(det.get("stuCode") or "")
    if code.strip():
        parts += ["<details><summary>初始代码模板</summary>", "",
                  "```c", code.rstrip(), "```", "", "</details>", ""]
    return "\n".join(parts)


def main():
    manifest = json.load(open(os.path.join(RAW, "_manifest.json"), encoding="utf-8"))
    cache = {}
    for fn in os.listdir(RAW):
        if fn.startswith("q_") and fn.endswith(".json"):
            cache[fn[2:-5]] = json.load(open(os.path.join(RAW, fn), encoding="utf-8"))

    courses = OrderedDict()
    for item in manifest["questions"]:
        eid = str(item["eid"])
        if eid not in cache:
            continue
        key = (item["schemeId"], item["courseName"], item["tableName"])
        courses.setdefault(key, []).append({**item, "q": cache[eid]})

    os.makedirs(OUT, exist_ok=True)
    all_md = ["# AnyView 数据结构题库（全量）", ""]
    index_rows = []
    total = 0
    flat = []

    for (sid, cname, tname), items in courses.items():
        cdir = os.path.join(OUT, safe(cname))
        os.makedirs(cdir, exist_ok=True)
        chapters = OrderedDict()
        for it in items:
            chapters.setdefault(str(it.get("chapName") or "0"), []).append(it)

        all_md += [f"# {cname}", "", f"> {tname} ｜ {len(items)} 题", ""]
        sec_rows = []

        for ch in sorted(chapters, key=lambda x: (len(x), x)):
            qs = chapters[ch]
            title = f"第{int(ch)}章" if ch.isdigit() else f"第{ch}章"
            head = [f"# {cname} · {title}", "",
                    f"> 共 {len(qs)} 题 ｜ 课程：{tname}", ""]
            body = []
            for i, it in enumerate(qs, 1):
                body += [question_block(it["q"], i), "", "---", ""]
                flat.append({"course": cname, "table": tname, "chapter": ch,
                             "eid": it["eid"], "pname": it["pname"],
                             "questionType": it["questionType"],
                             "difficulty": (it["q"].get("progress") or {}).get("difficulty"),
                             "remark": (it["q"].get("progress") or {}).get("remark"),
                             "doc": it["q"]["detail"].get("doc"),
                             "template": decode_code(it["q"]["detail"].get("stuCode") or "")})
            fp = os.path.join(cdir, f"{title}.md")
            open(fp, "w", encoding="utf-8").write("\n".join(head + body))
            total += len(qs)
            sec_rows.append((title, len(qs), os.path.relpath(fp, OUT)))
            all_md += [f"## {cname} · {title}（{len(qs)} 题）", ""] + body

        index_rows.append((cname, tname, len(items), sec_rows))

    # 账号信息不写死在代码里：需要显示时由 ANYVIEW_ACCOUNT 传入，未设置则留空
    account = os.environ.get("ANYVIEW_ACCOUNT", "").strip()
    who = f"账号：{account}｜ " if account else ""
    idx = ["# AnyView 题库总目录", "",
           f"{who}抓取时间：{time.strftime('%Y-%m-%d')} ｜ "
           f"共 **{len(courses)} 门课 / {total} 题**", "", "---", ""]
    for cname, tname, cnt, rows in index_rows:
        idx += [f"## {cname}", "", f"> {tname} ｜ {cnt} 题", "",
                "| 章节 | 题数 | 文件 |", "|---|---:|---|"]
        for title, n, rel in rows:
            idx.append(f"| {title} | {n} | [{os.path.basename(rel)}]({rel}) |")
        idx.append("")
    idx += ["---", "", "单文件版：[全题库.md](全题库.md)　结构化数据：[题库.json](题库.json)", ""]
    open(os.path.join(OUT, "index.md"), "w", encoding="utf-8").write("\n".join(idx))

    open(os.path.join(OUT, "全题库.md"), "w", encoding="utf-8").write("\n".join(all_md))
    json.dump(flat, open(os.path.join(OUT, "题库.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    print(f"完成：{len(courses)} 门课，{total} 题 -> {OUT}")
    for cname, tname, cnt, rows in index_rows:
        print(f"  {cname} ({tname}) {cnt} 题")
        for title, n, _ in rows:
            print(f"    {title}: {n}")


if __name__ == "__main__":
    main()
