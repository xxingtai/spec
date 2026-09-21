---
name: dev-docs
version: 1.5.6
description: >
  从已有代码库反建技术文档（dev-docs）：面向没有接口/设计/架构文档的存量项目，
  用「规则提取（文件全集/机器盘点/对账漂移）+ LLM 提取（AI 通读建语义地图再按模板填文档）」双轨机制，
  产出「总览 index / 架构 architecture / 上手 usage / 各模块详档 reference / 数据层（可选 data）」三层页面树文档集，
  每页统一格式（相关源文件头 + 机器区 + Sources 尾）；页面树由 plan 命令维护（.devdocs-plan.json），
  AI 按 brief 命令生成的页级工单逐页填写；支持草稿合入、长期漂移检测（check --drift，非 git 用文件哈希基线）、
  结构门禁（check：缺页 / 缺节 / 引用不存在 / AI-FILL 残留，--strict 收紧）、
  按模块增量重生成、人工补充区保护（AI-GEN marker，AI 叙事节在区外不被重生成冲掉）、
  人工登记通道（register，带 file:line 源码证据）、证据标注防编造（evidence 协议 + unknown）。
  语言策略：Python 由 AST 可靠提取；其他语言/生态（C++、ROS msg/srv、任意 DSL）由 LLM 语义地图覆盖，
  关键符号用 register 显式登记（不算 phantom）。
  触发词：给项目生成文档、从代码提取文档、反建文档、补接口文档、补架构文档、生成API文档、dev-docs、逆向文档化
---

# dev-docs：从代码库逆向提取项目文档

## 核心原则

1. **双轨提取** — 规则轨：`inventory` 产出**文件全集**（含未登记语言）+ 符号/端点机器盘点 + 哈希漂移基线；LLM 轨：AI 通读代码建**语义地图**（`.semantic-map.json`，每条带来源文件），两轨汇入同一套 `check` 对账
2. **AI 只写语义层** — 在文件全集/语义地图之上写职责/参数/返回/错误/示例；示例取自测试，禁止编造调用方式；盘点/地图不可靠处显式 `unknown`
3. **证据协议** — 每条结论标注 事实/推断/假设/缺失；拿不准写 `unknown`，禁止伪造（详见 `references/card-filling.md`）
4. **草稿合入** — AI 产出先落 `*.draft.md`，经人工 diff 确认后 `promote` 转正（只更新 AI-GEN 区）；未抽审 draft 不得进正式文档
5. **对账门禁** — 覆盖对账 `check` ERROR=0（无 orphan/phantom/stale/登记腐化）才可宣告完成；语言指纹未覆盖的符号走 `register` 显式登记（必须 file:line 真实）即不算 phantom
6. **权威来源去重** — 目标项目已有的 docs/rules/README 等同类内容：摘录 + 链接，不复制、不覆盖、不双轨
7. **分层顺序硬约束** — 架构（定位）→ 模块/接口 → 数据，逐层确认，禁止跳过定位层直接写接口
8. **只检查/生成文档，不改代码**；git 项目文档与代码同一次提交入库；**非 git 项目**降级为"同一批变更 + 用户确认快照"（漂移由文件哈希 manifest 检出）

## 系统要求（跨平台）

- Python 3.12+，运行时依赖 tree-sitter（v1.5 起全语言统一抽取；安装器自动 `pip install -r skills/dev-docs/requirements.txt`，手动安装亦可）。Windows / Linux / macOS 原生可跑。
- Windows 若没有 `python3` 启动名，用 `python` 或 `py -3` 替代下文命令中的 `python3`。
- 生成产物统一 LF 换行（脚本跨平台固定 `newline="\n"`）：任何平台生成结果字节一致，
  确定性（byte-identical）与 git diff / CI 漂移检测不受换行符影响。
- 全仓库安装：`install.py`（跨平台，`python install.py --project <path>` 或 `--global`）；
  Git Bash / WSL 下亦可 `bash install.sh`。bash 脚本（install.sh/uninstall.sh 等）需要 Git Bash/WSL。

## 产物契约

目标项目默认输出到 `<目标项目>/docs/dev-docs/`（可在调用时用 `--out` 指定其它子目录名）：

