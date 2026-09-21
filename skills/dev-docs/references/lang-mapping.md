# references/lang-mapping.md — 语言能力与发现约定（v1.5 适配器架构）

> dev-docs v1.5 起**全语言统一走 tree-sitter 适配器**：每种语言一个
> `LanguageAdapter` 子类（`scripts/dev_langs/`），全部 reliable 档，
> 不再有"低置信启发式"。tree-sitter 为运行时硬依赖。

## 1. 语言支持矩阵

| 语言 | 扩展名 | 适配器 | 能力 | 备注 |
|:---|:---|:---|:---|:---|
| Python | `.py` | `python_ts` | reliable | 顶层函数 + 类方法；`_` 私有不入表；装饰器路由端点（FastAPI/Flask） |
| C++ | `.cpp .cc .cxx .hpp .hh .h` | `cpp_ts` | reliable | qualified 名拆 `::` → 类归属；匿名命名空间跳过；宏调用不产符号 |
| C | `.c` | `c_ts` | reliable | 复用 cpp 规则（c 语法） |
| Java | `.java` | `java_ts` | reliable | `@*Mapping` 注解端点 |
| JavaScript | `.js .mjs .cjs` | `js_ts` | reliable | 函数/类方法/箭头函数赋值 |
| TypeScript | `.ts .tsx` | `js_ts` | reliable | 同上（typescript/tsx 语法） |
| Shell | `.sh` | `bash_ts` | reliable | 函数 + `source` 依赖 |
| ROS msg/srv | `.msg .srv` | `text_msgsrv` | reliable（text） | 唯一非 tree-sitter 路径：无官方 grammar；产出 `inventory.interfaces`（MSG-/SRV-）并纳入对账 |
| Go/Rust/Ruby/PHP/Kotlin… | — | 未注册 | unsupported | 文件仍进 L0 全集（防漏锚）；用语义地图归属 + `register` 登记（SYM/EPT/ITF）兜底 |

## 2. 入口与契约发现约定（AI 补 architecture/模块四问时用）

| 形态 | 后端服务 | CLI | ROS 节点 | 库/SDK |
|:---|:---|:---|:---|:---|
| 入口 | `main.py`/Spring `Application` | `cmd/`、`__main__.py` | `ros::init` + `ros::spin`（节点 main） | `__init__.py`、export |
| 契约来源 | 路由注册 + handler 签名 | argparse/cobra/clap 子命令 | 话题 pub/sub + .msg/.srv 接口定义 | `__all__`/export |
| 数据模型 | ORM Model / dataclass | 配置文件 | `.msg` 字段 / struct（database.h 式协议结构体） | 类型声明 |
| 测试约定 | **跨语言统一**（v1.5）：位于 `tests/`/`test/`/`__tests__/`/`spec/` 目录，或文件名形如 `test.cpp`/`test_*.py`/`*_test.cpp`/`tests.py` → 只入 `tests` 索引，**不生成符号卡片**（`dev_langs.base.is_test_file`） | 同上 | rostest 同理按文件名判定 | 同上 |

## 3. 跨语言执行要点

1. 起手先跑 `dev_docs.py inventory` 看 `langs`：v1.5 全部为 reliable（tree-sitter 支持矩阵内）。
2. 未注册语言（unsupported）的目录仍受"文件归属 100%"门禁约束——语义地图显式归属或声明 ignored。
3. 一个仓库多语言：按语言分节写 architecture；模块文档标注该模块语言（`reference` 页头部 `lang` 字段）。
4. 依赖分析（v1.5 已实现）：python import 与 C++ `#include` 自动映射为 `modules[].deps`（包内互引）；CMake `find_package` 与 `package.xml` 的 depend 系标签 → `modules[].external_deps`；ROS 调用形态（`advertise`/`subscribe`/`advertiseService`/`serviceClient`/`ros::init` 与 rospy 对应形态）→ `inventory.interfaces` 的 `TOP-/SVC-/NDE-` 条目。剩余细节由 AI 在 architecture/模块四问里补（evidence: 事实——文件路径）。
5. **行号与证据纪律（v1.5.2+）**：①引用行号必须是**代码行**——指向注释 / 字符串（含 `'''…'''` / `/* … */` 内被注释掉的代码）会被 `check --strict` 的 `ref_line_suspect` 拦下；②填卡先用 `brief` 的**候选证据**（定义处前置注释 / 声明行尾注释 / docstring / 字段注释），**不要用调用点注释解释定义处功能**；③常量/函数先看是否被引用（`refs` / `zero_refs`），**零引用不得写成现行约定**；④CMake 声明的 msg/srv/可执行若缺文件，`inventory` 会记 `build_issues`（构建风险，须在文档里提示读者）。
6. ROS 形态：`advertise/subscribe/ServiceServer` 的自动化识别已覆盖常见调用（TOP-/SVC-/NDE- 条目）；未覆盖形态由 AI 读源码补（evidence 纪律不变）。
