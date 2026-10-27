# AnyView 零浏览器 SOP

> 版本 1.0 · 2026-10-26
> 适用对象：`anyview.gdut.edu.cn`（广东工业大学 AnyView 数据结构判题平台）
> 一句话：**全链路不需要 ego-browser，也不需要任何浏览器。** 浏览器只在「平台改版」这种被动场景下才临时出场。

---

## 1. 结论先行：依赖边界矩阵

| 环节 | 脚本 | HTTP 通道 | 依赖浏览器？ | 说明 |
|---|---|---|---|---|
| 登录取 token | `tools/login.py` | `/usr/bin/curl` 子进程 | 否 | RSA 加密纯本地算，公钥走一次 GET |
| 抓题目录 / 题干 | `tools/crawl.py` | Python `urllib` | 否 | 实测当前 WAF 放行 |
| 生成答案 | `答案/_src/*.py` | 无网络 | 否 | 纯本地 C 代码 |
| 拼装完整源码 | `tools/assemble.py` | 无网络 | 否 | 模板骨架 + 答案实现，纯字符串 |
| 保存代码 | `tools/submit.py --save` | curl | 否 | JSON |
| 编译比对 | `tools/submit.py --compile` | curl | 否 | form-urlencoded |
| 运行判题 | `tools/submit.py --run` | curl | 否 | multipart，唯一写 `exerciseStatus=2` 的入口 |
| 批量拟人化提交 | `tools/exam_drive.py` | curl（复用 submit） | 否 | 带随机停顿与台账 |
| 环境自检 | `tools/doctor.py` | curl + urllib | 否 | 只读探测，秒级 |

**代码级证据**：对 `tools/` 与 `docs/` 全目录 grep `ego|browser|page\.|cdp|playwright|puppeteer`，零命中。

```bash
cd reverse-anyviewc && grep -rln "ego\|browser\|page\.\|cdp\|playwright\|puppeteer" \
  --include="*.py" --include="*.js" . | wc -l      # → 0
```

---

## 2. 为什么「看起来强依赖」其实是历史错觉

ego-browser 在整个项目里只干过两件事，**都不是运行时的活**：

| 历史用途 | 性质 | 当前状态 |
|---|---|---|
| CDP 抓真实请求，逆向出三个写接口的 Content-Type 与字段名 | **一次性调研** | 产物已固化成 `submit.py` 里的常量与注释，之后再也不需要 |
| `page.evaluate(fetch)` 借浏览器过 WAF | **兜底通道** | `curl` 能过，已不再启用 |

**解耦成立的关键动作只有一个**：把「只有浏览器能告诉我的事实」写成代码里的常量。
一旦写成常量，浏览器就从「依赖」降级为「一次性工具」。

已固化的硬知识（SOP 的核心资产，别丢）：

| 接口 | Content-Type | 关键字段 |
|---|---|---|
| `POST /api/gdb-service/exercise/saveStudentCode` | `application/json` | `eID`、`studentCode`(base64) |
| `POST /api/gdb-service/code/compile` | `application/x-www-form-urlencoded` | `questionRes.questionFullName`（**长名**，`第1章-DC01PE03e`）、`stuCode`(base64) |
| `POST /api/gdb-service/code/runGroup` | `multipart/form-data` | `questionFullName`（**短名**，`DC01PE03e`）、`kind`、`language`、`stuCode`(base64)，另需 `Eid` 头 |

> 坑：三者 Content-Type 弄错时，网关回 **404**（`{"path":...}`）而不是 415。
> 看到 404 先怀疑自己的请求格式，不要怀疑路由不存在。

---

## 3. 环境准备（一次性）

| 项 | 要求 | 说明 |
|---|---|---|
| 解释器 | Python ≥ 3.8，且**能 `import rsa`** | 只有登录需要；宿主机自带 python3 常缺 |
| curl | 存在即可，默认 `/usr/bin/curl` | 写接口唯一稳妥通道 |
| 数据根 | 存放题库 / 答案 / 台账的目录 | 默认仓库内 `local-data/`，被 `.gitignore` 排除；用 `ANYVIEW_DATA_DIR` 可指到别处 |

**代码与数据分离**：本仓库只放代码；题库、答案、提交台账、token 这些运行期数据统一放 `local-data/`，该目录整体不进版本库（`.gitignore` 里 `local-data/*`，只放行一份说明 README）。所有脚本通过 `tools/paths.py` 统一解析路径，不硬编码任何位置。

准备解释器（隔离 venv，不污染系统）：

```bash
python3 -m venv ~/.venvs/anyview
~/.venvs/anyview/bin/pip install rsa
```

下文统一约定：

```bash
git clone <本仓库地址> reverse-anyviewc && cd reverse-anyviewc
export PY=<能 import rsa 的 python>            # 例：~/.venvs/anyview/bin/python
mkdir -p local-data                            # 数据根默认就在仓库内，鲜少需要改
# 要换位置才设：export ANYVIEW_DATA_DIR=/path/to/data
```

系统 `/usr/bin/python3` 若 `import rsa` 报 `ModuleNotFoundError`，别硬用，换 `$PY`。

