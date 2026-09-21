---
doc_id: {doc_id}
type: reference
module_id: {module_id}
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

<!-- AI-FILL:REF-OVERVIEW 概览（四问）—— 读上列源文件，回答：做什么 / 为何存在 / 依赖什么 / 谁依赖它。每问一行，结论标 evidence（事实需带 file:line）。填完删除本注释 -->
## 概览（四问）

- **做什么**：<!-- TODO AI 依源码填写（一句话，evidence: 事实——file:line） -->
- **为何存在**：<!-- TODO AI 依源码填写 -->
- **依赖什么**：{deps}
- **谁依赖它**：<!-- TODO AI 依源码填写 -->

<!-- AI-FILL:REF-COMPONENTS 组件与协作 —— 1) 组件清单（类/协议/关键对象，各带 file:line）2) 装配期协作链与运行期协作链（A → B → C，每步 file:line）3) 关键设计要点/不变量。填完删除本注释 -->
## 组件与协作

<!-- TODO AI 依源码填写（组件清单 + 两条协作链 + 设计要点；evidence: 事实（file:line）/ 推断） -->

<!-- AI-FILL:REF-USAGE 使用指南 —— 1) 典型用法（示例必须取自测试或真实调用点，注明 文件::用例）2) 扩展点（新增一个 X 的编号步骤）。填完删除本注释 -->
## 使用指南

<!-- TODO AI 依源码填写（典型用法示例 + 扩展点步骤；示例注明来源） -->

<!-- AI-FILL:REF-SURFACE 对外接口面 —— 表格：名称｜签名/形状｜契约（参数/返回/错误）｜保持不变量。只列对外可用接口。填完删除本注释 -->
## 对外接口面

| 名称 | 签名/形状 | 契约 | 备注 |
|:---|:---|:---|:---|
<!-- TODO AI 依源码填写（只列对外可用接口） -->

<!-- AI-GEN:BEGIN -->
## 本页速览（机器渲染）

- {ref_summary}

## 符号索引

| ID | 符号 | 类型 | 位置 | 说明 |
|:---|:---|:---|:---|:---|
{index_rows}

## 详细契约

<!-- 分级组织：模块级函数 → 类 → 端点。签名/路径取自机器盘点勿手改；卡片语义处按批填充，填完删除对应 TODO -->
{detail_rows}
<!-- AI-GEN:END -->

## 人工补充（机器不覆盖）

<!-- TODO: 业务背景 / 取舍 / 与其他模块的关系故事，由人工填写（evidence: 人类） -->

## Sources

<!-- SOURCES:AUTO -->
