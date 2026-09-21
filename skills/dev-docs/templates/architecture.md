---
doc_id: {doc_id}
type: architecture
plan_slug: {plan_slug}
source_commit: {commit}
generated_at: {generated_at}
inventory_hash: {inventory_hash}
status: {status}
---

# {title}

> {purpose}

**页面导航**：{nav}

**相关源文件**：{source_files}

<!-- AI-FILL:ARCH-CONTEXT 系统上下文 —— 1) 使用者与触发方式（谁在什么场景启动/调用系统）2) 外部交互清单（外部服务/存储/设备，各带 file:line）3) 边界图（文本或 mermaid，标出系统内外）。填完删除本注释 -->
## 系统上下文

<!-- TODO AI 依源码填写（使用者/触发方式/外部交互清单 + 边界图，evidence: 事实——file:line） -->

## 构建块视图

<!-- AI-GEN:BEGIN -->
{module_rows}
<!-- AI-GEN:END -->

<!-- AI-FILL:ARCH-BLOCKS 模块白盒卡 —— 为上表每个模块写一张卡，统一用下列五要素骨架（读不到的要素写 unknown，不省略）：

#### <模块名>
- 职责：一句话（据 file:line）
- 对外接口：调用方能用什么（命令 / 话题 / 函数 / HTTP 路由，各带出处）
- 依赖：它调用谁（内部模块 + 外部库）
- 被依赖：谁调用它（搜不到写「未被内部调用」）
- 内部结构：关键文件/类一行一个（file:line + 一句话）

组件关系图另起一节。填完删除本注释 -->
### 模块白盒卡

<!-- TODO AI 依源码填写（每模块一张卡，evidence: 事实——入口文件 file:line） -->

<!-- AI-FILL:ARCH-RELATIONS 组件关系图 —— 用 mermaid 或文本画出模块/组件之间的依赖与数据流（连线带语义：调用/事件/数据）；组件名必须与语义地图 components 一致。填完删除本注释 -->
### 组件关系图

<!-- TODO AI 依源码填写（关系优先于罗列；只画真实存在的依赖） -->

<!-- AI-FILL:ARCH-RUNTIME 运行时场景 —— 至少 2 个端到端场景（如一次请求/一次任务执行）：分步骤序列，每步写清 谁调用谁 + file:line。填完删除本注释 -->
## 运行时场景

### 场景一：<!-- TODO AI 依源码填写（场景名） -->

<!-- TODO AI 依源码填写（编号步骤序列，每步 file:line；可配 mermaid sequence） -->

### 场景二：<!-- TODO AI 依源码填写（场景名） -->

<!-- TODO AI 依源码填写（编号步骤序列，每步 file:line） -->

<!-- AI-FILL:ARCH-CROSSCUTTING 横切概念与决策 —— 安全边界 / 持久化 / 错误处理约定 / 关键取舍（why 标「人类待确认」）。填完删除本注释 -->
## 横切概念与决策

<!-- TODO AI 依源码填写（安全/持久化/错误约定；取舍的 why 标「人类待确认」） -->

<!-- AI-FILL:ARCH-GLOSSARY 术语表 —— 8-20 条项目专属术语（非通用编程词），每条一句话。填完删除本注释 -->
## 术语表

| 术语 | 含义 |
|:---|:---|
<!-- TODO AI 依源码填写（项目专属术语，8-20 条） -->

## 人工补充（机器不覆盖）

<!-- TODO: 设计动机 / why / 历史包袱 / 相关 ADR 链接，由人工填写（evidence: 人类） -->

## Sources

<!-- SOURCES:AUTO -->
