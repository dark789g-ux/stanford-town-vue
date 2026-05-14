# 纵深防御式校验（Defense-in-Depth Validation）

## 概述

当你修复一个由无效数据引起的 bug 时，在某一处加上校验会让人觉得已经够了。但那一处校验可能被不同的代码路径、重构（refactoring）或 mock 绕过。

**核心原则：** 在数据经过的每一层都进行校验。让这个 bug 在结构上变得不可能发生。

## 为什么要多层

单点校验：“我们修复了这个 bug”
多层校验：“我们让这个 bug 不可能发生”

不同的层捕获不同的情况：
- 入口校验捕获大多数 bug
- 业务逻辑捕获边界情况（edge case）
- 环境守卫（environment guard）防止特定上下文中的风险
- 调试日志在其他层都失效时提供帮助

## 四个层次

### Layer 1：入口点校验（Entry Point Validation）
**目的：** 在 API 边界处拒绝明显无效的输入

```typescript
function createProject(name: string, workingDirectory: string) {
  if (!workingDirectory || workingDirectory.trim() === '') {
    throw new Error('workingDirectory cannot be empty');
  }
  if (!existsSync(workingDirectory)) {
    throw new Error(`workingDirectory does not exist: ${workingDirectory}`);
  }
  if (!statSync(workingDirectory).isDirectory()) {
    throw new Error(`workingDirectory is not a directory: ${workingDirectory}`);
  }
  // ... proceed
}
```

### Layer 2：业务逻辑校验（Business Logic Validation）
**目的：** 确保数据对当前操作是合理的

```typescript
function initializeWorkspace(projectDir: string, sessionId: string) {
  if (!projectDir) {
    throw new Error('projectDir required for workspace initialization');
  }
  // ... proceed
}
```

### Layer 3：环境守卫（Environment Guards）
**目的：** 防止在特定上下文中执行危险操作

```typescript
async function gitInit(directory: string) {
  // In tests, refuse git init outside temp directories
  if (process.env.NODE_ENV === 'test') {
    const normalized = normalize(resolve(directory));
    const tmpDir = normalize(resolve(tmpdir()));

    if (!normalized.startsWith(tmpDir)) {
      throw new Error(
        `Refusing git init outside temp dir during tests: ${directory}`
      );
    }
  }
  // ... proceed
}
```

### Layer 4：调试埋点（Debug Instrumentation）
**目的：** 捕获上下文以供事后取证（forensics）

```typescript
async function gitInit(directory: string) {
  const stack = new Error().stack;
  logger.debug('About to git init', {
    directory,
    cwd: process.cwd(),
    stack,
  });
  // ... proceed
}
```

## 应用这个模式

当你发现一个 bug 时：

1. **追踪数据流** —— 错误的值从哪里来？在哪里被使用？
2. **梳理所有检查点** —— 列出数据经过的每一个点
3. **在每一层添加校验** —— 入口、业务、环境、调试
4. **测试每一层** —— 尝试绕过 Layer 1，验证 Layer 2 能捕获它

## 来自实际会话的示例

bug：空的 `projectDir` 导致 `git init` 在源码目录中执行

**数据流：**
1. 测试 setup → 空字符串
2. `Project.create(name, '')`
3. `WorkspaceManager.createWorkspace('')`
4. `git init` 在 `process.cwd()` 中运行

**添加的四层：**
- Layer 1：`Project.create()` 校验非空 / 存在 / 可写
- Layer 2：`WorkspaceManager` 校验 projectDir 非空
- Layer 3：`WorktreeManager` 在测试中拒绝在 tmpdir 之外执行 git init
- Layer 4：在 git init 之前记录 stack trace

**结果：** 全部 1847 个测试通过，该 bug 无法复现

## 关键洞察

这四层全都是必要的。在测试过程中，每一层都捕获到了其他层漏掉的 bug：
- 不同的代码路径绕过了入口校验
- mock 绕过了业务逻辑检查
- 不同平台上的边界情况需要环境守卫
- 调试日志识别出了结构性的误用

**不要在一个校验点就停下。** 在每一层都加上检查。