```text
<目标项目>/docs/dev-docs/
├── index.md                    # L1 总览：定位/能力矩阵/文档树/阅读路径/覆盖率（机器渲染）
├── architecture.md             # L2 架构：上下文/构建块白盒卡/运行时场景/横切决策/术语
├── usage.md                    # L2 上手：安装启动/配置/常见任务/扩展点/故障排查
├── reference/<module-slug>.md  # L3 每模块一页：概览四问/组件与协作/使用指南/对外接口面/符号索引/详细契约
├── data/<module-slug>.md       # 可选：数据层/对象模型（默认不生成）
├── .devdocs-plan.json          # ★ 页面树（plan 命令维护：pages[] 含 slug/type/title/parent/purpose/sections/source_files/status）
├── inventory.json              # 机器盘点：文件全集 + 符号/端点 + 哈希（唯一事实源，自动生成）
├── .semantic-map.json          # ★ LLM 语义地图（AI 产出 + 用户确认；文件归属/关键符号/接口契约）
├── .registered.json            # 人工/低置信登记（register 命令维护，必须 file:line 源码证据）
└── .baseline.json              # 生成基线（自动维护，防漂移；含全文件哈希）
```

页面树（DeepWiki 对齐）：`index` 为根 → `architecture` / `usage` / `reference/*` 为子页；`plan` 默认启用，
重复运行只新增缺失页、**保留人工编辑**（title/purpose/sections 不被覆盖）。

文档集类型与稳定 ID：

| 类型 | 目录 | doc_id | 对应 inventory |
|:---|:---|:---|:---|
| 总览 | index.md | INDEX | —（机器渲染文档树 + 覆盖率） |
| 架构 | architecture.md | ARCH-001 | modules/deps + 语义地图 |
| 上手 | usage.md | USAGE-001 | 入口/配置/测试（AI 填） |
| 详档 | reference/<module-slug>.md | MOD-xxx | modules[] + symbols[]（FUN）+ endpoints[]（API）+ .registered.json（SYM/EPT/ITF） |
| 数据 | data/<module-slug>.md | DATA-MOD-xxx | 对象/字段（可选） |

- 每个公开符号/端点卡片带**隐藏锚点** `<!-- @FUN-135 -->`（标题只写语义名；check 解析注释锚点统计登记，删除即 orphan）
- **每页统一格式**：标题 + purpose + `**相关源文件**` 头 → AI 叙事节（`<!-- AI-FILL:… -->` 工单引导）→ AI-GEN 机器区 → 人工补充 → `## Sources`（机器汇总本页引用）
- **AI 要填的叙事节位于 AI-GEN 区之外**：重跑 extract 只刷新机器区，已填语义永不被冲掉
- 每文件头部 frontmatter（工具维护）：`doc_id / type / module_id / plan_slug / source_commit / generated_at / inventory_hash / status`
- AI-GEN 区 `<!-- AI-GEN:BEGIN --> … <!-- AI-GEN:END -->` 内可被重生成覆盖；区外是 AI 填写 + 人工补充区，**永远保留**

## 执行流程

### 0. 前置
- 声明文档语言（zh-CN/en-US，标识符不翻译）；向用户确认目标项目根目录（git 根）与排除范围（生成代码/vendor/密钥文件）
- ROS/catkin 工作空间自动嗅探（发现 `package.xml` 即按 catkin 处理，排除 build/devel/install/log）；必要时 `--project-type catkin|generic` 显式指定
- 若 `docs/dev-docs/` 已存在 `.baseline.json`：本次为**增量/再提取**，先读 baseline 与 index 了解现状

### 1. 机器盘点（规则轨）
```bash
python3 <skill_dir>/scripts/dev_docs.py inventory --dir <目标项目> [--project-type auto|catkin|generic]
```
- 产出 `inventory.json`：**文件全集**（含未登记语言如 .cpp/.msg/.srv）+ 符号/端点 + 每文件哈希（唯一事实源，AI 不改）
- 向用户展示盘点摘要（project_type/文件全集/模块数/符号数/端点数/测试数/低置信区）
- **语言指纹未覆盖的语言（如 C++）**：文件全集保证"不漏文件"，符号语义由步骤 2 的语义地图覆盖

