"""统计题库模板的形态分布，用于设计拼装规则。

用法: python tools/scan_templates.py
"""
import base64
import collections
import json
import re
from pathlib import Path

import paths

ROOT = paths.data_root()
CTRL = {"if", "for", "while", "switch", "return", "else", "do", "case"}


def decode_template(eid):
    """取原始数据里某题的模板明文（base64 解码）。"""
    p = ROOT / "原始数据" / f"q_{eid}.json"
    if not p.exists():
        return None
    blob = json.loads(p.read_text(encoding="utf-8"))
    det = blob.get("detail") or {}
    b64 = det.get("stuCode") or ""
    return base64.b64decode(b64).decode("utf-8", "replace") if b64 else ""


def count_sig_lines(src):
    """粗数模板里出现的函数签名（行首类型 + 名字 + 参数 + 行尾）。"""
    pat = r"^[A-Za-z_][\w \t*&]*?\b(\w+)\s*\([^;{}]*\)\s*(?://[^\n]*)?$"
    names = re.findall(pat, src, re.M)
    return [n for n in names if n not in CTRL]


def main():
    questions = json.loads((ROOT / "题库" / "题库.json").read_text(encoding="utf-8"))
    stat = collections.Counter()
    no_anchor = []
    multi = []
    empty = []

    for q in questions:
        eid = str(q["eid"])
        src = decode_template(eid)
        if src is None:
            stat["MISSING"] += 1
            continue
        if not src.strip():
            stat["EMPTY"] += 1
            empty.append((eid, q["pname"]))
            continue

        has_main = ("int main" in src) or ("void main" in src)
        has_anchor = "Add your code here" in src
        stat["HAS_MAIN" if has_main else "NO_MAIN"] += 1
        stat["ANCHOR" if has_anchor else "NO_ANCHOR"] += 1

        sigs = count_sig_lines(src)
        stat[f"SIG_{min(len(sigs), 5)}"] += 1
        if len(sigs) >= 3:
            multi.append((eid, q["pname"], len(sigs)))

        if not has_anchor and not has_main:
            head = src.strip().splitlines()[0] if src.strip() else ""
            no_anchor.append((eid, q["pname"], head[:70]))

    print("=== 统计 ===")
    for k in sorted(stat):
        print(f"  {k:12s} {stat[k]}")

    print(f"\n=== 空模板 ({len(empty)}) ===")
    for x in empty[:10]:
        print("  ", x)

    print(f"\n=== 无锚点也无 main ({len(no_anchor)}) ===")
    for x in no_anchor[:15]:
        print("  ", x)

    print(f"\n=== 模板内签名 >=3 ({len(multi)}) ===")
    for x in multi[:12]:
        print("  ", x)


if __name__ == "__main__":
    main()
