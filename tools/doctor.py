#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
零浏览器链路自检 —— doctor.py

只回答一个问题：
    这台机器现在能不能【完全不启动任何浏览器】，把题目抓下来、把答案提交上去？

只发只读 GET 请求，不写任何远端数据，跑多少次都安全。

用法:
    PY=~/.workbuddy/binaries/python/envs/default/bin/python
    $PY 工具/doctor.py
    $PY 工具/doctor.py -q      # 只输出结论行

退出码: 0 = 关键项全通过（可以干活）；1 = 有致命项（先按提示修）
"""
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import paths

HERE = Path(__file__).resolve().parent
ROOT = paths.data_root()
BASE = "https://anyview.gdut.edu.cn/api"
CURL = "/usr/bin/curl"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
REFERER = "https://anyview.gdut.edu.cn/student/exercises"

RESULTS = []


def rec(name, ok, detail="", fatal=False, warn=False):
    RESULTS.append({"name": name, "ok": ok, "fatal": fatal, "warn": warn})
    if warn and not ok:
        mark = "WARN"
    else:
        mark = "PASS" if ok else ("FAIL" if fatal else "WARN")
    print(f"  [{mark}] {name:26s} {detail}")


# ------------------------------------------------------------------ 本机环境

def check_python():
    v = sys.version_info
    rec("Python ≥ 3.8", v >= (3, 8), f"{v.major}.{v.minor}.{v.micro}", fatal=True)


def check_rsa():
    try:
        import rsa                                        # noqa: F401
        mod = sys.modules["rsa"]
        rec("rsa 库（登录用）", True, getattr(mod, "__version__", "installed"))
    except ImportError:
        rec("rsa 库（登录用）", False,
            "缺失 → 换解释器或按 SOP §3 装进隔离 venv", fatal=True)


def check_curl():
    p = CURL if os.path.exists(CURL) else shutil.which("curl")
    rec("curl（写接口通道）", bool(p), str(p or "未找到"), fatal=True)


def check_token():
    p = paths.token_path()
    if not p.exists():
        rec(".token", False, f"不存在于 {paths.token_path(writable=True)} → 先跑 login.py",
            fatal=True)
        return ""
    tk = p.read_text().strip()
    mode = oct(p.stat().st_mode)[-3:]
    rec(".token", bool(tk), f"长度 {len(tk)}，权限 {mode}")
    if mode != "600":
        rec(".token 权限", False, f"当前 {mode}，建议 chmod 600", warn=True)
    return tk


# ------------------------------------------------------------------ 网络通道

def curl_get(path, tk):
    cmd = [CURL, "-sS", "--max-time", "25",
           "-H", f"User-Agent: {UA}",
           "-H", "Accept: application/json, text/plain, */*",
           "-H", f"token: {tk}",
           "-H", f"Referer: {REFERER}",
           BASE + path]
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout


def check_curl_channel(tk):
    if not tk:
        rec("curl 业务通道", False, "无 token，跳过", fatal=True)
        return
    rc, out = curl_get("/scheme-service/scheme/list/student", tk)
    if rc != 0:
        rec("curl 业务通道", False, f"curl 退出码 {rc}", fatal=True)
        return
    try:
        j = json.loads(out)
    except Exception:
        rec("curl 业务通道", False,
            f"非 JSON（疑似 WAF 挑战页）: {out[:70]!r}", fatal=True)
        return
    code = j.get("code")
    data = j.get("data")
    n = len(data) if isinstance(data, list) else 0
    if code in (200, 0, 31):
        rec("curl 业务通道", True, f"code={code}，课程表 {n} 个")
    elif code in (401, 403):
        rec("curl 业务通道", False,
            f"code={code} {j.get('message')} → token 失效，重跑 login.py", fatal=True)
    else:
        rec("curl 业务通道", False, f"code={code} msg={j.get('message')}", fatal=True)


def check_urllib_channel(tk):
    """Python 原生 TLS 能不能过 WAF。过不了不影响干活（写接口都走 curl）。"""
    if not tk:
        rec("urllib 通道（可选）", False, "无 token，跳过")
        return
    req = urllib.request.Request(BASE + "/scheme-service/scheme/list/student",
                                 headers={"token": tk, "Referer": REFERER,
                                          "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
        rec("urllib 通道（可选）", j.get("code") in (200, 0, 31),
            f"code={j.get('code')}  crawl.py 可直接用")
    except urllib.error.HTTPError as e:
        rec("urllib 通道（可选）", False,
            f"HTTP {e.code} → 本机 Python TLS 被拦，抓题也改走 curl")
    except Exception as e:                                # noqa: BLE001
        rec("urllib 通道（可选）", False, f"{type(e).__name__}: {e}")


# ------------------------------------------------------------------ 本地产物

def check_data():
    q = ROOT / "题库" / "题库.json"
    if q.exists():
        try:
            n = len(json.loads(q.read_text(encoding="utf-8")))
            rec("题库.json", True, f"{n} 题")
        except Exception:
            rec("题库.json", False, "解析失败")
    else:
        rec("题库.json", False, "缺失 → 跑 crawl.py", fatal=True)

    src = ROOT / "答案" / "_src"
    files = sorted(src.glob("*.py")) if src.is_dir() else []
    rec("答案源码 _src", bool(files), f"{len(files)} 个文件")

    built = ROOT / "提交源码"
    n_c = len(list(built.glob("*.c"))) if built.is_dir() else 0
    rec("提交源码（拼装产物）", n_c > 0, f"{n_c} 个 .c")

    r = ROOT / "提交记录.json"
    if r.exists():
        try:
            blob = json.loads(r.read_text(encoding="utf-8"))
            ok = sum(1 for v in blob.values() if v.get("passed"))
            rec("提交记录.json", True, f"{len(blob)} 条，通过 {ok}")
        except Exception:
            rec("提交记录.json", False, "解析失败")
    else:
        rec("提交记录.json", False, "不存在（尚未提交过）", warn=True)


def main():
    quiet = "-q" in sys.argv
    if not quiet:
        print("AnyView 零浏览器链路自检")
        print(f"  根目录 {ROOT}")
        print(f"  解释器 {sys.executable}")
        print()

    check_python()
    if ROOT.exists():
        rec("数据根目录", True, str(ROOT))
    else:
        rec("数据根目录", False,
            f"{ROOT} 不存在 → 设 ANYVIEW_DATA_DIR", fatal=True)
    check_rsa()
    check_curl()
    tk = check_token()
    check_curl_channel(tk)
    check_urllib_channel(tk)
    if not quiet:
        print()
        print("本地产物")
    check_data()

    fatal = [r for r in RESULTS if not r["ok"] and r["fatal"]]
    warns = [r for r in RESULTS if not r["ok"] and not r["fatal"] and not r["warn"]]

    print()
    if fatal:
        print(f"结论：不可用 —— {len(fatal)} 个致命项")
        for r in fatal:
            print(f"  · {r['name']}")
        print("  浏览器不是必需项；按 SOP §3/§6 修上面几项即可。")
        return 1

    if warns:
        print(f"结论：可用，但 {len(warns)} 项需留意（不影响提交）")
        for r in warns:
            print(f"  · {r['name']}")
    else:
        print("结论：可用 —— 全链路零浏览器，可以抓题 / 拼装 / 提交")
    return 0


if __name__ == "__main__":
    sys.exit(main())
