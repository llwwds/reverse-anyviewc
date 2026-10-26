#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据根定位 —— 让「代码」与「数据」解耦。

本仓库只放代码。题库、答案、提交记录这些运行期数据留在你自己的数据目录里，
不进仓库（见 .gitignore）。所有脚本统一从这里取路径，不要在别处硬编码。

解析顺序：
    ANYVIEW_DATA_DIR 环境变量
    → 默认 ~/Downloads/AnyView题库

token 解析顺序：
    ANYVIEW_TOKEN 环境变量
    → <数据根>/.token
    → <数据根>/工具/.token（兼容旧布局）

用法:
    from paths import data_root, token_path
    OUT = data_root() / "原始数据"
"""
import os
from pathlib import Path

DEFAULT_DATA_ROOT = Path.home() / "Downloads" / "AnyView题库"

# 数据根下的约定子路径
RAW_DIR = "原始数据"
BANK_DIR = "题库"
ANSWER_DIR = "答案"
SRC_DIR = "答案/_src"
BUILD_DIR = "提交源码"
RECORD_FILE = "提交记录.json"


def data_root() -> Path:
    """运行期数据目录。不存在时各脚本自行决定是建还是报错。"""
    env = os.environ.get("ANYVIEW_DATA_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return DEFAULT_DATA_ROOT


def token_path(writable: bool = False) -> Path:
    """token 文件位置。

    writable=True 时（login.py 写 token）优先落在数据根，
    避免和只读探测走不同分支。
    """
    root = data_root()
    if writable:
        return root / ".token"
    for p in (root / ".token", root / "工具" / ".token"):
        if p.exists():
            return p
    return root / ".token"


def read_token() -> str:
    """先环境变量，再文件。没有就返回空串，由调用方决定怎么报错。"""
    env = os.environ.get("ANYVIEW_TOKEN", "").strip()
    if env:
        return env
    p = token_path()
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    return ""


def require_token() -> str:
    tk = read_token()
    if not tk:
        raise SystemExit(
            "缺少 token：\n"
            f"  方式一  ANYVIEW_TOKEN=<token> 环境变量\n"
            f"  方式二  放到 {token_path(writable=True)}\n"
            "  获取方式见 docs/SOP-无浏览器.md 的 S1 阶段（tools/login.py）"
        )
    return tk


# ------------------------------------------------------------ 便捷路径

def raw_dir() -> Path:
    return data_root() / RAW_DIR


def bank_json() -> Path:
    return data_root() / BANK_DIR / "题库.json"


def src_dir() -> Path:
    return data_root() / SRC_DIR


def build_dir() -> Path:
    return data_root() / BUILD_DIR


def record_file() -> Path:
    return data_root() / RECORD_FILE