### 1.5 语义地图（LLM 轨）[确认点⓪]
- AI 按模块分批读码（每批 ≤5-8 文件，优先构建文件/入口/头文件/接口定义；**读取策略，与交付轮次无关**），产出 `.semantic-map.json` 草稿：

```json
{"version": 1,
 "modules": [{"name": "ncu", "path": "src/pkg_core",
   "responsibility": "一句话职责",
   "files": ["src/pkg_core/main.cpp", "src/pkg_core/Demo.srv"],
   "ignored_files": [{"path": "third_party/x.lib", "reason": "vendored"}],
   "key_symbols": [{"name": "NCU::spin", "kind": "method", "file": "src/pkg_core/main.cpp", "line": 12}],
   "interfaces": [{"kind": "srv", "name": "ncu/Demo", "file": "src/pkg_core/Demo.srv"}],
   "depends_on": [], "tests": []}]}
```

- **反推测纪律**：地图里每个条目必须带来源 `file`；签名/字段引用源码原文；看不见的依赖写 "not visible in sources"，禁止凭记忆补
- **用户确认地图后**才进入架构/详档层；`check` 会用地图统计**文件归属覆盖率**并输出未归属文件清单
- 语言指纹未覆盖、但文档需要登记的关键符号 → `register --kind symbol|endpoint|interface --name … --src <文件> --line <行号>`（必须真实 file:line，登记后不算 phantom）

### 2. 架构层（ARCH）[确认点①]
- 读 inventory 的模块与依赖 → 生成 `architecture.md` 草稿（技术栈/模块划分表/数据流图）
- **先通过用户确认再进入模块层**（分层顺序硬约束）

### 3. 详档层（reference）[确认点②③]
- 逐模块（建议每批 ≤3-5 个）生成 `reference/<slug>.md` 草稿：概览四问 + 符号索引表（机器全量）+ 分级契约卡片
- AI 填充：四问、索引表“说明”列（每符号一句话）、逐符号契约（用途/参数/返回/错误；示例取自 `tested_by`/测试文件）
- 每批向用户展示，确认后继续

### 4. 数据层（DATA，可选）
- 对象/字段逐项，缺失写 `unknown`，不省略不伪造

### 5. 对账门禁
```bash
python3 <skill_dir>/scripts/dev_docs.py check --dir <目标项目>
```
- ERROR=0（无 orphan/phantom/stale）才可宣告完成；有问题先补齐/修正，禁止降级绕过

### 6. 合入 + 提交
- 每个 draft 经用户 diff 确认后 `promote` 转正（只更新 AI-GEN 区）
- `report` 重建 index + 刷新 `.baseline.json`（含全文件哈希）；git 项目文档与触发变更**同一次 git 提交**入库；**非 git 项目**降级为"同一批变更 + 用户确认快照"（`check --drift` 用文件哈希基线检出漂移）

## CLI 速记

```text
dev_docs.py inventory --dir <目标项目> [--out <子目录名=dev-docs>] [--exclude 额外排除]
                      [--project-type auto|catkin|generic]
                      # 文件全集 + 符号/端点 + 哈希基线（catkin 自动排除 build/devel/install/log）
dev_docs.py plan     --dir <目标项目> [--write] [--force] [--exclude 模式]
                     # 页面树（默认 dry-run 打印；--write 落盘 .devdocs-plan.json；重跑保留人工编辑）
dev_docs.py brief    --dir <目标项目> --page <slug> [--json]
                     # 页级填写工单：purpose / 章节 / 相关源文件 / 必覆盖锚点 / 候选证据 /
                     # 零引用名称（refs=0，勿凭命名推断用途）/ 撰写要求 / 自检清单
dev_docs.py extract  --dir <目标项目> --layer index|architecture|usage|reference|data|all \
                     [--module MOD-id] [--page <slug>]
                     # 按页面树生成 draft 骨架；只刷新 AI-GEN 机器区，区外已填语义保留
dev_docs.py promote  --dir <目标项目> --file <draft文件>
                     # draft 转正（人工确认后；自动刷新 Sources 并更新页面进度）
dev_docs.py register --dir <目标项目> --kind symbol|endpoint|interface --name <限定名> \
                     --src <相对项目根文件> [--line N] [--signature S] [--note N]
                     # 人工/低置信登记（语言指纹未覆盖时用）；必须真实 file:line，登记后不算 phantom
dev_docs.py check    --dir <目标项目> [--drift] [--strict]
                     # 对账：orphan/phantom/stale/登记腐化/缺页/缺节/引用不存在/行号异常 + 文件覆盖 + 语义填充度；
                     # 另打印 zero_ref / zero_ref_assert（源码零引用却已断言用途）与
                     # smap_ref_error / smap_suspect_term（语义地图编造引用/重词无痕迹——AI 断言产物同样受检）；
                     # --strict 把 warn（AI-FILL 残留、文件未归属、行号异常）升为 ERROR；exit 0=干净
dev_docs.py fixrefs  --dir <目标项目> [--write]
                     # 修正页内 file:line 引用：指向空行/越界=明确错误可自动修；
                     # 指向注释/import 行=疑似偏移只提示（避免误改正当引用）
dev_docs.py report   --dir <目标项目>                  # 刷新 index.md 机器区 + baseline（含全文件哈希）
dev_docs.py audit    --dir <目标项目> [--write] [--sample-refs 25] [--sample-cards 15]
                     # 独立评估工作底稿：抽样"文档原句 vs 源码原文"并排 + 行性质机器判定 +
                     # 卡片签名与盘点比对 + 机器项汇总；确定性输出（无随机，可复现）
```

