# 测试策略指南

## 测试金字塔

```
         /\
        /  \        端到端测试（少）
       /    \
      /------\      集成测试（中）
     /        \
    /----------\    单元测试（多）
   /____________\
```

## 单元测试策略

### 测试什么

- 公共函数/方法
- 边界条件
- 错误处理路径
- 核心业务逻辑

### 不测什么

- 框架代码
- 第三方库
- 简单的 getter/setter
- 纯 UI 样式

### 测试命名规范

```
<被测函数名>_<场景>_<期望结果>
```

示例：
- `calculateDiscount_validCoupon_returnsDiscount`
- `login_wrongPassword_returnsError`
- `parseInput_emptyString_throwsException`

### 测试结构（AAA 模式）

```python
def test_example():
    # Arrange — 准备测试数据
    user = User(name="test")
    
    # Act — 执行被测函数
    result = greet(user)
    
    # Assert — 验证结果
    assert result == "Hello, test"
```

## 集成测试策略

### 测试场景

- 模块间接口调用
- API 端到端请求
- 数据库读写操作
- 外部服务交互（mock）

### 集成测试原则

- 测试真实业务流程
- 使用测试数据库，不污染生产数据
- Mock 外部依赖，不依赖网络
- 每个测试独立，可并行执行

## 覆盖率标准

| 类型 | 目标 |
|------|------|
| 整体覆盖率 | ≥ 80% |
| 关键业务路径 | ≥ 90% |
| 新增代码 | ≥ 85% |

## 测试报告格式

```markdown
## 测试报告

### 概览
- 总用例数：XX
- 通过：XX
- 失败：XX
- 跳过：XX
- 覆盖率：XX%

### 失败用例
| 用例名 | 失败原因 | 严重等级 |
|--------|----------|----------|
| xxx | xxx | HIGH |

### 覆盖率详情
| 模块 | 行覆盖率 | 分支覆盖率 |
|------|----------|------------|
| xxx | XX% | XX% |
```
