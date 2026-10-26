# reverse-anyviewc

> 对 AnyViewC 在线判题平台（`anyview.gdut.edu.cn`）的逆向分析与**零浏览器自动化**工具链。

这个仓库记录一件事：**如何在不启动任何浏览器的情况下，把一个 SPA 判题平台的完整答题流程做成可脚本化、可复现的 SOP。**

它不是一堆能跑的脚本的集合，而是一份**方法论 + 可执行验证**：每个结论都有实测支撑，每处踩过的坑都留在文档里。

---

## 目录

- [解决什么问题](#解决什么问题)
- [核心结论](#核心结论)
- [仓库布局](#仓库布局)
- [快速开始](#快速开始)
- [工具一览](#工具一览)
- [关键发现](#关键发现)
- [文档](#文档)
- [边界与免责](#边界与免责)

---

## 解决什么问题

在线判题平台的前端是 Vue 3 SPA，所有业务都走 `/api` 网关。想把它自动化，通常有两条路：

| 路线 | 做法 | 问题 |
|---|---|---|
| 浏览器自动化 | Playwright / Puppeteer / CDP 驱动真实页面 | 慢、脆、依赖浏览器版本，且要维护选择器 |
| 接口直连 | 逆向出 HTTP 契约，直接发请求 | 需要先跨过「怎么发现契约」这道坎 |

本项目走第二条，并把第一步（**发现**）的产物固化成代码常量，从而让浏览器彻底退出运行时。

## 核心结论

**全链路零浏览器依赖。**

| 环节 | 通道 | 依赖浏览器 |
|---|---|---|
| 登录取 token | curl 子进程 | 否 |
| 抓题目录 / 题干 | Python `urllib` | 否 |
| 答案生成 / 源码拼装 | 无网络 | 否 |
| 保存 / 编译 / 运行判题 | curl 子进程 | 否 |
| 批量拟人化驱动 | curl（复用 submit） | 否 |
| 环境自检 | curl + urllib | 否 |

代码级证据：

```bash
grep -rln "ego\|browser\|page\.\|cdp\|playwright\|puppeteer" --include="*.py" . | wc -l
# → 0
```

浏览器在这个项目里只干过两件**非运行时**的事：用 CDP 抓真实请求做逆向、在 WAF 拦死时当应急隧道。两件都已被「写成常量」化解 —— 详见 [docs/SOP-无浏览器.md](docs/SOP-无浏览器.md) §2。

## 仓库布局

```
reverse-anyviewc/
├── README.md
├── docs/
│   ├── SOP-无浏览器.md      # 主体：五阶段 SOP + 依赖边界 + 降级链
│   └── 接口契约.md          # 三个写接口的 Content-Type / 字段 / 响应
├── tools/
│   ├── paths.py             # 数据根定位（代码与数据解耦的核心）
│   ├── login.py             # RSA 登录 → token
│   ├── crawl.py             # 抓目录与题干
│   ├── enrich.py            # 补每题学习进度
│   ├── render.py            # 渲染成 Markdown 题库
│   ├── scan_templates.py    # 统计模板形态
│   ├── assemble.py          # 模板骨架 + 答案实现 → 可提交源码
│   ├── answers_build.py     # 批量生成答案文件
│   ├── submit.py            # 保存 / 编译 / 判题（单题）
│   ├── exam_drive.py        # 批量拟人化驱动
│   └── doctor.py            # 环境自检
└── .gitignore
```

**本仓库不含**：账号凭据、题库原文、课程答案、提交记录。这些是运行期数据，放在你自己的数据目录（默认 `~/Downloads/AnyView题库`，可用 `ANYVIEW_DATA_DIR` 覆盖）。

## 快速开始

```bash
git clone <本仓库地址> reverse-anyviewc && cd reverse-anyviewc

# 1. 准备解释器（登录需要 rsa）
python3 -m venv ~/.venvs/anyview
~/.venvs/anyview/bin/pip install rsa
export PY=~/.venvs/anyview/bin/python

# 2. 指向你的数据目录
export ANYVIEW_DATA_DIR=~/Downloads/AnyView题库

# 3. 自检 —— 退出码 0 就能干活
$PY tools/doctor.py

# 4. 登录（token 落在 $ANYVIEW_DATA_DIR/.token）
ANYVIEW_USER=<学号> ANYVIEW_PASS='<密码>' $PY tools/login.py

# 5. 抓题
$PY tools/crawl.py

# 6. 拼装 + 提交
$PY tools/assemble.py --all --out-dir "$ANYVIEW_DATA_DIR/提交源码"
$PY tools/submit.py --eid <eid> --all
$PY tools/exam_drive.py --plan          # 批量前先看计划
```

完整流程与失败处置见 [docs/SOP-无浏览器.md](docs/SOP-无浏览器.md)。

## 工具一览

| 工具 | 作用 | 网络 |
|---|---|---|
| `paths.py` | 统一解析数据根与 token 位置 | 无 |
| `login.py` | RSA/PKCS1v15 加密登录，写 token（权限 600） | curl |
| `crawl.py` | 拉课程表 → 题目目录 → 逐题详情，增量可重跑 | urllib |
| `enrich.py` | 补 `difficulty` / `exerciseStatus` / `accumTime` 等 | urllib |
| `render.py` | 渲染成 `index.md` / `全题库.md` / `题库.json` | 无 |
| `scan_templates.py` | 统计模板形态分布，用于设计拼装规则 | 无 |
| `assemble.py` | 按函数名替换函数体，辅助函数前置插入 | 无 |
| `answers_build.py` | 批量产出答案文件并做语法校验 | 无 |
| `submit.py` | 保存 / 编译 / 判题三件套 + 状态查询 | curl |
| `exam_drive.py` | 按拟人节奏批量跑，台账支持断点续跑 | curl |
| `doctor.py` | 10 项环境自检，只读 | curl + urllib |

## 关键发现

三个写接口的 Content-Type **各不相同**，弄错时网关回的是 `404` 而不是 `415` —— 极易被误判成「路由不存在」：

| 接口 | Content-Type | 关键字段 |
|---|---|---|
| `POST /api/gdb-service/exercise/saveStudentCode` | `application/json` | `eID`、`studentCode`(base64) |
| `POST /api/gdb-service/code/compile` | `application/x-www-form-urlencoded` | `questionRes.questionFullName`（**长名**） |
| `POST /api/gdb-service/code/runGroup` | `multipart/form-data` | `questionFullName`（**短名**）、`kind`、`language`、`stuCode`，另需 `Eid` 头 |

判题成功的唯一判据是 `runGroup` 返回 `{"code":200,"data":{"passed":true}}`。前端**没有**「标记通过」接口，工具栏也**没有**「提交」按钮 —— 只有编译 / 运行 / 调试 / 保存。

其余坑（保存异步落库延迟、16 位整型环境、平台 `allinclude.h` 自带 typedef、WAF TLS 指纹、单点登录互踢）逐条记在 [docs/接口契约.md](docs/接口契约.md) 与 SOP 的故障排查表里。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/SOP-无浏览器.md](docs/SOP-无浏览器.md) | 依赖边界矩阵、五阶段 SOP、WAF 降级链、反检测红线、检查清单 |
| [docs/接口契约.md](docs/接口契约.md) | 接口清单、请求构造、响应判据、已知坑 |

## 边界与免责

- 本项目仅用于**个人学习与自动化工程研究**：研究 SPA 平台的接口契约、WAF 通道选择、无头自动化的可复现性。
- 仓库不含任何账号、密码、token、题库原文、课程答案或个人提交记录。
- 使用前请自行确认所在学校/课程对自动化提交的规定。**因使用本项目产生的任何后果由使用者自行承担。**
- 平台接口在改版后可能失效；失效时的重新发现流程写在 SOP §5，更新后请提 PR 或自行维护 fork。

## License

[MIT](LICENSE)