## 生成规范

按需加载（阶段匹配，不整读）：
- `references/output-contract.md` — 产物契约：目录/锚点/frontmatter/AI-GEN 区/语义地图 schema/引用与行号规则
- `references/card-filling.md` — 卡片填写与证据标注：四级证据标签、unknown 规则、引用硬规范（单行+全路径）、示例质量要求、风格红线
- `references/doc-filler-prompt.md` — 填卡 agent 指令模板（多 agent 分工时整段内嵌 spawn）
- `references/lang-mapping.md` — 语言支持矩阵、入口/契约发现约定、跨语言执行要点
- `references/anti-patterns.md` — 红线 + 分层抽审 checklist
- `references/quality-review.md` — 独立评估清单（交付前语义复核）

对用户反馈"编号难读 / 结构重复 / 分层 / 对外接口文档 / 语言不支持"等问题：按当前版本能力应答
（是已登记的方向/限制），**不现场重构**；开发规划在 spec-workflow `docs/`，不在本 skill 内。

## 红线（违反不得宣告完成，详见 anti-patterns.md）

1. 不得批量一次生成全部文档不 review——按层/批确认
2. 不得把 AI 内容直接写正式文件——一律 draft → promote
3. 不得用记忆代替扫描——事实以 inventory/源码为准
4. 不得伪造/省略——evidence + unknown
5. 不得覆盖人工区（AI-GEN marker 外）与既有权威文档（摘录+链接）
6. 不得在 check 未 ERROR=0 时宣告完成
7. 不得跳层（先架构后接口）
8. 不得跳过页面树与工单——先 `plan` 再 `brief` 再填写；`<!-- AI-FILL -->` 未清空不得宣告完成
9. **不得凭记忆写行号**——叙述段引用源码必须用 `brief`/盘点输出的行号，写后运行 `fixrefs` 校验（指向空行/注释即为偏移）
10. **不得一次只做一半**——文档抽取按「一轮做完」为默认交付方式：页面叙事 + 全部符号/接口卡片语义 + 门禁全绿在同一轮内完成；**不得把剩余页/剩余卡片写成"后续候选"交付**
11. **不得凭命名推断用途**——先查引用计数（inventory `refs` / `zero_refs`；brief 工单列出本页零引用名称）：refs=0 的名称如实写明"未被使用（dead）"或注明疑似框架回调，不得臆造功能

## 交付方式：一轮做完（默认，非例外）

标准链路一条到底，中间不设"先出样例再继续"的停顿（除非用户明确要求先看样例）：

```bash
inventory → plan --write → extract --layer all → （逐页填叙事 + 逐卡片填语义）
          → promote 全部 → report → check --strict（全绿才交）
```

