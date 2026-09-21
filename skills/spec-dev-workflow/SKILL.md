---
name: spec-dev-workflow
version: 0.4.1
description: >
  spec-workflow 编排层的核心流水线 skill（spec 驱动开发，8 阶段）。
  由编排引擎（spec_cli.py）驱动：开始新功能/新阶段时初始化 spec，
  按声明式流水线逐阶段推进——每阶段完成必须通过机器门禁（结构 + 可扩展检查），
  需求/设计两阶段先经用户确认，产物与决策登记进 state.json，阶段间以 handoff 交接。
  支持中断后断点续传。触发词：开始开发、开始阶段、开始功能、init spec、初始化开发、
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

`<spec根目录>` 默认为项目下 `spec/`；流水线定义取 `<spec根目录>/pipeline.json`（项目覆盖）→ skill 内置 `pipelines/default.json`。

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
   对 feature 产物链评审打分，产出 review-result（score 0-100 + dimensions + issues）：
   - `phase-complete ... --review-result '<json>'`；引擎校验 score ≥ min_score（默认 80）才放行
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
  - `command` 外部命令：`{"type":"command","cmd":"..."}`（exit 0 通过）——接入任意 CLI 不改引擎
  - `review` 质量门控：`{"type":"review","min_score":80}`——收口时需 `--review-result`（spec-health-check 四维评审），score ≥ min_score 才放行
- **门禁失败**：`phase-complete` 整体失败且状态零变化；修复产物后重试（review 被拒则重评）

> 编排层第二个被集成的 skill：`spec-health-check`（位于本编排层 `skills/` 下的同级 skill 目录；也可作为独立体检工具对任意 spec 目录使用）。
