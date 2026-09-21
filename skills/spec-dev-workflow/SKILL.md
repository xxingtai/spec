---
name: spec-dev-workflow
version: 0.5.0
description: >
  spec-workflow 编排层的核心流水线 skill（spec 驱动开发，8 阶段；另附 9 阶段串联流水线）。
  由编排引擎（spec_cli.py）驱动：开始新功能/新阶段时初始化 spec，
  按声明式流水线逐阶段推进——每阶段完成必须通过机器门禁（结构 + 可扩展检查），
  需求/设计两阶段先经用户确认，产物与决策登记进 state.json，阶段间以 handoff 交接。
  支持中断后断点续传。门禁三类：builtin 内置规则、command 外部命令（args + 占位符，跨平台接任意 CLI）、
  review 质量门控（score + Gate 红线）。内置 chained 流水线把 spec-health-check 与 dev-docs 串进流程。
  触发词：开始开发、开始阶段、开始功能、init spec、初始化开发、
  开发第N阶段、准备开发、恢复开发、继续开发、查进度
---

# Spec 开发工作流（编排版）

## 核心原则

1. **门控达标才流转** — 每个阶段收口前必须通过机器门禁，禁止跳过校验或自行推进
2. **用户管方向，机器管纪律，AI 管执行** — 需求/设计经用户确认；状态/门禁由引擎裁决；AI 负责产出
3. **状态唯一真源** — state.json 由引擎管理；00-index.md 是自动渲染视图，禁止手改
4. **handoff 交接** — 下游阶段只读上游 handoff（≤4KB），禁止全文重读上游产物
5. **实事求是** — 记录实际做了什么，不虚构；handoff 是"电报"不是作文

## 目录分层（文档与运行时分离）

| 区域 | 位置 | 内容 | 性质 |
|:---|:---|:---|:---|
| 文档区 | `<spec根>/<yyyymmddhhmm>-<slug>/` | `00-index.md` + `01-requirements.md` ~ `08-commit.md` 等 spec 产物 | 人读文档，可提交/交付；目录名含创建时间戳便于排序 |
| 会话区（运行时） | `<安装目录>/.specworkflow/sessions/<yyyymmddhhmm>-<slug>/` | `state.json`（状态机）+ `handoff/`（阶段交接） | 机器状态，gitignore |

设计要点：**开发项目目录只保留 spec 文档**；状态与交接放在专门的 `.specworkflow/` 运行时目录。
`00-index.md` 是从 state.json 渲染的视图（仍落在文档区供人查看），状态真源在会话区。

## 路径与命令

CLI 与模板位于**本 skill 目录**（SKILL.md 所在目录）的 `scripts/`、`templates/` 下。
无论全局还是项目级安装，统一用本目录定位：

> 跨平台：引擎为纯 Python（3.7+），Windows/Linux/macOS 原生可跑。Windows 若没有 `python3`
> 启动名，用 `python` 或 `py -3` 替代下方示例的 `python3`（如 `python $CLI init …`）。

```bash
CLI="<skill目录>/scripts/spec_cli.py"
python3 $CLI init <spec根目录> <slug> [--name 显示名]  # 开始新功能；目录自动命名 <yyyymmddhhmm>-<slug>（按时间排序）
python3 $CLI status <spec根目录> <目录名>              # 进度看板（目录名 = <yyyymmddhhmm>-<slug>，见 init 输出/status 标题）
python3 $CLI restore <spec根目录> <feature> [--json]           # 断点续传（恢复会话第一步）
python3 $CLI phase-complete <spec根目录> <feature> <阶段> \
  --handoff '{"summary":"...","key_decisions":[...],"artifacts":{...},"next_inputs":{...}}'
python3 $CLI phase-complete <spec根目录> <feature> review \
  --handoff '...' --review-result '{"score":87,"gate":"green","dimensions":[{"id":"A需求质量","score":92},...],"issues":[{"severity":"MINOR","dimension":"B","desc":"...","fix":"..."}]}'  # review 质量门控阶段必填，score<min_score(默认80)拒绝
python3 $CLI phase-complete <spec根目录> <feature> <阶段> --skip "<原因>"   # 显式跳过
python3 $CLI gate <spec根目录> <feature> [--phase 阶段]        # 门禁预检
python3 $CLI handoff read <spec根目录> <feature> <阶段>         # 读上游交接
python3 $CLI handoff list <spec根目录> <feature>
```

`<spec根目录>` 默认为项目下 `spec/`。

### 流水线选择