- **语义填充是交付的一部分，不是"后续候选"**：符号/接口卡片的「用途 / 参数 / 返回 / 错误」必须与叙事同轮填尽；`--strict` 下 `semantic_todo_left` 为 ERROR。
- 卡片量大时用**机器辅助**（导出"锚点+签名+源码片段"材料 → 逐条撰写 → 按锚点回写），但**同轮完成**，不因量大而拆轮。
- 「分批」只允许两种情况：①用户显式要求先看样例；②单项目规模确实一轮装不下（须在交付时**显式列出剩余清单与原因**，不得含糊）。
- 模块较多时"分批读码"（见 1.5）是**读取策略**，与交付轮次无关。

## 规模化交付：多 agent 分工（v1.5.6，243 卡实战验证）

**触发条件**：符号卡 > 50 张或页面 > 5 页；否则单 agent 直填（小规模并行得不偿失）。

**所有权铁律**：一个产物文件一个 agent，**零共享写**（并行编辑同一文件必然冲突）；
主 agent 保留：叙事三页（index/architecture/usage 需全局视角与横切一致性）+
装配与门禁收口；填卡 agent 只拿 reference 页 + 对应源码清单。

**标准序**（冷启动与增量一致，两处顺序硬约束）：
1. `inventory → 语义地图 → plan → extract`——语义地图必须在 extract **之前**
   （架构表职责列、文件覆盖率都依赖它）；
2. 填写一律在 **draft 上**进行（红线 2 严格成立），全部填完**统一 promote**——
   Sources 一次刷新到位，避免转正后再改正式文件；
3. `report → check --strict → audit → 独立评估（不同上下文 agent）→ 修 P0 → 交付`。

**填卡 agent 指令**：spawn 时整段内嵌 `references/doc-filler-prompt.md`，
再追加该 agent 的目标文件与源码清单。分派时按文件符号数均衡（单 agent 上限约 140 卡）。

**机制**：主 agent 用 Task 工具 spawn——小批量用同步子 agent（阻塞等结果，最稳）；
大规模并行用 agent team（后台异步，靠轮询文件状态推进，消息回包有延迟）。
自定义角色须在 **workspace 根** `.codebuddy/agents/<name>.md` 落定义文件
（tools/acceptEdits 权限/纪律），spawn 时用 `subagent_path` 指向它；
内置 code-reviewer 直接用于独立评估（不同上下文）。

**团队协议**：完成汇报三件套（填写卡数 / unknown 数与原因 / 源码关键发现 ≤3 条）；
**关键发现是强制项**——写进交付说明供用户决定是否回流代码；全部完成后 shutdown 清理团队。



机器门禁只覆盖**结构、覆盖、行号位置、卡片填尽**；**语义正确性**（事实是否与源码一致、
有无编造 / 张冠李戴 / 把死代码当现行约定）机器无法判定，必须另过一次**独立评估**：

1. 机器链路全绿：`inventory → plan --write → extract --layer all → 填写 → promote 全部 → report → check --strict`
2. **独立评估**（另一 agent / 人，按 `references/quality-review.md` 清单执行）：先跑 `audit`
   生成工作底稿（机器已核行性质 / 签名一致性 / 零引用断言 / 地图引用与重词），
   评估者按底稿核对**语义**：抽 **≥25 处** file:line、**≥15 张**卡片，输出 P0/P1/P2 与抽样统计表
3. **P0（错误 / 编造）修完才交付**；P1 修完或显式列出；P2 记录
4. 交付说明附评估结论与修后状态

省略第 2 步 = 交付"格式正确、语义未验"的半成品（实测曾一次性漏过 4 处 P0：
编造"本地 SQLite 数据库"、把注释掉的代码当真实行为、把调用点注释误配给定义处、
把零引用的死宏当现行约定——机器门禁当时全部为绿）。

## 完成条件（同时满足）

- `check` 退出码 0（orphan/phantom/stale 全清或已显式 retired；无缺页 / 缺节 / 引用文件不存在）
- `fixrefs` 无「空行/越界」类异常（`check --strict` 通过）
- **交付前 `check --strict` 通过**：AI-FILL 填尽 + **卡片语义填尽（`semantic_todo_left`=0）** + 文件归属无遗漏 + 行号无异常
- 每个 draft 经人工确认已 promote；人工区/权威链接已补 why
- index.md 覆盖率摘要已刷新；产物与触发变更同一次提交
