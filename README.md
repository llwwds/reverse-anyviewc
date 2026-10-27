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
| 实验课工程编译 / 运行 / 子题提交 | curl 子进程（JSON） | 否 |
| 批量拟人化驱动 | curl（复用 submit） | 否 |
| 环境自检 | curl + urllib | 否 |

代码级证据：

```bash
grep -rln "ego\|browser\|page\.\|cdp\|playwright\|puppeteer" --include="*.py" . | wc -l
# → 0
```

浏览器在这个项目里只干过三件**非运行时**的事：用 CDP 抓真实请求做逆向、在 WAF 拦死时当应急隧道、在真浏览器里读 `performance.getEntriesByType("resource")` 拿 webpack 分包的真实文件名。三件都已被「写成常量」化解 —— 详见 [docs/SOP-无浏览器.md](docs/SOP-无浏览器.md) §2。

## 仓库布局

```
reverse-anyviewc/
├── README.md
├── docs/
│   ├── SOP-无浏览器.md      # 主体：五阶段 SOP + 依赖边界 + 降级链
│   └── 接口契约.md          # 理论课三写接口 + 实验课 lab 子系统的 Content-Type / 字段 / 响应
├── local-data/              # 本地数据（.gitignore 排除，只留 README 作说明）
│   └── README.md            # 数据目录约定：每个子目录放什么
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
│   ├── lab.py               # 实验课：多文件工程的抓取 / 编译 / 运行 / 保存 / 子题
│   ├── labtest.sh           # 实验课本地编译 + 限时运行（不联平台，先自己验一遍）
│   ├── run_submit.sh        # 一键启动：间隔抖动 + 单实例锁 + 日志 + status/stop
│   ├── triage.py            # 失败题分诊：只编译不判题，洗净编译器原文
│   └── doctor.py            # 环境自检
├── .githooks/
│   └── pre-commit           # 兜底：local-data/ 只允许 README.md 入库
└── .gitignore
```

**本仓库不含**：账号凭据、题库原文、课程答案、提交记录。这些是运行期数据，统一放在 `local-data/`——该目录被 `.gitignore` 整体排除，只有一份 `README.md` 随仓库分发，用来说明各子目录该放什么。`ANYVIEW_DATA_DIR` 可把数据根指到别处。

### 数据目录的两道防线

`.gitignore` 只拦「常规 `git add`」；`git add -f` 能绕过它。数据目录就在仓库内，误 `-f` 的代价是题库与答案永久留在公开历史里，所以加了一道 pre-commit 兜底。clone 后启用一次：

```bash
git config core.hooksPath .githooks
```

之后任何试图把 `local-data/` 下非 README 文件提交进来的操作都会被拒。自查：

```bash
git status --short                              # local-data/ 下只该出现 README.md
git check-ignore -v local-data/答案/_src/ch01.py  # 应命中 local-data/* 规则
```

## 快速开始

```bash
git clone <本仓库地址> reverse-anyviewc && cd reverse-anyviewc

# 0. 启用数据保护 hook（防 git add -f 误把数据提交上去）
git config core.hooksPath .githooks

# 1. 准备解释器（登录需要 rsa）
python3 -m venv ~/.venvs/anyview
~/.venvs/anyview/bin/pip install rsa
export PY=~/.venvs/anyview/bin/python

# 2. 数据根默认就是仓库内的 local-data/，一般不用设
#    要改位置才需要：export ANYVIEW_DATA_DIR=/path/to/data
mkdir -p local-data

# 3. 自检 —— 退出码 0 就能干活
$PY tools/doctor.py

# 4. 登录（token 落在 local-data/.token）
#    二选一：环境变量，或写进 local-data/.cred（推荐，两行式：账号 / 密码）
printf '%s\n%s\n' '<学号>' '<密码>' > local-data/.cred && chmod 600 local-data/.cred
$PY tools/login.py

# 5. 抓题
$PY tools/crawl.py

# 6. 拼装 + 提交
$PY tools/assemble.py --all --out-dir local-data/提交源码
$PY tools/submit.py --eid <eid> --all

# 7. 一键批量（每题间隔 60~300 秒抖动，单实例保护，可随时中断续跑）
./tools/run_submit.sh --plan      # 先看计划与预估时长
./tools/run_submit.sh             # 开跑
./tools/run_submit.sh --eid 11875442,11875559    # 只补指定题
./tools/run_submit.sh --status    # 进度
./tools/run_submit.sh --stop      # 停

# 8. 实验课（多文件工程，走另一套接口，见下文「关键发现」）
$PY tools/lab.py dump                       # 21 个工程 → local-data/实验答案/<eid>/
bash tools/labtest.sh 141443                # 本地编译 + 6 秒限时运行，先自己验一遍
$PY tools/lab.py compile 141443             # 平台编译
$PY tools/lab.py run 141443                 # 平台运行，比对 output 与本文件注释里的预期
$PY tools/lab.py save 141443                # 保存并回读逐字节校验
$PY tools/lab.py submit-practice 141443     # 提交子题（读同目录 _answers.json）
$PY tools/lab.py check --deep               # 完成度总览
```

`.cred` 和 `.token` 都在 `local-data/` 下，已被 `.gitignore` 与 pre-commit hook 双重拦截，不会进仓库。

完整流程与失败处置见 [docs/SOP-无浏览器.md](docs/SOP-无浏览器.md)。

## 工具一览

