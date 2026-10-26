#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AnyView 单题「保存 / 编译 / 运行判题 / 查状态」——纯 HTTP 无头提交，不需要浏览器。

接口（逆向自 /student/js/student76d0ea40.js，再用 CDP 抓真实请求核对过）:

  GET  /api/scheme-service/scheme/list/student/{eid}
       题干 + stuCode(base64) + kind + language + questionFullName
  GET  /api/scheme-service/scheme/list/student/detail/{eid}
       判题状态（exercise.exerciseStatus / firstPastTime / runRightCount ...）
  POST /api/gdb-service/exercise/saveStudentCode
       保存代码。application/json，body {eID, studentCode(base64)}
  POST /api/gdb-service/code/compile
       编译 + 参考数据比对。application/x-www-form-urlencoded
       part 名是 questionRes.questionFullName（长名，形如 第1章-DC01PE03e）
  POST /api/gdb-service/code/runGroup
       运行 + 判题。multipart/form-data  ← 写 exerciseStatus=2 的唯一入口
       part 名是 questionFullName（短名，形如 DC01PE03e）
       必带 kind / language / isTeacher / stuCode(base64)

三个写接口的 Content-Type 各不相同。弄错时网关回的是 404 而不是 415，
非常容易被误判成「路由不存在」——踩过这个坑，别再踩。

WAF 说明: 本机 requests 的 TLS 指纹被 WZWS 拦截（403），所以 HTTP 层统一走
系统 curl 子进程。curl 正常。

用法:
  python submit.py --eid 11875510 --status            # 只看判题状态
  python submit.py --eid 11875510 --run               # 用题干模板跑一次判题
  python submit.py --eid 11875510 --all               # 保存 + 编译 + 判题
  python submit.py --eid 11875510 --all --file a.c    # 用本地文件
  python submit.py --eid 11875510 --dry               # 只打印将要提交的代码
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile

import paths

BASE = "https://anyview.gdut.edu.cn/api"
CURL = "/usr/bin/curl"


# ---------------------------------------------------------------- HTTP (curl)

def token():
    return paths.require_token()


UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def call(method, path, tk, eid=None, json_body=None, form=None, multipart=None):
    """统一出口：拼 curl 参数、解析 JSON、检查 HTTP 状态。"""
    args = ["-X", method, f"{BASE}{path}", "-H", f"token: {tk}",
            "-w", "\n__HTTP__%{http_code}", "-o", "-"]
    if eid:
        args += ["-H", f"Eid: {eid}"]
    tmp = None
    if json_body is not None:
        args += ["-H", "Content-Type: application/json", "--data-binary", "@-"]
    elif form is not None:
        for k, v in form.items():
            args += ["--data-urlencode", f"{k}={v}"]
    elif multipart is not None:
        for k, v in multipart.items():
            if k == "stuCode" and len(v) > 512:
                # 长 base64 走临时文件，避开命令行长度/转义问题
                fd, tmp = tempfile.mkstemp()
                with os.fdopen(fd, "w") as f:
                    f.write(v)
                args += ["-F", f"{k}=<{tmp}"]
            else:
                args += ["-F", f"{k}={v}"]

    cmd = [CURL, "-sS", "--max-time", "180",
           "-H", f"User-Agent: {UA}",
           "-H", "Accept: application/json, text/plain, */*",
           "-H", "Origin: https://anyview.gdut.edu.cn",
           "-H", "Referer: https://anyview.gdut.edu.cn/student/exercises"] + args
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           input=json.dumps(json_body) if json_body is not None else None)
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)
    if p.returncode != 0:
        sys.exit(f"[curl] {p.stderr.strip()}")

    raw = p.stdout
    parts = raw.rsplit("__HTTP__", 1)
    body = parts[0]
    status = int(parts[1].strip()) if len(parts) == 2 and parts[1].strip().isdigit() else 0
    try:
        j = json.loads(body)
    except Exception:
        j = {"_raw": body[:400]}
    return status, j


# ---------------------------------------------------------------- 业务

def get_question(tk, eid):
    st, j = call("GET", f"/scheme-service/scheme/list/student/{eid}", tk)
    d = j.get("data")
    if not isinstance(d, dict):
        sys.exit(f"取题失败 HTTP={st} code={j.get('code')} msg={j.get('message')}")
    return d


def get_status(tk, eid):
    st, j = call("GET", f"/scheme-service/scheme/list/student/detail/{eid}", tk)
    return ((j.get("data") or {}).get("exercise")) or {}


def short_name(full):
    """第1章-DC01PE03e -> DC01PE03e"""
    return re.sub(r"^第\d+章[-－]", "", full or "")