---

## 4. 五阶段 SOP

### S0 · 自检（每次动手前 5 秒）

```bash
$PY tools/doctor.py
```

预期末行：`结论：可用 —— 全链路零浏览器，可以抓题 / 拼装 / 提交`
退出码 0 → 往下走；1 → 看 `/FAIL` 那行提示。

### S1 · 登录取 token

```bash
# 方式 A（推荐）：账号密码写进 <数据根>/.cred，两行式；该文件已被 git 忽略、权限 600
printf '%s\n%s\n' '<学号>' '<密码>' > local-data/.cred && chmod 600 local-data/.cred
$PY tools/login.py

# 方式 B：走环境变量（不落盘）
ANYVIEW_USER=<学号> ANYVIEW_PASS='<密码>' $PY tools/login.py
```

预期：`登录成功: <姓名>` + `TOKEN_LEN=<n> (已写入 <数据根>/.token, 权限 600)`

**注意两个密码不是一套**：`anyviewc` 有自己的密码，和「GDUT 统一认证中心」的密码不同。
用统一认证密码去登会得到 `登录失败 code=1 用户名或密码错误`，别误判成账号被锁。

**铁律**：平台是单点登录，**同一账号只能有一个活 token**。
在浏览器里登录一次，脚本的 token 立刻废（报 `403 当前用户状态失效`）；反之亦然。
所以顺序永远是「浏览器侧先收工 → 再跑 login.py」。日常零浏览器，这条基本不会踩。

### S2 · 抓题目录与题干

```bash
$PY tools/crawl.py
```

产出落在数据根（默认 `local-data/`）：`原始数据/schemes.json`、`catalog_*.json`、`q_<eid>.json`、`_manifest.json`。
已存在的 `q_<eid>.json` 会跳过，可安全重跑（增量）。token 由脚本自动从 `<数据根>/.token` 或 `ANYVIEW_TOKEN` 读取，不必手动传。

> 注意：`crawl.py` 用 `urllib`，`submit.py` 用 `curl`。若某天 `doctor.py` 报 `urllib 通道` 失败，
> 说明 WAF 开始拦 Python 原生 TLS，此时把 `crawl.py` 的 `api()` 换成 curl 子进程即可（见 §6）。

### S3 · 答案与拼装

```bash
$PY tools/assemble.py --all --out-dir local-data/提交源码   # 全量成品 .c
$PY tools/assemble.py --eid <eid> --verify                  # 单题语法自检
```

规则（`assemble.py` 已实现，改答案时照此遵守）：

- 按**函数名**精确替换函数体，不碰模板骨架；
- 答案里多出来的辅助函数必须**插到模板第一个函数之前**（C 要求先声明后使用，追加末尾会编译失败）；
- 平台 `allinclude.h` 自带 `ElemType / KeyType / SqStack` 等类型，**不能重复 typedef**；
- 平台是 **16 位整型环境**，`MAXINT = 32767`，不是 2147483647；
- 平台没有 `OVERFLOW` 宏，溢出请 `return ERROR`。

### S4 · 提交

单题：

```bash
$PY tools/submit.py --eid <eid> --all          # 保存 + 编译 + 判题
$PY tools/submit.py --eid <eid> --status       # 只看状态
```

批量（拟人节奏）：

```bash
$PY tools/exam_drive.py --plan                                  # 只看计划与预估时长
$PY tools/exam_drive.py --run --min-wait 60 --max-wait 300      # 约 8 小时
$PY tools/exam_drive.py --run --limit 3                          # 先跑 3 题
```

判题成功的判据（不要看别的）：

```json
{"code":200,"data":{"passed":true,"errorOrder":{},"end":true,"runRightCount":6}}
```

`--status` 里 `exerciseStatus=2` 即「已通过」。

### S5 · 核验与分诊

单题核验：

```bash
$PY tools/submit.py --eid <eid> --status
```

看四个字段：`exerciseStatus=2`、`firstPastTime` 非空、`cmpRightCount/cmpCount` 满、`runRightCount > 0`。
批量结果落在 `提交记录.json`，可随时断点续跑。

批量跑完之后，**先分诊再动手改**：

```bash
$PY tools/triage.py --failed                 # 只编译不判题，分类打印
$PY tools/triage.py --failed --errors        # 直接读平台留存的完整 errorOrder（纯 GET）
$PY tools/triage.py --eid <eid> --show-src   # 单题 + 拼装后的源码
```

`--errors` 是从状态接口读 `data.exercise.errorOrder` —— 平台把最近一次判题失败
逐用例的「正确结果 vs 你的结果」完整存在那里，只读一次就有，不用重新判题。
排查失败题的第一动作应该是它，而不是重跑。

分诊只调 `compile`，不调 `runGroup`，所以不改变题目状态、不留判题痕迹，可以反复跑。