```text
<spec根目录>/pipeline.json        项目级定义（存在即整体替换内置定义）
    写法 A：完整 stages 数组
    写法 B：{"extends": "<内置名>"}  ← 继承本 skill 内置流水线，可再覆盖 id/name/version
                                      （给 stages 则整体替换，适合"只改一小部分"）
内置：pipelines/default.json      8 阶段（编排骨架，不依赖其他 skill）
      pipelines/chained.json      9 阶段（串联 spec-health-check + dev-docs，见下）
```

`init` / `status` / `restore` 都会打印实际生效的 pipeline id 与来源（builtin / project），
可据此确认配置是否生效。`extends` 的名字只允许字母数字`-`，路径穿越会被拒绝。

## 入口路由（每次会话第一步）

| 用户意图 | 动作 |
|:---|:---|
| 「恢复/继续 <feature>」「接着上次」 | `restore --json` → 按输出与 suggest 从断点继续 |
| 新需求（「开始开发 X」等） | 定位 spec 根 → `init <spec根目录> <feature> --name X` → 进入第一阶段 |
| 问进度 | `status` |
| 纯查询/讨论，无开发意图 | 直接回答，**不走流程** |

## 阶段循环协议

每阶段标准动作（按当前阶段执行）：

1. **读上游 handoff**：`handoff read <spec根目录> <feature> <上一阶段>`（第一阶段跳过）。
   信息不足时按小节锚点定向 `Grep/Read(offset+limit)` 上游产物，**禁止全文重读**。
2. **填写产物**：按 references/ 指南与模板要求填写本阶段产物文件。
   **完成后删除产物文件末尾的模板标记行**（含 `SPEC_TEMPLATE_PENDING` 的行）——门禁据此判定已填写。
3. **确认点阶段**（pipeline 中 `confirm_point: true`，默认 requirements/design）：
   先把产物要点 + 验收标准/设计要点呈现给用户，**获用户认可后才能收口**，不得自主推进。
4. **review 质量门控**（pipeline 中挂 `{"type":"review"}` 的阶段，默认 review）：
   收口前按 `spec-health-check` skill 的四维评审（A 需求质量/B 跨文档一致性/C 留痕真实性/D 设计计划）
   对 feature 产物链评审打分，产出 review-result（score 0-100 + gate + dimensions + issues）：
   - `phase-complete ... --review-result '<json>'`；引擎校验 score ≥ min_score（默认 80）才放行
   - 配置了 `require_gate` 时**还必须给出 `gate` 且达到该等级**（缺失即拒，fail-closed）
   - **低于红线被拒**：按输出的 issues 修复产物 → 重新评审 → 再收口；禁止降阈值或绕过
   - review-result 管 spec 文档质量；代码审查由 06-code-review-report.md + code-reviewer 子代理负责（互补）
5. **收口**：`phase-complete ... --handoff '<json>'`。门禁全过则状态推进并刷新视图，进入下一阶段循环。

### handoff 四字段（下游唯一输入，≤4KB）

| 字段 | 内容 | 约束 |
|:---|:---|:---|
| `summary` | 本阶段完成了什么 | ≤300 字，电报式 |
| `key_decisions` | 本阶段关键决策（每条一句话） | ≤5 条 |
| `artifacts` | 产物相对路径 | 对象 |
| `next_inputs` | 下游所需键值对 | 对象 |

## 执行纪律（硬约束）

- ⛔ 状态只能通过 CLI 修改，禁止直接编辑 `state.json` / `00-index.md`
- ⛔ phase-complete 失败时按报错修复后重试，禁止绕过校验或跳过门禁
- ⛔ confirm_point 阶段未经用户确认不得收口（用户认可后才可 phase-complete）
- ⛔ 下游只读上游 handoff，禁止全文重读上游产物
- ⛔ 同会话已读文件不重复读；可并行的工具调用必须并行
- ⛔ 命令参数只按上方速记调用，禁止猜测

---

## 八阶段参考（产物内容指南，配合 references/ 使用）

### 进度状态

`status` / `00-index.md` 阶段：⏳ 待开始 / 🔄 进行中 / ✅ 已完成 / ⏭️ 已跳过。

### 阶段一：需求阐述（confirm_point）

**产物**：`01-requirements.md`

**要点**：背景与目标、用户场景、In/Out of Scope、**可测试的 AC**（SMART/EARS：输入 → 预期结果）、非功能需求、依赖关系、开放问题。

**指南**：阅读 `references/requirements.md`。**收口前必须向用户展示并确认。**

