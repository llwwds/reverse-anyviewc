#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AnyView 登录 -> 拿 token

密码用平台公钥 RSA/PKCS1v15 加密，明文格式: password + "," + 毫秒时间戳
公钥接口返回裸 base64 DER(SPKI)，要自己补 PEM 头尾。

注意
  - 本机 requests 的 TLS 指纹会被 WZWS WAF 拦成 403，所以 HTTP 一律走系统 curl。
  - 同一账号重复登录会让**旧的 token 立即失效**（单点登录）。在浏览器里登录一次，
    脚本里的 .token 就废了；反之亦然。遇到「当前用户状态失效」重跑本脚本即可。

用法:
    ANYVIEW_USER=学号 ANYVIEW_PASS=密码 python3 login.py
    # 或者把账号密码写进 <数据根>/.cred（两行式，或 user=/pass=），直接 python3 login.py
仅把 token 写入数据根的 .token（权限 600），不落盘账号密码。
数据根位置见 paths.py（默认 ~/Downloads/AnyView题库，可用 ANYVIEW_DATA_DIR 覆盖）。
"""
import base64
import json
import os
import subprocess
import sys
import time

import rsa

import paths

BASE = "https://anyview.gdut.edu.cn/api"
CURL = "/usr/bin/curl"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def http(args, data=None):
    """跑 curl，返回解析后的 JSON。"""
    cmd = [CURL, "-sS", "--max-time", "60",
           "-H", f"User-Agent: {UA}",
           "-H", "Accept: application/json, text/plain, */*",
           "-H", "Origin: https://anyview.gdut.edu.cn",
           "-H", "Referer: https://anyview.gdut.edu.cn/student/login"] + args
    p = subprocess.run(cmd, capture_output=True, text=True, input=data)
    if p.returncode != 0:
        sys.exit(f"[curl] {p.stderr.strip()}")
    try:
        return json.loads(p.stdout)
    except Exception:
        sys.exit(f"[!] 返回非 JSON: {p.stdout[:300]}")


def pem_from_der_b64(b64):
    body = "\n".join(b64[i:i + 64] for i in range(0, len(b64), 64))
    return f"-----BEGIN PUBLIC KEY-----\n{body}\n-----END PUBLIC KEY-----"


def read_cred_file():
    """从 <数据根>/.cred 读账号密码（该文件已被 .gitignore 掉，权限 600）。

    格式二选一，怎么顺手怎么来：

        # 两行式
        3122xxxxxx
        你的密码

        # 或 key=value 式
        user=3122xxxxxx
        pass=你的密码

    加这个入口纯粹是为了省掉每次 `ANYVIEW_USER=... ANYVIEW_PASS=...` 的长命令行；
    环境变量优先级更高，两者都有时以环境变量为准。
    """
    p = paths.data_root() / ".cred"
    if not p.exists():
        return "", ""
    user = pwd = ""
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if "=" in s:
                k, v = s.split("=", 1)
                k = k.strip().lower()
                if k in ("user", "username", "学号", "账号"):
                    user = v.strip()
                elif k in ("pass", "password", "密码"):
                    pwd = v.rstrip("\n")
                continue
            if not user:
                user = s
            elif not pwd:
                pwd = s
    except OSError:
        return "", ""
    return user, pwd


def main():
    user = os.environ.get("ANYVIEW_USER", "").strip()
    pwd = os.environ.get("ANYVIEW_PASS", "")
    if not user or not pwd:
        fu, fp = read_cred_file()
        user = user or fu
        pwd = pwd or fp
        if user and pwd:
            print(f"[0/3] 账号密码取自 {paths.data_root() / '.cred'}")
    if not user or not pwd:
        sys.exit("需要 ANYVIEW_USER / ANYVIEW_PASS 环境变量，或 <数据根>/.cred 文件")

    # 1. 公钥
    j = http([f"{BASE}/user-service/rsa"])
    key_b64 = (j.get("data") or "")
    if not key_b64:
        sys.exit(f"[!] 拿不到公钥: {j}")
    pub = rsa.PublicKey.load_pkcs1_openssl_pem(pem_from_der_b64(key_b64).encode())
    print(f"[1/3] 公钥 OK  ({pub.n.bit_length()} bit)")

    # 2. 学校列表 -> 广东工业大学
    j = http([f"{BASE}/user-service/school/listAll"])
    schools = j.get("data") or []
    target = next((sc for sc in schools if "广东工业大学" in str(sc.get("name", ""))), None)
    if target is None:
        cands = [sc for sc in schools if "工业" in str(sc.get("name", ""))]
        target = cands[0] if cands else None
    if target is None:
        sys.exit(f"[!] 未找到广东工业大学，可选: {[sc.get('name') for sc in schools][:30]}")
    print(f"[2/3] 学校: {target['name']} (id={target['id']})")

    # 3. 加密密码并登录
    plain = f"{pwd},{int(time.time() * 1000)}"
    enc = base64.b64encode(rsa.encrypt(plain.encode(), pub)).decode()
    form = (f"roleId=3&username={user}&schoolId={target['id']}"
            f"&password={requests_quote(enc)}")
    j = http(["-X", "POST", f"{BASE}/user-service/login",
              "-H", "Content-Type: application/x-www-form-urlencoded",
              "--data-binary", "@-"], data=form)

    if j.get("code") != 200:
        sys.exit(f"[!] 登录失败 code={j.get('code')} msg={j.get('message')}")

    d = j.get("data") or {}
    token = d.get("token") or ""
    stu = d.get("student") or {}
    if not token:
        sys.exit(f"[!] 响应里没有 token: {list(d.keys())}")

    print(f"[3/3] 登录成功: {stu.get('name', '')} (id={stu.get('id')})")
    p = paths.token_path(writable=True)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        f.write(token)
    os.chmod(p, 0o600)
    print(f"TOKEN_LEN={len(token)}  (已写入 {p}, 权限 600)")


def requests_quote(s):
    from urllib.parse import quote
    return quote(s, safe="")


if __name__ == "__main__":
    main()