| 分诊结论 | 含义 | 该改哪儿 |
|---|---|---|
| 编译失败 | 拼装产物过不了编译器 | 先看是不是 `redefinition` / `conflicting declaration`（模板被污染或重复 typedef），再考虑答案代码 |
| 编译通过 | 代码能跑，输出与标准答案不一致 | 看台账 `reason`（已内置 `errorOrder` 摘要），按「正确结果 vs 你的结果」逐字节对齐 |
| 保存失败 | `saved=false` | token 被顶或网络问题，先 `login.py` / 重试，不要动答案 |

修完重跑同一批即可，`exam_drive` 只会挑台账里没通过的题：

```bash
./tools/run_submit.sh --min 60 --max 300
```

**两条反直觉的教训（都踩过）：**

- 拼装要幂等。提交过之后平台里的 `stuCode` 就是你自己写的代码，拿它当骨架会把自己的旧代码带进来，前置片段插两遍 → 编译期 `redefinition`。本仓库固定用原始模板。
- 题目说「以下函数可以直接调用」的，连声明都别写。手写原型的按值/按引用不一致，得到的是 `undefined reference` 而不是编译错。

---

## 5. 浏览器唯二需要出场的场景

两件都是**被动触发、低频、非自动化**。判断标准：不满足就不许开浏览器。

| 触发条件 | 浏览器要干什么 | 收尾动作（关键） |
|---|---|---|
| `doctor.py` 报 `curl 业务通道` 失败，且 curl 手动也过不去，但真浏览器能打开 | 仅在 DevTools Console 里跑一段 `fetch()` 把请求代理出去，当隧道用 | **不要**改成浏览器自动化脚本；只把结果手工带回来 |
| 平台改版（接口报 404 / 字段变了 / 新增验证码） | 用 CDP 抓一次真实请求，重新逆向契约 | 立刻把新契约写回 `submit.py` 常量 + 本文 §2 表格，然后**关掉浏览器** |

这就是「解耦」的完整含义：**浏览器是探针和应急隧道，不是执行引擎。**

---

## 6. WAF 通道选择与降级链

平台前置 WZWS WAF（响应头 `WZWS-RAY`）。历史上 `python requests` 会被拦成 403 或 JS 挑战页，
因为它的 TLS 指纹可识别；`curl` 和真浏览器可通。

降级链（从上到下依次试，第一项能用就不看后面）：

| 优先级 | 通道 | 现状 | 备注 |
|---|---|---|---|
| 1 | `/usr/bin/curl` 子进程 | 通 | **写接口首选**，也是 `submit.py` 唯一实现 |
| 2 | Python `urllib` | 通（2026-10-26 实测） | `crawl.py` 在用；被拦就换 1 |
| 3 | 浏览器 Console `fetch()` | 备用 | 只在 1、2 全挂时用，见 §5 |

2026-10-26 14:1x 实测记录：curl `code=31`（课程表 2 个）通过；urllib 同接口 `code=31` 通过；两者 token 均为 `login.py` 新签发。

---

## 7. 反检测红线（和浏览器无关，但决定「像不像真人」）

已做到的四点：模板原样保留、动作序列与真人一致（打开→停顿→保存→编译→运行）、间隔随机（按行数缩放 + ±20% 抖动）、保留试错痕迹。

三条硬约束：

1. **保存是异步落库的**。`save` 返回成功后立刻 `compile/runGroup`，判题环境可能仍拿旧代码，表现为「代码对却 `passed=false`」。必须 `save → 等 6s → compile → 等 2s → run`，失败再等 8s 补判一次。
2. **`accumTime`（答题耗时）无法伪造**。它只能由 WebSocket 连接时长累加，没有任何 HTTP 接口可写。受控实验里挂着连接等 92 秒，服务端仍记 0。这一项做不到「完美」。
3. **不要并发**。单点登录 + 同一 eid 的 save/run 有落库顺序依赖，并发只会制造假的失败记录。

---

## 8. 一页检查清单

动手前：

- [ ] `$PY tools/doctor.py` 退出码 0
- [ ] `.token` 存在、权限 600、`curl 业务通道` PASS
- [ ] 浏览器端没有活跃会话（避免被顶）

提交时：

- [ ] 单题先用 `--all` 验证，再批量
- [ ] 批量前 `--plan` 看预估时长，`--limit 3` 试跑
- [ ] 空代码必须被拒（`submit.py` 有此保护，不要绕过）

出问题先查：

| 现象 | 原因 | 处置 |
|---|---|---|
| `403 当前用户状态失效` | token 被浏览器登录顶掉 | 重跑 `login.py` |
| `404 {"path":...}` | Content-Type 或字段名错 | 对照 §2 表格 |
| `passed=false` 但代码正确 | save 落库延迟 | 等十几秒 `--run` 补判 |
| `ModuleNotFoundError: rsa` | 用错解释器 | 换 `$PY` |
| 编译报 `未声明` | 辅助函数追加在末尾 | 必须插到第一个函数之前 |
| `code=31` | 正常业务码，非错误 | 忽略 |

---

## 9. 维护

本 SOP 的失效信号只有一个：**平台接口契约变了**。
届时按 §5 第二行处理，更新 §2 表格与本文件版本号，其余章节不动。
