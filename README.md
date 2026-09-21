# spec

以 **Spec 驱动开发（spec-driven development）** 为核心的一组可复用技能，
覆盖「需求 → 设计 → 实现 → 测试 → 评审 → 文档 → 提交」全链路。

三个 skill **可以单独用，也可以串起来用** —— 由 `spec-dev-workflow` 当编排层，
把 `spec-health-check` 的质量验收和 `dev-docs` 的文档抽取挂成流水线门禁；
串联流程已是**默认**行为，不写任何配置即可启用。

## 技能列表

| Skill | 版本 | 一句话说明 |
|:---|:---|:---|
| [`spec-dev-workflow`](skills/spec-dev-workflow/) | 0.6.0 | spec 驱动开发流水线，**默认 9 阶段串联流程**，由编排引擎 `spec_cli.py` 驱动，每阶段带机器门禁、状态机与断点续传 |
| [`spec-health-check`](skills/spec-health-check/) | 0.3.2 | 对已有 spec 目录做双层体检（脚本层结构 + 模型层文档质量），输出 0-100 健康度评分与返修指引 |
| [`dev-docs`](skills/dev-docs/) | 1.5.7 | 从存量代码库反向生成技术文档（总览 / 架构 / 上手 / 各模块详档），支持漂移检测与证据标注 |

## 目录结构

```
spec/
├── README.md
├── .gitignore
├── .gitattributes
└── skills/
    ├── spec-dev-workflow/         # 编排层
    │   ├── SKILL.md
    │   ├── _meta.json
    │   ├── pipelines/
    │   │   ├── chained.json       # 9 阶段 · 默认（串联另外两个 skill）★
    │   │   └── default.json       # 8 阶段 · 零外部依赖（显式退回用）
    │   ├── references/            # 需求/设计/测试/评审指南
    │   ├── scripts/spec_cli.py    # 编排引擎
    │   └── templates/             # 00-index ~ 08-commit 阶段模板
    ├── spec-health-check/         # 质量验收
    │   ├── SKILL.md
    │   ├── _meta.json
    │   ├── references/scoring-rubric.md
    │   └── scripts/health-check.py / .sh
    └── dev-docs/                  # 文档抽取
        ├── SKILL.md
        ├── _meta.json
        ├── requirements.txt       # tree-sitter（硬依赖）
        ├── references/
        ├── scripts/dev_docs.py, dev_inventory.py, dev_langs/
        └── templates/
```

## 安装

每个技能都是自包含目录，复制到 skills 目录即可；**三个要放在同一个 `skills/` 下**
（默认流水线是串联流程，用 `{SKILLS_DIR}` 定位同级 skill；缺依赖会在加载期报错）：

```bash
git clone https://github.com/xxingtai/spec.git
cp -r spec/skills/* ~/.workbuddy/skills/

# dev-docs 有额外运行时依赖
python -m pip install -r ~/.workbuddy/skills/dev-docs/requirements.txt
python ~/.workbuddy/skills/dev-docs/scripts/dev_docs.py doctor --dir .   # 自检
```

装好后在对话中直接说触发词即可唤起：

| Skill | 触发词 |
|:---|:---|
| `spec-dev-workflow` | 开始开发 / 初始化开发 / 开发第 N 阶段 / 继续开发 / 查进度 |
| `spec-health-check` | 检查 spec / spec 健康度 / 体检 spec / 验收 spec |
| `dev-docs` | 给项目生成文档 / 反建文档 / 补接口文档 / 补架构文档 |

## 串联用法（默认行为）

### 启用

**无需任何配置。** 不写 `<项目>/spec/pipeline.json` 时，引擎直接使用内置
`skills/spec-dev-workflow/pipelines/chained.json`（9 阶段），`init` 即可开跑。

`init` / `status` / `restore` 都会打印实际生效的 pipeline id、名称、阶段数与来源，可据此确认。

### 前置：同级安装