### 阶段二：设计规划（confirm_point）

**产物**：`02-design.md`（架构/模块/接口/数据模型/错误处理）+ `03-implementation-plan.md`（任务拆解 15-30min，依赖明确）

**要点**：必须含 Mermaid 架构图；每个模块职责单一；任务能落地执行。

**指南**：阅读 `references/design.md`。**收口前必须向用户展示并确认。**

### 阶段三：实现

**产物**：源代码 + 在 `08-commit.md` 任务清单勾选进度

**执行**：按 03 的任务逐个实现（先测试后实现或按项目惯例），完成的打 `[x]`。

### 阶段四：单元测试

**产物**：`04-unit-test-plan.md`

**要点**：测试覆盖核心逻辑/边界/错误路径；记录用例与结果。

**指南**：阅读 `references/testing-strategy.md`。

### 阶段五：集成测试

**产物**：`05-integration-test-plan.md`

**注意**：项目无集成基础设施时可显式跳过：`phase-complete ... --skip "原因"`。

### 阶段六：Code Review

**产物**：`06-code-review-report.md`

**要点**：安全/性能/正确性/可维护性/测试 5 维度；标注严重度（CRITICAL/MAJOR/MINOR/NIT）与处置。

**指南**：阅读 `references/code-review-guide.md`。

### 阶段七：文档更新

**产物**：`07-docs-update-plan.md`

**要点**：README/API 文档/CHANGELOG 等与实际变更对齐，别流于形式。

### 阶段八：完成记录

**产物**：`08-commit.md`（终态门禁要求任务全勾选）

**要点**：汇总任务清单、commit 记录、实现概览、已知限制。

---

## 门禁说明

- **三类检查**：
  - `builtin` 内置规则：`artifacts_exist` / `artifacts_nonempty`（≥100B）/ `no_placeholder` / `no_fill_marker` / `tasks_any_checked` / `tasks_all_checked`
  - `command` 外部命令：exit 0 通过——**接入任意 CLI 不改引擎**
  - `review` 质量门控：`{"type":"review","min_score":80,"require_gate":"green"}`——收口时需 `--review-result`
- **门禁失败**：`phase-complete` 整体失败且状态零变化；修复产物后重试（review 被拒则重评）
- pipeline 加载期就会校验门禁声明：未知占位符、非法 `require_gate`、`args` 非数组、`tools` 结构错误都会直接报错退出，不会等到收口时才炸。

### command 门禁：args 模式（推荐）

```json
{"type":"command",
 "cmd": "python",
 "args": ["{SKILLS_DIR}/dev-docs/scripts/dev_docs.py", "check", "--dir", "{PROJECT_ROOT}", "--strict"]}
```

- `cmd` 为可执行文件。首元素写 `python` / `python3` 时引擎会替换为当前解释器（Windows 常无 `python3` 启动名）。
- **`args` 模式用 `shell=False` 逐参数传递**：路径含空格/中文安全，不经过 shell 解析。**跨平台推荐用这种。**
- 旧式 `{"cmd":"<整条 shell 命令>"}` 仍支持（交给 cmd.exe / `/bin/sh`），仅用于兼容既有配置。

**可用占位符**（引擎展开，不依赖 shell 变量语法）：

| 占位符 | 值 |
|:---|:---|
| `{SPEC_ROOT}` | spec 根目录（绝对路径） |
| `{SPEC_FEATURE}` | 当前 feature 目录名（`<yyyymmddhhmm>-<slug>`） |
| `{SPEC_DOCS_DIR}` | 当前 feature 的文档目录（绝对路径） |
| `{SPEC_SESSION_DIR}` | 当前 feature 的会话目录（含 state.json / handoff） |
| `{PROJECT_ROOT}` | spec 根目录的上级 = 被开发项目根（默认 `<项目>/spec` 时成立） |
| `{SPEC_PHASE}` | 当前阶段 id |
| `{SKILL_DIR}` | 本 skill 目录 |
| `{SKILLS_DIR}` | 同级 skill 的父目录（`spec-health-check` / `dev-docs` 所在处） |
| `{PYTHON}` | 当前 Python 解释器绝对路径 |

同一批键名**同时作为环境变量注入**门禁进程（`SPEC_ROOT` / `SPEC_FEATURE` / `SPEC_PHASE` …），
脚本可直接 `os.environ["SPEC_FEATURE"]` 读取，不必自己解析参数。门禁命令的 `cwd` 恒为 `<spec根目录>`。