| 工具 | 作用 | 网络 |
|---|---|---|
| `paths.py` | 统一解析数据根与 token 位置 | 无 |
| `run_submit.sh` | 一键启动批量提交；间隔抖动、单实例锁、日志、`--eid` 指定题、状态查询 | 间接 |
| `login.py` | RSA/PKCS1v15 加密登录，写 token（权限 600）；账号密码可放 `<数据根>/.cred` | curl |
| `crawl.py` | 拉课程表 → 题目目录 → 逐题详情，增量可重跑 | urllib |
| `enrich.py` | 补 `difficulty` / `exerciseStatus` / `accumTime` 等 | urllib |
| `render.py` | 渲染成 `index.md` / `全题库.md` / `题库.json` | 无 |
| `scan_templates.py` | 统计模板形态分布，用于设计拼装规则 | 无 |
| `assemble.py` | 按函数名替换函数体，辅助函数前置插入 | 无 |
| `answers_build.py` | 批量产出答案文件并做语法校验 | 无 |
| `submit.py` | 保存 / 编译 / 判题三件套 + 状态查询；传输层失败自动重试并回落直连 | curl |
| `triage.py` | 失败题分诊：批量只编译不判题；`--errors` 直接读平台留存的完整 errorOrder（只 GET，不消耗判题） | curl |
| `exam_drive.py` | 按拟人节奏批量跑，台账支持断点续跑 | curl |
| `lab.py` | 实验课（多文件工程）全链路：`dump` / `compile` / `run` / `save` / `practice` / `submit-practice` / `check` | curl |
| `labtest.sh` | 实验课本地验证：`clang++` 全量编译 + 6 秒限时运行，退出码区分编译失败 / 超时 | 无 |
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

### 实验课不是「理论课换参数」，是另一套子系统

同一门课里，理论课和实验课的接口、载荷、状态机全都不共用。把它们当成一个系统去猜，会连续踩四个坑：

| | 理论课（scheme 465，`questionType=1`） | 实验课（scheme 466，`questionType=3`） |
|---|---|---|
| 形态 | 单文件 `stuCode` | 多文件工程（3~7 个 `.cpp/.h`） |
| 取题 | `/scheme-service/scheme/list/student/{eid}` | `/scheme-service/scheme/list/student/**lab**/{eid}` |
| 编译 | `/gdb-service/code/compile`（form） | `/gdb-service/code/**lab**/compile`（**JSON**） |
| 运行判题 | `/gdb-service/code/runGroup`（multipart） | `/gdb-service/code/**lab**/run`（**JSON**） |
| 保存 | `/gdb-service/exercise/saveStudentCode` | `/gdb-service/exercise/**lab**/saveCode` |
| 子题 | 无 | `/exam-service/lab/practice/*`（每个实验 3~10 道） |

四个坑，每个都能让你以为「接口写错了」或「代码没生效」：

**`labExerciseId` 必须等于 `eid`，不是 `questionId`。** 传错时后端回 `404 {"error":"Not Found","path":"/code/lab/compile"}` —— 长得和「路由不存在」一模一样，实际是参数不匹配。跟理论课三个写接口那个 404 陷阱同源：**这个网关习惯用 404 表达「你的请求我不认识」**。

**实验课的 `passed` 恒为 `false`。** `lab/run` 会照常返回 `output`、`exception`、`end`，唯独 `passed` 没有参考答案可比，永远是 false。把 `completes` 里的标识符全塞进注释也不会变 true。判据只能是「编译成功 + output 与 Main.cpp 注释里的预期一致」。

**子题的 `score` / `isCorrect` 恒为 0。** 选择题作答与 `pqd.optionSolution` 逐位相同时依然是 0 —— 这个字段要等教师批改。判据只能是「有提交记录且 `isTempSaved=false`」。

**实验课的状态列永远是「—」，这不是没做完。** `catalog/list?schemeId=466` 的每一项只有 10 个字段（`difficulty/eid/pname/pmemo/maximumSimilarity/comment/accumTime/questionType/chapName/commentStatus`），没有 `pass`，也没有理论课那个 `kind`；课程列表里实验课的 `passNum` 恒为 `null`（理论课是 131/131）。前端渲染是 `row.pass ? ✓ : row.accumTime ? ✎ : —`，没有 `pass` 就一定落到兜底分支。

### 四条最贵的教训

**拼装必须幂等。** 提交过一次之后，平台里的 `stuCode` 就是你自己写进去的代码。如果拼装时把平台当前模板当骨架，下次就会把自己的旧代码当输入，前置 typedef / 辅助函数插两遍 —— 编译期炸 `redefinition`。本仓库的 `build_one()` 固定用**首次抓取时存档的原始模板**，并在插入前置片段时按符号去重。

**别自己声明平台提供的函数。** 题目写「以下函数可以直接调用」的，连原型都不要写。手写一份形参按值的声明去调平台按引用的实现，得到的不是编译错误，而是 `undefined reference`。

**「跳过」比「做错」更危险。** 做错会被判题打回来，跳过则安安静静躺在那里，台账上一个字都不提它。`exam_drive.py` 的跳过判据（「模板里已有同名函数」= 用户的历史代码）曾把平台给的半成品骨架和 `return ...;` 占位符一并放过，导致统计出「126/126 全绿」而实际还有 4 题没做。现在判空壳时连 `...` 一起认，并要求「确实做过」的证据（`exerciseStatus=2` / `pass` / `accumTime>0`）才允许跳过。凡是按规则放过的分支，都要有一条独立复核再数一遍。

**先确认「完成」在服务端有没有落点，再决定做什么。** 实验课这边，21 个工程、148 处空位、145 道子题全部落地之后才发现：`catalog/list` 对实验课压根没有状态字段，页面上的「未完成」永远不会变。如果一开始先查一眼返回字段表，就不会先把子题提交一轮、再回头怀疑「是不是没生效」。**先读字段表，再读文档，最后才动手。**

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