chained 的 `command` 门禁直接调用另外两个 skill 的脚本，所以三者必须位于**同一个 `skills/` 下**。
缺依赖时引擎在**加载期**就报错退出（fail-closed），**不会静默降级成 8 阶段**——降级会把质量红线一起降掉：

```
❌ 流水线 [spec-chained] 引用的同级 skill 依赖缺失：
  - 阶段 [docs] → dev-docs：找不到 …\skills\dev-docs\scripts\dev_docs.py
  …
  1) 把这两个 skill 装到同一个 skills 目录下：…
  2) 只用本 skill 独立开发：在 <spec根>/pipeline.json 写 {"extends": "default"}
```

### 退回独立流程

只想单跑 `spec-dev-workflow`（零外部依赖的 8 阶段），在项目里显式写一行：

```json
{ "extends": "default" }
```

### 阶段集合不可中途替换

`status` / `restore` / `gate` / `phase-complete` 都会比对 `state.json` 记录的阶段集合与当前流水线，
不一致时拒绝执行并打印两个集合的差异（只比阶段 id 序列，不比 pipeline id——同阶段集合下改门禁
参数是合法的）。

> ⚠️ **升级到 0.6.0 之前 init 的老 feature 会命中这条**：旧的 8 阶段 state 撞上新的 9 阶段默认流水线。
> 在该项目的 `spec/pipeline.json` 写回 `{"extends": "default"}` 即可继续；想用串联流程请**新开 feature**。

### 9 阶段与三个 skill 的分工

```
requirements ── design ── implementation ── unit-test ── integration-test
   [确认]        [确认]
                                                              │
                              ┌───────────────────────────────┘
                              ▼
       06 review     ← spec-health-check 四维评审（score + Gate，require_gate=green）
       07 docs       ← dev-docs doctor（环境）+ check --strict（对账）
       08 commit
       09 acceptance ← spec-health-check 脚本层（command）+ 模型层（review Gate）★新增
```

| skill | 角色 | 机器接口 |
|:---|:---|:---|
| `spec-dev-workflow` | 唯一状态机与门禁裁决者 | `spec_cli.py` |
| `spec-health-check` | 06 review 的评审器 + 09 acceptance 的验收器 | `health-check.py <spec根目录> <名称>` |
| `dev-docs` | 07 docs 的文档抽取与对账器 | `dev_docs.py doctor` / `check --dir <项目根> --strict` |

三个 skill 之间**只通过两条通道通信**：命令退出码（`command` 门禁）+ 落盘报告
（`health-report.md` 作为 09 阶段的产物）。互不 import，各自仍可单跑。

### 门禁能力（spec-dev-workflow ≥ 0.6.0）

- **`command` 门禁 args 模式**：`{"cmd":"python","args":["<脚本>","{SPEC_ROOT}","{SPEC_FEATURE}"]}`
  —— `shell=False` 逐参数传递，路径含空格/中文安全；占位符由引擎展开，跨平台一致。
- **占位符**：`{SPEC_ROOT}` `{SPEC_FEATURE}` `{SPEC_DOCS_DIR}` `{SPEC_SESSION_DIR}` `{PROJECT_ROOT}`
  `{SPEC_PHASE}` `{SKILL_DIR}` `{SKILLS_DIR}` `{PYTHON}`，同名键同时注入为环境变量。
- **`review` 门控的 Gate 红线**：`{"type":"review","min_score":85,"require_gate":"green"}`，
  `gate` 缺失即拒（fail-closed），避免"只报分数不报 Gate"绕过红线。
- **`tools` 声明**：`"tools":[{"skill":"dev-docs","role":"…","gate":"…"}]`，
  声明式、不自动执行，`restore` 时会输出当前阶段的绑定。
- **`extends` 继承**：项目级 `pipeline.json` 可只写 `{"extends":"<内置名>"}`。
- **默认流水线 = chained**（0.6.0）：不写 `pipeline.json` 即走 9 阶段串联流程；
  `pipelines/default.json` 降为显式退回项（`{"extends":"default"}`）。