> `{SKILLS_DIR}` 依赖"三个 skill 同级安装"这一约定——它们都在同一个 `skills/` 目录下。
> 若你把某个 skill 装到别处，改用它自己的绝对路径。

### review 门控：score + Gate 红线

```json
{"type":"review","min_score":85,"require_gate":"green","desc":"..."}
```

- `min_score`：总分下限。`--review-result '{"score":88,"gate":"green","dimensions":[...],"issues":[...]}'`
- `require_gate`：可选。要求 `gate` 达到该等级（`green` < `yellow` < `red`）。**缺失或非法时 fail-closed 拒绝**，
  防止"只报分数不报 Gate"绕过红线。语义与 `spec-health-check` 的 Gate 条件集对齐。

### tools：阶段与 skill 的绑定声明

```json
"tools": [{"skill":"dev-docs","role":"文档抽取与对账","entry":"scripts/dev_docs.py","gate":"check --strict"}]
```

`tools` 是**声明式**的：引擎不执行它，仅做结构校验，并在 `restore` 时输出当前阶段的绑定
（`restore --json` 的 `current_stage_tools` 字段 + 纯文本建议行）。作用是让"这个阶段该用哪个 skill、
验收靠哪条命令"随流水线一起版本化，AI 恢复会话时能直接读到，不用靠记忆。

> 注意：`tools` **不会自动调用**另一个 skill。本引擎是状态机 + 门禁裁决器，不是调度器；
> 生成动作仍由 AI 在阶段内按对应 SKILL.md 执行，门禁只负责验收。

## 串联流水线（chained，9 阶段）

在 `<spec根目录>/pipeline.json` 写一行即可启用：

```json
{"extends": "chained"}
```

在 default 8 阶段之上做了三件事：

| 阶段 | 相对 default 的变化 |
|:---|:---|
| `06 review` | `min_score` 80→**85**，并加 `require_gate: "green"`，与 spec-health-check 的 🟢 对齐 |
| `07 docs` | 追加**两步** `command` 门禁：`dev_docs.py doctor`（环境自检）→ `dev_docs.py check --dir {PROJECT_ROOT} --strict`（对账）。**文档未对账完不能收口** |
| `09 acceptance`（新增） | 产物 `health-report.md`；builtin + `command`（`health-check.py` 脚本层）+ `review`（模型层 Gate），**两层同时把关** |

三个 skill 的协作契约：

| skill | 在流水线中的角色 | 机器接口 |
|:---|:---|:---|
| `spec-dev-workflow` | 唯一状态机与门禁裁决者 | `spec_cli.py` |
| `spec-health-check` | `06 review` 的评审器 + `09 acceptance` 的验收器 | `health-check.py <spec根目录> <名称>`，退出码即结论 |
| `dev-docs` | `07 docs` 的文档抽取与对账器 | `dev_docs.py check --dir <项目根> --strict`，退出码即结论 |

**三条重要口径**（踩过才知道）：

1. `07 docs` 的门禁是**读-only 对账**。dev-docs 没跑过就没有 `inventory.json`，门禁会直接失败并提示
   "先运行 inventory" —— 这是有意的：chained 流水线要求文档阶段必须真的用 dev-docs 做。
   项目确实不需要 dev-docs 时，用 `phase-complete … docs --skip "原因"` 显式跳过，而不是放宽门禁。
2. `07 docs` 的门禁**必须先 `doctor` 再 `check`**。缺 tree-sitter 时 `inventory` 会静默产出"0 符号"的
   空盘点，而 `check --strict` 对空文档集返回 **PASS** —— 只挂 `check` 会把"什么都没提取"放行。
   `doctor` 在环境未就绪时退出码非 0，正好补上这个缺口。
3. `09 acceptance` 的 `command` 门禁（health-check 脚本层）**只保证结构**——9 文件齐全、无占位符残留。
   实测它对刚 `init` 出来的模板桩也会返回 0。真正的质量红线由同阶段的 `review` 门控
   （`require_gate: green`）承担，**不要因为脚本层绿灯就以为可以收口**。

> 通用教训：以"退出码即结论"接入外部 CLI 时，先确认**它在异常输入下真的会非 0 退出**，而不是
> 静默降级后返回 0。上面第 2 条就是没验证这一点踩出来的。

> 同级 skill：`spec-health-check` 与 `dev-docs` 都位于本编排层 `skills/` 下的同级目录；
> 两者也都能作为独立工具对任意项目单独使用，不依赖本流水线。
