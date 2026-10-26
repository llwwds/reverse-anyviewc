#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AnyView Stage 2: 补全每题的「学习进度」信息

接口: GET /api/scheme-service/scheme/list/student/detail/{eid}
      返回 question.difficulty / question.remark 与 exercise.exerciseStatus(是否已过) 等

写入 <数据根>/原始数据/q_<eid>.json 的 "progress" 字段（幂等，已存在则跳过）

通道说明：早期版本用 requests，本机 TLS 指纹会被 WZWS WAF 拦成 403，
现统一改走标准库 urllib；若某天 urllib 也被拦，照 SOP §6 换 curl 子进程。
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

import paths

BASE = "https://anyview.gdut.edu.cn/api"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
RAW = str(paths.raw_dir())
DELAY = 0.3


def get_json(url, token):
    req = urllib.request.Request(url, headers={
        "Accept": "application/json, text/plain, */*",
        "token": token,
        "User-Agent": UA,
        "Referer": "https://anyview.gdut.edu.cn/student/exercises",
    })
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def main():
    token = paths.read_token()
    if not token:
        sys.exit(f"缺少 token：设 ANYVIEW_TOKEN，或先跑 login.py 写到 "
                 f"{paths.token_path(writable=True)}")

    if not os.path.isdir(RAW):
        sys.exit(f"原始数据目录不存在：{RAW}\n先跑 crawl.py（SOP §4 S2）")

    files = sorted(f for f in os.listdir(RAW) if f.startswith("q_") and f.endswith(".json"))
    print(f"共 {len(files)} 个题目文件")
    ok = skip = fail = 0
    for i, fn in enumerate(files, 1):
        fp = os.path.join(RAW, fn)
        with open(fp, encoding="utf-8") as f:
            obj = json.load(f)
        if obj.get("progress"):
            skip += 1
            continue
        eid = fn[2:-5]
        try:
            j = get_json(f"{BASE}/scheme-service/scheme/list/student/detail/{eid}", token)
            data = (j or {}).get("data") or {}
        except Exception as e:  # noqa: BLE001
            print(f"  [fail] {eid} {e!r}")
            fail += 1
            continue
        q = data.get("question") or {}
        ex = data.get("exercise") or {}
        obj["progress"] = {
            "difficulty": q.get("difficulty"),
            "officialName": q.get("name"),
            "remark": q.get("remark"),
            "updateTime": q.get("updateTime"),
            "exerciseStatus": ex.get("exerciseStatus"),
            "score": ex.get("score"),
            "firstPastTime": ex.get("firstPastTime"),
            "lastPastTime": ex.get("lastPastTime"),
            "accumTime": ex.get("accumTime"),
            "cmpCount": ex.get("cmpCount"),
            "cmpErrorCount": ex.get("cmpErrorCount"),
            "runErrCount": ex.get("runErrCount"),
            "maximumSimilarity": ex.get("maximumSimilarity"),
            "createTime": ex.get("createTime"),
        }
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        ok += 1
        if i % 25 == 0:
            print(f"  {i}/{len(files)}")
        time.sleep(DELAY)

    print(f"完成: 新增 {ok}, 已有 {skip}, 失败 {fail}")


if __name__ == "__main__":
    main()
