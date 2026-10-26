# local-data —— 本地数据目录

本目录是 reverse-anyviewc 的**运行期数据根**，只放数据，不放代码。

> 该目录被 `.gitignore` 整体排除（`local-data/*`），**只有本文件随仓库分发**。
> 也就是说：clone 下来你会看到这个 README 和空目录，数据得自己跑脚本生成。

## 为什么放在仓库里而不是仓库外

数据跟着代码走，路径就不用跨盘符猜，`paths.py` 的默认值直接命中；同时靠 `.gitignore` 保证它永不入库。两件事不冲突。

## 目录约定

| 路径 | 内容 | 由谁生成 |
|---|---|---|
| `.token` | 登录 token（权限 600） | `tools/login.py` |
| `原始数据/` | 每题原始 JSON：`q_<eid>.json`、`catalog_*.json`、`schemes.json`、`_manifest.json` | `tools/crawl.py` |
| `题库/` | 渲染后的 Markdown 题库与 `题库.json` | `tools/render.py` |
| `答案/` | `_src/*.py`（答案权威源）、`answers.json`、校验报告 | `tools/answers_build.py` |
| `提交源码/` | 拼装产物 152 份 `.c`，可逐题审阅 | `tools/assemble.py --all` |
| `提交记录.json` | 提交台账，支持断点续跑 | `tools/exam_drive.py` |
| `技术文档.md` | 完整技术档案：逆向过程、失败尝试、接口契约、答案拼装规则 | 手写 |
| `README.md` | 本文件（唯一入库项） | 手写 |

## 红线

- **`.token`、`原始数据/`、`题库/`、`答案/`、`提交源码/`、`提交记录.json`、`技术文档.md` 一律不入库。**
- 两道防线：
  1. `.gitignore` 的 `local-data/*` —— 拦常规 `git add`；
  2. `.githooks/pre-commit` —— 拦 `git add -f`，任何非 README 文件进暂存区都会让提交失败。
     仓库根执行一次 `git config core.hooksPath .githooks` 启用。
- 提交前自查：
  ```bash
  git status --short          # local-data/ 下除 README.md 外不应出现任何文件
  git check-ignore -v local-data/答案/_src/ch01.py   # 应命中 local-data/* 规则
  ```
- 若把数据根改到仓库外（`ANYVIEW_DATA_DIR`），`.gitignore` 里另有 `题库/ 答案/ 原始数据/ 提交源码/ 提交记录.json .token` 兜底。

## 用法

```bash
cd <仓库根>
export PY=<能 import rsa 的 python>
$PY tools/doctor.py          # 自检，会打印实际解析到的数据根
```

数据根解析顺序（见 `tools/paths.py`）：

```
ANYVIEW_DATA_DIR  →  <仓库根>/local-data  →  ~/Downloads/AnyView题库（旧位置兼容）  →  兜底 <仓库根>/local-data
```

## 相关文档

| 文档 | 位置 |
|---|---|
| 零浏览器 SOP | `docs/SOP-无浏览器.md` |
| 接口契约 | `docs/接口契约.md` |
| 完整技术档案（含题库答题细节，不入库） | 本目录 `技术文档.md` |
