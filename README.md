# spec

WorkBuddy Agent Skills 集合：以 **Spec 驱动开发（spec-driven development）** 为核心的一组可复用技能，
覆盖「需求 → 设计 → 实现 → 测试 → 评审 → 文档 → 提交」全链路。

三个 skill **可以单独用，也可以串起来用** —— 由 `spec-dev-workflow` 当编排层，
把 `spec-health-check` 的质量验收和 `dev-docs` 的文档抽取挂成流水线门禁。

> **本仓库是长期 fork，独立演进，不与上游同步。**
>
> 这三个 skill 的原创作者是 **ryaondeng**，本仓库在其基础上做了编排层改造，
> 自 2026-09-21 起由 **xingtai** 独立维护。**不追踪上游变更、不合并上游提交。**
>
> ⚠️ 因此本仓库的版本号与上游**不可直接比对**：同号不同内容。
> 引用版本时请带上仓库标识（如 `xxingtai/spec@spec-dev-workflow 0.5.0`），不要只说版本号。

## 技能列表

| Skill | 版本 | 一句话说明 |
|:---|:---|:---|
| [`spec-dev-workflow`](skills/spec-dev-workflow/) | 0.5.0 | 8 阶段 spec 驱动开发流水线，由编排引擎 `spec_cli.py` 驱动，每阶段带机器门禁、状态机与断点续传 |
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
    │   │   ├── default.json       # 8 阶段（不依赖其他 skill）
    │   │   └── chained.json       # 9 阶段（串联另外两个 skill）★
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
（串联流水线用 `{SKILLS_DIR}` 定位同级 skill）：

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

## 串联用法

### 启用

在被开发项目的 `spec/pipeline.json` 写一行：

```json
{ "extends": "chained" }
```

引擎会加载 `skills/spec-dev-workflow/pipelines/chained.json`（9 阶段）。
`init` / `status` / `restore` 都会打印实际生效的 pipeline id 与来源，可据此确认。

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

### 门禁能力（spec-dev-workflow ≥ 0.5.0）

- **`command` 门禁 args 模式**：`{"cmd":"python","args":["<脚本>","{SPEC_ROOT}","{SPEC_FEATURE}"]}`
  —— `shell=False` 逐参数传递，路径含空格/中文安全；占位符由引擎展开，跨平台一致。
- **占位符**：`{SPEC_ROOT}` `{SPEC_FEATURE}` `{SPEC_DOCS_DIR}` `{SPEC_SESSION_DIR}` `{PROJECT_ROOT}`
  `{SPEC_PHASE}` `{SKILL_DIR}` `{SKILLS_DIR}` `{PYTHON}`，同名键同时注入为环境变量。
- **`review` 门控的 Gate 红线**：`{"type":"review","min_score":85,"require_gate":"green"}`，
  `gate` 缺失即拒（fail-closed），避免"只报分数不报 Gate"绕过红线。
- **`tools` 声明**：`"tools":[{"skill":"dev-docs","role":"…","gate":"…"}]`，
  声明式、不自动执行，`restore` 时会输出当前阶段的绑定。
- **`extends` 继承**：项目级 `pipeline.json` 可只写 `{"extends":"<内置名>"}`。

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

## Fork 说明

### 状态

| | |
|:---|:---|
| 仓库性质 | 长期 fork，独立演进 |
| 原创作者 | **ryaondeng**（三个 skill 的原始设计与实现） |
| 当前维护者 | **xingtai** |
| 分叉起点 | 2026-09-21 |
| 上游同步 | **关闭** —— 不追踪、不合并上游提交 |

### 这意味着什么

- **版本号不通用**：本仓库与上游各自演进版本线，同号不同内容。引用时请带仓库标识
  （`xxingtai/spec@spec-dev-workflow 0.5.0`），不要只写版本号。
- **上游修复不会自动进来**：如果上游后续修了 bug，需要人工判断是否移植，不存在 merge 流程。
- **本仓库的改动是主线**：`spec-dev-workflow` 0.5.0 的编排层扩展（门禁 args 模式 / 占位符 /
  `require_gate` / `extends` / `tools`）与 `pipelines/chained.json` 都只存在于这里。
- **归属保留**：每个 skill 的 `SKILL.md` 与 `_meta.json` 均保留原作者的 `author` 字段；
  fork 关系记录在 `_meta.json` 的 `fork` 块里（`maintainer` / `since` / `upstreamSync: false`）。

### 变更记录在哪

- 每个 skill 的 `_meta.json` → `version` + `note`（该 skill 的变更摘要）
- 本 README 的「变更记录」章节 → 跨 skill 的整体演进
- `git log` → 逐次提交的完整上下文

### 待办

- **本仓库尚无 LICENSE**。长期 fork 对外分发前需要确定授权（并确认与上游授权兼容），
  当前刻意未擅自添加。

## 变更记录

### 2026-09-21 — 串联改造

- `spec-dev-workflow` 0.4.1 → **0.5.0**
  - `command` 门禁新增 `args` 模式 + 9 个占位符 + `SPEC_*` 环境变量注入
  - `review` 门控新增 `require_gate`（Gate 红线，fail-closed）
  - 流水线新增 `extends` 继承；`tools` 字段从死字段变为声明式绑定并在 `restore` 中透出
  - 新增 `pipelines/chained.json`（9 阶段）
- `spec-health-check` 0.3.1 → **0.3.2**：补机器契约文档（退出码语义、两层检查边界、门禁接入约定）
- `dev-docs` 1.5.6 → **1.5.7**：修正失效的 `install.py` 引用、补 `doctor` 文档、新增编排契约与分工边界

### 2026-09-21 — 确立长期 fork

- 明确本仓库为**长期 fork**，关闭上游同步、不合并上游提交；README 增加「Fork 说明」章节
- 各 skill `_meta.json` 的 `modifiedBy` 字段改为 `fork` 块
  （`maintainer` / `since` / `upstreamSync` / `note`），并保留原作者 `author`
- 标注待办：本仓库尚无 LICENSE，对外分发前需确定授权并与上游授权核对
