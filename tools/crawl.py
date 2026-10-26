#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AnyView (anyview.gdut.edu.cn) 题目抓取器 —— Stage 1: 原始数据
用法:
    ANYVIEW_TOKEN=xxxx python3 crawl.py
    python3 crawl.py --token xxxx
输出:
    out/raw/schemes.json              课程/作业表列表
    out/raw/catalog_<schemeId>_<classId>.json   每门课的题目目录
    out/raw/q_<eid>.json              单题详情
    out/raw/_manifest.json            汇总索引
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import paths

BASE = "https://anyview.gdut.edu.cn/api"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
OUT = str(paths.raw_dir())
DELAY = 0.35  # 温和限速，避免给学校服务器压力


def api(path, token, params=None, retry=3):
    url = BASE + path
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        if clean:
            url += "?" + urllib.parse.urlencode(clean)
    req = urllib.request.Request(url, headers={
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "token": token,
        "Referer": "https://anyview.gdut.edu.cn/student/exercises",
        "User-Agent": UA,
    })
    last = None
    for i in range(retry):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read().decode("utf-8", "replace")
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return {"_raw": body}
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code in (401, 403):
                try:
                    msg = json.loads(body).get("message", "")
                except Exception:
                    msg = body[:200]
                sys.exit(f"[!] 鉴权失败 ({e.code}): {msg}\n    请重新获取 token。")
            last = f"HTTP {e.code}: {body[:200]}"
        except Exception as e:  # noqa: BLE001
            last = repr(e)
        time.sleep(1.2 * (i + 1))
    print(f"    [warn] 请求失败 {path} -> {last}")
    return None


def unwrap(resp):
    """后端统一返回 {code,data,message}，取 data。"""
    if resp is None:
        return None
    if isinstance(resp, dict) and "code" in resp:
        if resp.get("code") not in (200, 0):
            print(f"    [warn] code={resp.get('code')} msg={resp.get('message')}")
            return resp.get("data")
        return resp.get("data")
    return resp


def save(obj, name):
    p = os.path.join(OUT, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default=paths.read_token())
    ap.add_argument("--only-scheme", default="", help="只抓某个 schemeId")
    args = ap.parse_args()
    token = args.token.strip()
    if not token:
        sys.exit("缺少 token：ANYVIEW_TOKEN=xxx python3 crawl.py、--token xxx，"
                 f"或先跑 login.py 写到 {paths.token_path(writable=True)}")

    os.makedirs(OUT, exist_ok=True)

    print("[1/3] 拉取课程 / 作业表列表 ...")
    schemes = unwrap(api("/scheme-service/scheme/list/student", token)) or []
    if not isinstance(schemes, list):
        sys.exit(f"课程列表结构异常: {type(schemes)}")
    save(schemes, "schemes.json")
    print(f"    -> {len(schemes)} 个课程/作业表")
    for s in schemes:
        print(f"       id={s.get('id')} classId={s.get('classId')} "
              f"{s.get('courseName')} / {s.get('name')} 题数={s.get('totalExerciseNum')}")

    manifest = {"schemes": [], "questions": [], "errors": []}

    for s in schemes:
        # 后端字段：课程/作业表主键是 id，不是 schemeId
        sid = s.get("schemeId") or s.get("id")
        cid = s.get("classId")
        if args.only_scheme and str(sid) != args.only_scheme:
            continue
        print(f"[2/3] 目录: {s.get('courseName')} / {s.get('name')} (id={sid})")
        cat = unwrap(api("/scheme-service/catalog/list", token,
                         {"schemeId": sid, "classId": cid})) or []
        save(cat, f"catalog_{sid}_{cid}.json")
        print(f"    -> {len(cat)} 道题")
        time.sleep(DELAY)

        for q in cat:
            eid = q.get("eid")
            if eid is None:
                continue
            fp = os.path.join(OUT, f"q_{eid}.json")
            if os.path.exists(fp):
                with open(fp, encoding="utf-8") as f:
                    detail = json.load(f)
            else:
                print(f"    题目 eid={eid} {q.get('pname', '')}")
                detail = unwrap(api(f"/scheme-service/scheme/list/student/{eid}", token))
                if detail is None:
                    manifest["errors"].append({"schemeId": sid, "eid": eid,
                                               "reason": "detail 拉取失败"})
                    continue
                save({"catalog": q, "detail": detail}, f"q_{eid}.json")
                time.sleep(DELAY)
            manifest["questions"].append({
                "schemeId": sid, "classId": cid,
                "courseName": s.get("courseName"), "tableName": s.get("name"),
                "eid": eid, "chapName": q.get("chapName"),
                "pname": q.get("pname"), "number": q.get("number"),
                "questionType": q.get("questionType"),
            })
        manifest["schemes"].append({**s, "questionCount": len(cat)})

    save(manifest, "_manifest.json")
    print(f"[3/3] 完成：{len(manifest['questions'])} 道题，"
          f"{len(manifest['errors'])} 个失败 -> {OUT}")


if __name__ == "__main__":
    main()
