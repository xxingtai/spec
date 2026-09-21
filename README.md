# spec

WorkBuddy Agent Skills 集合：以 **Spec 驱动开发（spec-driven development）** 为核心的一组可复用技能，
覆盖「需求 → 设计 → 实现 → 测试 → 评审 → 文档 → 提交」全链路。

## 技能列表

| Skill | 版本 | 一句话说明 |
|:---|:---|:---|
| [`spec-dev-workflow`](skills/spec-dev-workflow/) | 0.4.1 | 8 阶段 spec 驱动开发流水线，由编排引擎 `spec_cli.py` 驱动，每阶段带机器门禁、状态机与断点续传 |
| [`spec-health-check`](skills/spec-health-check/) | 0.3.1 | 对已有 spec 目录做双层体检（脚本层结构一致性 + 模型层文档质量），输出 0-100 健康度评分与返修指引 |
| [`dev-docs`](skills/dev-docs/) | 1.5.6 | 从存量代码库反向生成技术文档（总览 / 架构 / 上手 / 各模块详档），支持漂移检测与证据标注 |

## 目录结构

```
spec/
├── README.md
└── skills/
    ├── spec-dev-workflow/     # 8 阶段开发流水线
    │   ├── SKILL.md
    │   ├── _meta.json
    │   ├── pipelines/         # 声明式流水线定义
    │   ├── references/        # 需求/设计/测试/评审指南
    │   ├── scripts/           # spec_cli.py 编排引擎
    │   └── templates/         # 00-index ~ 08-commit 阶段模板
    ├── spec-health-check/     # spec 健康度检查
    │   ├── SKILL.md
    │   ├── _meta.json
    │   ├── references/        # 评分细则
    │   └── scripts/           # health-check.py / .sh
    └── dev-docs/              # 代码反建文档
        ├── SKILL.md
        ├── _meta.json
        ├── requirements.txt
        ├── references/        # 填写规范 / 语言映射 / 输出契约
        ├── scripts/           # dev_docs.py, dev_inventory.py, dev_langs/
        └── templates/         # index / architecture / usage / reference / data
```

## 安装

每个技能都是自包含目录，复制到 skills 目录即可：

```bash
# 用户级（对所有项目生效）
git clone https://github.com/xxingtai/spec.git
cp -r spec/skills/* ~/.workbuddy/skills/

# 或只装某一个
cp -r spec/skills/spec-dev-workflow ~/.workbuddy/skills/
```

装好后在对话中直接说触发词即可唤起，例如：

- `spec-dev-workflow`：开始开发 / 初始化开发 / 开发第 N 阶段 / 继续开发 / 查进度
- `spec-health-check`：检查 spec / spec 健康度 / 体检 spec / 验收 spec
- `dev-docs`：给项目生成文档 / 反建文档 / 补接口文档 / 补架构文档

## 环境要求

- Python 3.7+（`spec_dev_workflow` 编排引擎、`spec-health-check`、`dev-docs` 均为纯 Python 实现，跨平台）
- Windows 下若没有 `python3` 启动名，用 `python` 或 `py -3` 替代
- 仅 `dev-docs` 需要额外依赖，见 `skills/dev-docs/requirements.txt`

## 备注

- `_meta.json` 保留各技能的版本与作者信息，便于后续增量同步。
- 仓库内的 `skills/` 为发布源；本机可直接从 `~/.workbuddy/skills/` 复制更新后再提交。