def save_code(tk, eid, code_b64):
    st, j = call("POST", "/gdb-service/exercise/saveStudentCode", tk, eid,
                 json_body={"eID": str(eid), "studentCode": code_b64})
    return st, j


def compile_code(tk, eid, code_b64, full_name):
    st, j = call("POST", "/gdb-service/code/compile", tk, eid, form={
        "eID": str(eid),
        "stuCode": code_b64,
        "isTeacher": "false",
        "isDebug": "false",
        "questionRes.questionFullName": full_name,
    })
    return st, j


def run_group(tk, eid, code_b64, short, kind, language):
    st, j = call("POST", "/gdb-service/code/runGroup", tk, eid, multipart={
        "questionFullName": short,
        "eID": str(eid),
        "isTeacher": "false",
        "kind": str(kind),
        "language": str(language),
        "stuCode": code_b64,
    })
    return st, j


def show(e):
    if not e:
        print("  (拿不到状态)")
        return
    st = e.get("exerciseStatus")
    label = {0: "未做", 1: "已作答", 2: "已通过"}.get(st, "?")
    print(f"  exerciseStatus = {st} ({label})")
    print(f"  firstPastTime  = {e.get('firstPastTime')}")
    print(f"  lastPastTime   = {e.get('lastPastTime')}")
    print(f"  cmp  = {e.get('cmpRightCount')}/{e.get('cmpCount')}  err={e.get('cmpErrorCount')}"
          f"   runRight={e.get('runRightCount')}  runErr={e.get('runErrCount')}")


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eid", required=True)
    ap.add_argument("--file", help="用本地文件内容代替题干模板")
    ap.add_argument("--status", action="store_true", help="只看状态")
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--compile", action="store_true")
    ap.add_argument("--run", action="store_true", help="运行判题（写 exerciseStatus=2）")
    ap.add_argument("--all", action="store_true", help="save + compile + run")
    ap.add_argument("--dry", action="store_true", help="只打印代码")
    a = ap.parse_args()

    if a.all:
        a.save = a.compile = a.run = True

    tk = token()
    det = get_question(tk, a.eid)

    full_name = det.get("questionFullName") or ""
    short = short_name(full_name)
    kind = det.get("kind") or 3
    language = det.get("language") or 1
    stu_raw = det.get("stuCode") or ""

    if a.file:
        code = open(a.file, encoding="utf-8").read()
        src = a.file
    else:
        code = base64.b64decode(stu_raw).decode("utf-8", "replace") if stu_raw else ""
        src = "题干模板"

    print(f"[题] {full_name}   eid={a.eid}   kind={kind}   language={language}")
    print(f"[源] {src}   {len(code)} 字符")
    print("[前]")
    show(get_status(tk, a.eid))

    if a.dry:
        print("-" * 60)
        print(code)
        return

    if a.status and not (a.save or a.compile or a.run):
        return

    if not code.strip():
        sys.exit("!! 代码为空，拒绝提交（防止把已存代码清空）")

    code_b64 = base64.b64encode(code.encode()).decode()

    if a.save:
        st, j = save_code(tk, a.eid, code_b64)
        print(f"[保存] HTTP {st}  code={j.get('code')} {j.get('message')}")

    if a.compile:
        st, j = compile_code(tk, a.eid, code_b64, full_name)
        d = j.get("data") or {}
        right, total = d.get("cmpRightCount"), d.get("cmpCount")
        cmp_txt = f"{right}/{total}" if total is not None else f"{right}"
        print(f"[编译] HTTP {st}  code={j.get('code')} "
              f"result={(d.get('result') or '').strip()[:60]!r} "
              f"cmp={cmp_txt} err={d.get('cmpErrorCount')}")

    if a.run:
        st, j = run_group(tk, a.eid, code_b64, short, kind, language)
        d = j.get("data") or {}
        if d:
            print(f"[判题] HTTP {st}  code={j.get('code')}  passed={d.get('passed')}  "
                  f"runRight={d.get('runRightCount')}  runErr={d.get('runErrCount')}  "
                  f"end={d.get('end')}")
            errs = d.get("errorOrder") or {}
            if errs:
                print(f"  未通过数据: {list(errs.keys())[:10]}")
            out = re.sub(r"<[^>]+>", "", d.get("output") or "").replace("&nbsp;", " ")
            tail = out.strip().splitlines()[-3:]
            if tail:
                print("  输出尾部: " + " | ".join(t.strip() for t in tail if t.strip()))
        else:
            print(f"[判题] HTTP {st}  code={j.get('code')} {j.get('message')}  {str(j)[:200]}")

    print("[后]")
    show(get_status(tk, a.eid))


if __name__ == "__main__":
    main()
