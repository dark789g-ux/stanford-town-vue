# 根目录 `pnpm run dev` 编排：按顺序启动后端、前端

- 日期：2026-05-14
- 状态：已批准，待实现

## 目标

在仓库**根目录**执行 `pnpm run dev` 时，先启动后端、待其健康后再启动前端，
单条命令拉起完整开发环境。

## 背景

当前仓库根目录没有 `package.json`；开发者需分别进入 `backend/` 和 `frontend/`
手动启动两个进程。后端是单个 FastAPI 进程（`uvicorn app.main:app`），冷启动时会跑
`alembic upgrade head` 并加载 vendored 仿真器，需要数秒；后端暴露 `GET /api/health`
可用于就绪探测。前端是 Vite 开发服务器，会把 `/api /ws /static /assets` 代理到 `:8000`。

开发者运行环境：Windows + PowerShell；`uvicorn` 安装在全局 Python 3.12（仓库内**无** `.venv`）；
`pnpm` 10.x、Node 24.x。

## 方案

采用**零依赖 Node 编排脚本**：根目录新建 `package.json`（仅含脚本入口、无 npm 依赖），
配套 `scripts/dev.mjs` 用 Node 内置 `child_process` 启动并编排两个进程。

不引入 `concurrently` / `wait-on` 等第三方包——根目录因此**无需** `pnpm install`，
且健康轮询、超时、Windows 进程树清理都能精确控制。

## 新增文件

### `package.json`（仓库根，新建）

只含脚本入口，`"private": true`，无 `dependencies` / `devDependencies`：

```json
{
  "name": "stanford-town-vue",
  "private": true,
  "scripts": {
    "dev": "node scripts/dev.mjs"
  }
}
```

### `scripts/dev.mjs`（新建）

编排脚本，与现有 `scripts/flatten_map.py`、`scripts/copy_assets.py` 并列。

## 编排行为

### 启动前预检（fail-fast）

启动任何进程前先校验环境，缺失则打印明确提示并以退出码 1 退出：

- `uvicorn` 是否在 PATH 上（用 `where` / `which` 解析其真实路径）。
- `frontend/node_modules/vite/bin/vite.js` 是否存在（缺失说明前端依赖未装，
  提示先在 `frontend` 目录运行 `pnpm install`）。

### 启动流程

1. 用 `child_process.spawn` 启动后端：工作目录 `backend/`，**直接调用预检解析到的
   `uvicorn` 可执行文件**（参数 `app.main:app`，**不带 `--reload`**——后端代码变动
   不热重载，需手动重启 `pnpm run dev` 或单独重启后端才生效）。
2. 轮询 `http://localhost:8000/api/health`，间隔 1s，最多等 **60s**
   （覆盖 alembic 迁移 + vendored 仿真器加载的冷启动耗时）。
3. 健康检查通过 → 启动前端：工作目录 `frontend/`，用 `node` 直接运行
   `node_modules/vite/bin/vite.js`（等价于 `frontend` 的 `pnpm dev`，即 `vite`）。
4. 60s 内仍未就绪 → 打印错误，终止后端进程，整体以退出码 1 退出。

**为何不经 `shell: true` / `pnpm`：** 直接 spawn 真实可执行文件后，`child.pid`
就是真实进程（`uvicorn.exe`、运行 vite 的 `node`），`taskkill /T` 才能可靠地清掉
整棵子树。若经 `cmd.exe`/`pnpm` 包一层，`child.pid` 是包装进程，`taskkill /T` 在
`cmd → uvicorn.exe → python` 这类多级链上会漏杀子树、残留孤儿进程。

### 输出

- 两个子进程的 stdout/stderr 透传，分别加彩色前缀 `[backend]` / `[frontend]`。
- 编排脚本自身的状态信息用 `[dev]` 前缀（如"等待后端就绪…"、
  "后端已就绪，启动前端"）。

### 退出与信号处理（Windows 重点）

- 监听 `SIGINT`（Ctrl+C）→ 终止两个子进程后退出。子进程会再派生孙进程
  （`uvicorn.exe → python`、`vite` 的 `node → esbuild`），用
  `taskkill /pid <pid> /T /F` 对真实进程 pid 清理整棵进程树，不残留占用
  `:8000` / `:5173` 的孤儿进程。`taskkill` 用 `spawnSync` 同步执行，确保清理
  在 `process.exit()` 之前完成，无竞态。
- 任一子进程意外退出（非 Ctrl+C 触发）→ 终止另一个子进程，编排脚本以该子
  进程的退出码退出。
- 启动前的预检（见上）已覆盖 `uvicorn` / 前端依赖缺失的场景；进程启动后若再
  抛 `error` 事件，脚本捕获并打印后整体退出。

## 前置假设（脚本不负责的事）

- **不**自动执行 `pip install` / `pnpm install`——假设后端依赖已装在全局
  Python、`frontend/node_modules` 已存在。
- **不**自动执行 `alembic upgrade head`——`app/main.py` 启动时已自动执行。
- **不**执行 `flatten_map.py`——一次性预处理，不属于日常 dev 循环。

## 测试

这是开发工具脚本，靠手动验证，不写自动化测试：

1. 根目录 `pnpm run dev` 能依次拉起后端与前端，前端在后端 `/api/health`
   返回 200 之后才启动。
2. Ctrl+C 能干净退出，事后 `:8000` 与 `:5173` 无残留进程。
3. 后端起不到（如端口被占或依赖缺失）时，60s 后超时退出、退出码非 0，
   且不残留前端进程。

## 不在本次范围内

- 后端热重载（已明确不要 `--reload`）。
- 自动安装依赖、自动建 `.venv`、自动跑地图预处理。
- 生产环境启动编排（见 `docs/deployment.md`）。