- **依赖闸**（0.6.0）：加载期校验 `command` 门禁里 `{SKILLS_DIR}/…`、`{SKILL_DIR}/…` 指向的文件
  是否真实存在（这两类占位符与 feature 无关，可在加载期判定）。缺失即 fail-closed 退出，
  并给出「补装同级 skill」与「退回独立流程」两条出路，不让门禁在收口时才甩出 `can't open file`。
- **漂移闸**（0.6.0）：`status` / `restore` / `gate` / `phase-complete` 比对 `state.json` 记录的
  阶段集合与当前流水线，不一致时拒绝执行并打印差异。只比阶段 id 序列、不比 pipeline id，
  所以「同阶段集合下调门禁参数」仍然合法，只有真正增删阶段才拦。

### 两个已知坑（已写进 SKILL.md）

1. **缺 tree-sitter 时 `dev_docs.py check --strict` 对空文档集返回 PASS** ——
   因为 `inventory` 会静默产出"0 符号"的空盘点。所以 07 docs 的门禁是**两步**：
   先 `doctor`（环境未就绪即非 0 退出），再 `check`。
2. **`health-check.py` 的脚本层只判结构**，对刚 `init` 出的模板桩也会返回 0。
   09 acceptance 的真正红线是同一阶段的 `review` 门控（`require_gate: green`）。

## 环境要求

- Python 3.7+（`spec-dev-workflow`、`spec-health-check` 为纯 Python，跨平台）
- Python 3.12+ + tree-sitter（仅 `dev-docs`，见 `skills/dev-docs/requirements.txt`）
- Windows 下若没有 `python3` 启动名，用 `python` 或 `py -3` 替代

## 变更记录在哪

- 每个 skill 的 `_meta.json` → `version` + `note`（该 skill 的变更摘要）
- 本 README 的「变更记录」章节 → 跨 skill 的整体演进
- `git log` → 逐次提交的完整上下文

## 待办

- **本仓库尚无 LICENSE**。长期 fork 对外分发前需要确定授权（并确认与上游授权兼容），
  当前刻意未擅自添加。

## 变更记录

### 2026-09-21 — 串联流程成为默认

- `spec-dev-workflow` 0.5.0 → **0.6.0**
  - **默认流水线改为 `chained`（9 阶段）**：不写 `<spec根>/pipeline.json` 即走串联流程；
    `pipelines/default.json` 降为显式退回项（`{"extends":"default"}`），名称改为「零外部依赖」
  - 新增**加载期同级 skill 依赖校验**（`missing_skill_deps` / `verify_skill_deps`）：
    `{SKILLS_DIR}` / `{SKILL_DIR}` 引用缺失即 fail-closed，并给出「补装同级 skill」与
    「退回独立流程」两条出路
  - 新增**阶段集合漂移拦截**（`load_pipeline_matching`）：比对 `state.json` 与当前流水线的
    阶段 id 序列，不一致即拒绝执行（四处接入：`status` / `restore` / `gate` / `phase-complete`）
  - `init` 输出补充 pipeline 名称、阶段数与退回提示；新增 `DEFAULT_PIPELINE_NAME` /
    `STANDALONE_PIPELINE_NAME` 常量，改默认只需改一处
  - 冒烟测试 23 → **29** 项（新增 H1–H6：默认流程、退回、漂移拦截、缺依赖 fail-closed）

### 2026-09-21 — 串联改造

- `spec-dev-workflow` 0.4.1 → **0.5.0**
  - `command` 门禁新增 `args` 模式 + 9 个占位符 + `SPEC_*` 环境变量注入
  - `review` 门控新增 `require_gate`（Gate 红线，fail-closed）
  - 流水线新增 `extends` 继承；`tools` 字段从死字段变为声明式绑定并在 `restore` 中透出
  - 新增 `pipelines/chained.json`（9 阶段）
- `spec-health-check` 0.3.1 → **0.3.2**：补机器契约文档（退出码语义、两层检查边界、门禁接入约定）
- `dev-docs` 1.5.6 → **1.5.7**：修正失效的 `install.py` 引用、补 `doctor` 文档、新增编排契约与分工边界
