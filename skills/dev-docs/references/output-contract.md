# references/output-contract.md — 产物契约

> dev-docs 所有产物的结构、ID、元数据约定。机器按此解析，AI 按此填写。

## 1. 落位与目录

- 目标项目默认 `<目标项目>/docs/dev-docs/`（可用 `--out` 改子目录名）；内容随目标项目 git 入库。
- `inventory.json` / `.baseline.json` / `.devdocs-plan.json` 为机器维护，**人工不手改**。
- 目标项目已有同类文档/规则（docs、README、ADR）：**摘录 + 链接，不复制、不覆盖**；冲突以项目自有文档为准。
- 文件命名用**语义 slug**（模块 path 转写：`demo.md`、`demo-evaluation.md`；根模块 `root.md`）；MOD 编号只留在 frontmatter `doc_id`。

## 2. 稳定 ID 与锚点

- ID 由 inventory 分配，唯一且稳定：`FUN-xxx`（函数/方法/类）、`API-xxx`（端点）、`MOD-xxx`（模块）、`TOP-/SVC-/NDE-/MSG-/SRV-`（接口与绑定）。
- 人工登记（register 命令）分配 `SYM-/EPT-/ITF-`，存于 `.registered.json`。
- **登记锚点格式**：卡片标题只写语义名，ID 以隐藏注释放签名/handler 行尾：`<!-- @FUN-135 -->`。删除符号卡片 = check 报 orphan。
- **对账口径**：登记集 = inventory 自动枚举 ∪ 人工登记；文档锚点不在登记集 → phantom；register 条目指向的源文件消失 → ERROR。

## 3. 语义地图（.semantic-map.json，LLM 产出）

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

- AI 分批读码产出、**用户确认后**生效；`check` 用它统计文件归属覆盖率并输出未归属文件清单（缺地图仅提示不门禁）。
- 纪律：每个条目必须带来源 `file`；签名/字段引用源码原文；看不见的写 "not visible in sources"，禁止凭记忆补。
- 地图是 **AI 断言产物**：其职责描述渲染到架构页时机器自动标注「AI 断言·待核」；`check` 另对其 file:line 引用与重词（数据库/缓存等）做提示。

## 4. 页面树（.devdocs-plan.json，plan 命令维护）

- 层级：`index`（根）→ `architecture` / `usage` / `reference/<slug>`；`data` 默认不生成。
- 页字段：`slug / type / module_id / title / parent / purpose / sections / source_files / status`。
  `purpose` = 该页要回答什么（brief 输出）；`sections` = 结构门禁必需章节；
  `status`：`planned` → `generated`（有文件，语义未填尽）→ `filled`。
- 人工可编辑：重跑 `plan` 只新增缺失页，已有页的 title/purpose/parent/sections/status 不被覆盖（`--force` 除外）。

## 5. 引用与行号（可校验声明）

- 页内 `路径:行号` 引用 `check` 逐条核：
  - `ref_file_missing`：文件不在项目文件全集内 → **ERROR**（编造拦截）；
  - `ref_line_suspect`：行号指向**空行 / 注释行 / 越界 / 字符串内** → WARN（`--strict` 为 ERROR）；import 行上下文未讲"依赖"同样报疑似。
- `fixrefs [--write]`：空行/越界类按"最近的 def/class/赋值行"自动修正；注释/import 类只提示，避免改坏正当引用。
- 纪律：**行号来自机器输出**（brief 锚点、盘点卡片、Sources），不凭记忆手写；写完一批跑一次 `fixrefs`。

## 6. frontmatter（每文件头部，机器维护）

```yaml
---
doc_id: MOD-001
type: reference
module_id: MOD-001
plan_slug: reference/demo
source_commit: a1b2c3d   # 生成时目标项目 HEAD（git）；非 git 为空
generated_at: 2026-09-06T12:00:00+08:00
inventory_hash: <sha256>
status: current           # draft → current；废弃标 retired
---
```

人工只关心正文，不手改 frontmatter（重跑自动刷新）。

## 7. AI-GEN 区与 draft 流程

```markdown
<!-- AI-GEN:BEGIN -->
…机器/AI 生成区：符号索引、详细契约、覆盖率，再次 extract 时整体刷新…
<!-- AI-GEN:END -->

## 人工补充（机器不覆盖）
<!-- 区外内容（AI 叙事节、人工补充）增量重生成时全部保留 -->
```

- 生成/再生成一律先写 `xxx.md.draft`；人工 diff 确认后 `promote` 才覆盖正式文件。
- **禁止**直接编辑 AI-GEN 区内的机器字段；AI 语义填在对应 TODO 之后（规则见 card-filling.md）。

## 8. 确定性与生命周期

- `inventory.json` 不含时间戳：同一代码两次盘点 byte-identical（CI 漂移检测的前提）；产物统一 LF。
- 默认排除 `.git/node_modules/__pycache__/dist/build/…`，`--exclude` 追加（生成代码/密钥/CI 产物按项目加）。
- 模块被删除：文档 `status: retired` 保留历史，不悄悄抹掉；重复提取 = 增量（`extract --module` 只重写该模块 draft）。
