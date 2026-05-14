# 根因追踪（Root Cause Tracing）

## 概述

bug 往往表现在调用栈深处（git init 跑到了错误的目录、文件被创建在错误的位置、数据库用错误的路径打开）。你的直觉是在错误出现的地方修复它，但那是在治标。

**核心原则：** 沿着调用链逆向追踪，直到找到最初的触发点，然后在源头修复。

## 何时使用

```dot
digraph when_to_use {
    "Bug appears deep in stack?" [shape=diamond];
    "Can trace backwards?" [shape=diamond];
    "Fix at symptom point" [shape=box];
    "Trace to original trigger" [shape=box];
    "BETTER: Also add defense-in-depth" [shape=box];

    "Bug appears deep in stack?" -> "Can trace backwards?" [label="yes"];
    "Can trace backwards?" -> "Trace to original trigger" [label="yes"];
    "Can trace backwards?" -> "Fix at symptom point" [label="no - dead end"];
    "Trace to original trigger" -> "BETTER: Also add defense-in-depth";
}
```

**在以下情况使用：**
- 错误发生在执行的深处（而不是入口点）
- stack trace 显示出很长的调用链
- 不清楚无效数据从何而来
- 需要找出是哪个测试/代码触发了问题

## 追踪过程

### 1. 观察症状
```
Error: git init failed in /Users/jesse/project/packages/core
```

### 2. 找到直接原因
**是哪段代码直接导致了它？**
```typescript
await execFileAsync('git', ['init'], { cwd: projectDir });
```

### 3. 追问：是什么调用了它？
```typescript
WorktreeManager.createSessionWorktree(projectDir, sessionId)
  → called by Session.initializeWorkspace()
  → called by Session.create()
  → called by test at Project.create()
```

### 4. 持续向上追踪
**传入的是什么值？**
- `projectDir = ''`（空字符串！）
- 空字符串作为 `cwd` 会解析为 `process.cwd()`
- 那就是源码目录！

### 5. 找到最初的触发点
**空字符串从哪里来？**
```typescript
const context = setupCoreTest(); // Returns { tempDir: '' }
Project.create('name', context.tempDir); // Accessed before beforeEach!
```

## 添加 stack trace

当你无法手动追踪时，加入埋点（instrumentation）：

```typescript
// Before the problematic operation
async function gitInit(directory: string) {
  const stack = new Error().stack;
  console.error('DEBUG git init:', {
    directory,
    cwd: process.cwd(),
    nodeEnv: process.env.NODE_ENV,
    stack,
  });

  await execFileAsync('git', ['init'], { cwd: directory });
}
```

**关键：** 在测试中使用 `console.error()`（不要用 logger —— 它可能不会显示）

**运行并捕获：**
```bash
npm test 2>&1 | grep 'DEBUG git init'
```

**分析 stack trace：**
- 寻找测试文件名
- 找到触发该调用的行号
- 识别出模式（同一个测试？同一个参数？）

## 找出是哪个测试造成了污染

如果某些东西在测试期间出现，但你不知道是哪个测试导致的：

使用本目录下的二分查找脚本 `find-polluter.sh`：

```bash
./find-polluter.sh '.git' 'src/**/*.test.ts'
```

逐个运行测试，在第一个造成污染的测试处停下。用法见脚本。

## 真实示例：空的 projectDir

**症状：** `.git` 被创建在 `packages/core/`（源码目录）中

**追踪链：**
1. `git init` 在 `process.cwd()` 中运行 ← cwd 参数为空
2. WorktreeManager 被传入空的 projectDir
3. Session.create() 传入了空字符串
4. 测试在 beforeEach 之前访问了 `context.tempDir`
5. setupCoreTest() 初始返回 `{ tempDir: '' }`

**根因：** 顶层变量初始化时访问了空值

**修复：** 将 tempDir 改为一个 getter，在 beforeEach 之前被访问时抛出错误

**同时添加了纵深防御：**
- Layer 1：Project.create() 校验目录
- Layer 2：WorkspaceManager 校验非空
- Layer 3：NODE_ENV 守卫拒绝在 tmpdir 之外执行 git init
- Layer 4：在 git init 之前记录 stack trace

## 关键原则

```dot
digraph principle {
    "Found immediate cause" [shape=ellipse];
    "Can trace one level up?" [shape=diamond];
    "Trace backwards" [shape=box];
    "Is this the source?" [shape=diamond];
    "Fix at source" [shape=box];
    "Add validation at each layer" [shape=box];
    "Bug impossible" [shape=doublecircle];
    "NEVER fix just the symptom" [shape=octagon, style=filled, fillcolor=red, fontcolor=white];

    "Found immediate cause" -> "Can trace one level up?";
    "Can trace one level up?" -> "Trace backwards" [label="yes"];
    "Can trace one level up?" -> "NEVER fix just the symptom" [label="no"];
    "Trace backwards" -> "Is this the source?";
    "Is this the source?" -> "Trace backwards" [label="no - keeps going"];
    "Is this the source?" -> "Fix at source" [label="yes"];
    "Fix at source" -> "Add validation at each layer";
    "Add validation at each layer" -> "Bug impossible";
}
```

**绝不要只在错误出现的地方修复。** 逆向追踪，找到最初的触发点。

## stack trace 小贴士

**在测试中：** 使用 `console.error()` 而不是 logger —— logger 可能被抑制
**在操作之前：** 在危险操作之前记录日志，而不是在它失败之后
**包含上下文：** 目录、cwd、环境变量、时间戳
**捕获调用栈：** `new Error().stack` 能展示完整的调用链

## 真实世界的成效

来自调试会话（2025-10-03）：
- 通过 5 层追踪找到了根因
- 在源头修复（getter 校验）
- 添加了 4 层防御
- 1847 个测试通过，零污染
