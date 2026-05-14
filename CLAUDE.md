# CLAUDE.md

本文件为 Claude Code（claude.ai/code）在本仓库中工作时提供指引。

## 项目概览

生成式智能体仿真（“小镇 the Ville”），以**自包含的 Vue 3 + FastAPI + SQLite** 应用形式构建。
仿真器所需的全部框架代码都已 **vendor（内联）**进本仓库，可独立克隆运行。

## 常用命令

### 后端（在 `backend/` 目录下执行）

```bash
pip install -e ".[dev]"        # 可编辑安装 + pytest/ruff/mypy
alembic upgrade head           # 应用数据库迁移（应用启动时也会自动执行）
uvicorn app.main:app --reload  # 在 :8000 启动服务

pytest                         # 运行 tests/ ；默认 addopts 会排除 slow 测试
pytest tests/test_st_runner.py -q          # 运行单个测试文件
pytest -k event_bus -q                     # 按表达式筛选
pytest -m slow                             # 运行 slow 集成测试（完整的 mock-LLM tick）

ruff check . --fix             # 代码检查（line-length 120，目标 py310）
mypy .                         # 类型检查
```

### 前端（在 `frontend/` 目录下执行）

```bash
pnpm install
pnpm dev          # Vite 开发服务器，端口 :5173，将 /api /ws /static /assets 代理到 :8000
pnpm build        # vue-tsc --noEmit && vite build
pnpm type-check   # 仅执行 vue-tsc
pnpm gen:api      # 从运行中的后端 /openapi.json 重新生成 src/types/api.ts
```

先启动后端；前端开发服务器会代理到 `http://localhost:8000`。

### 离线预处理（在仓库根目录执行）

```bash
python scripts/flatten_map.py        # 将 Tiled 地图预先压平为单张 PNG，供 PixiJS 使用
python scripts/copy_assets.py        # 从上游目录树将原始素材拉取到 backend/assets/
```

## 架构

后端是**单个 FastAPI 进程**，同时承载 REST API、WebSocket hub，以及一个进程内的 asyncio
`SimulationManager`。**SQLite 是唯一数据源**（13 张表）；一个双向的 importer/exporter
负责读写原版 Stanford Town 的磁盘 JSON 格式。

### Runner 注入接缝（最需要理解的部分）

`runner/manager.py::SimulationManager` 刻意与真实仿真器解耦：

- manager 为每个 `sim_id` 维护一个 **asyncio 任务**，通过两个 `asyncio.Event` 标志实现协作式的
  `pause`/`resume`/`stop`。它负责对账数据库的 `status` 列，并且在注入 no-op runner 后完全可单测。
- 真正的步进逻辑是一个注入的 `Runner` 可调用对象（`Callable[[RunContext], Awaitable[None]]`）。
  默认是 `_default_noop_runner`；应用启动时，`runner/bootstrap.py` 通过 FastAPI 的 lifespan
  钩子将其替换为真实的 `runner/st_runner.py::stanford_town_runner`。
- `RunContext` 是每次运行的句柄：它携带 `session_factory`、`EventBus`、pause/stop 事件，以及
  `emit_step` / `emit_status` / `emit_llm_call` 等辅助方法。

`st_runner.py` 以**每次一个 tick** 的方式驱动 vendored 仿真器（`await town.env.run()`），
而不是 `town.run(n_round)`——因为后者会在内部循环、没有逐步的钩子。每个 tick 它会：读取仿真器
刚写出的 `environment/{step}.json` + `movement/{step}.json` 文件，调用
`runner/step_sync.py::sync_step_to_db` 将其 upsert 入库，推进 `simulations` 行，并发出一个
`step` 事件。它只**读取**仿真器的 JSON I/O，绝不修改它。运行结束时，`_persist_final_state`
使用 importer 的 `_import_personas` 辅助函数重新导入 persona 子树（而不是 `import_simulation`，
后者会重建 `simulations` 行，从而破坏 manager 持有的实时句柄）。

### 事件流：EventBus -> WebSocket

`runner/events.py::EventBus` 是一个按 `sim_id` 分键的内存 pub/sub（队列有界，溢出时丢弃最旧的）。
`app/ws/hub.py` 提供 `/ws/sim/{sim_id}`：客户端订阅后先发送 `snapshot`，再从 `since_step`
回放已持久化的 `step_movements` 历史，然后转发实时的 `EventBus` 事件。hub 维护自己的
`get_event_bus()` 单例——注意它与 `manager_singleton.event_bus` 是**不同的实例**；在假定二者
共享之前，请先确认接线方式。

### 目录结构

```
backend/
  app/        FastAPI 入口（app/main.py）、HTTP 路由（app/routes/）、WebSocket hub（app/ws/）
  core/       VENDORED 的框架代码——LLM 提供商、config、context、schema。视作上游代码。
  simulator/  VENDORED 的仿真器代码——roles、memory、plan、actions、prompts。视作上游代码。
  runner/     SimulationManager、st_runner、step_sync、EventBus、llm_config、bootstrap
  storage/    SQLAlchemy 模型（storage/models.py）、repos/、JSON importer/exporter、json_schemas
  config/     基于 pydantic-settings 的运行时配置（STT_ 环境变量前缀）、default.yaml
  alembic/    数据库迁移
  assets/     地图、角色精灵图、persona 引导记忆
  data/       SQLite 数据库文件——运行时创建，已 gitignore
frontend/src/
  api/        axios 客户端 + 各资源模块 + ws.ts；types/api.ts 为自动生成
  stores/     Pinia stores（simulations、simSession、llmLogs、llmProfiles、personaState、appConfig）
  pixi/       PixiJS v8 渲染器（TownRenderer、AgentSprite、Camera）——由 components/pixi/MapCanvas.vue 使用
  views/      每个路由对应一个组件（见 router.ts）
scripts/      flatten_map.py、copy_assets.py
docs/         json_format.md（磁盘 JSON 格式约定）、deployment.md
```

`core/` 和 `simulator/` 是 vendored 的上游代码。优先改动 `runner/`、`storage/` 或 `app/`，
而不是编辑它们；如确需编辑，应保持改动最小且贴近上游风格。

### LLM 配置解析

runner 在每次运行时解析一个 LLM 上下文，优先级从高到低为：(1) UI 上的单次启动覆盖项，
(2) 附加到该仿真的已保存 UI **LLM profile**——由 `runner/llm_config.py::load_profile_context`
查找并解密，(3) 内置的 `config/default.yaml`，(4) 环境变量，(5) 环境中的 `~/.metagpt/config2.yaml`。
未附加 profile 时，仿真器回退到环境中的 `core` 配置。

LLM profile 的 API key 以 **Fernet 加密**形式存储，使用应用密钥（首次启动时在
`~/.stanford-town-vue/secret.key` 生成——丢失它会导致已有 profile 无法读取）。API 永不返回该 key。
支持的提供商：`openai`、`deepseek`、`anthropic`。

## 约定

- Python **>= 3.10**；ruff 配置 `select = ["E","F","W","I","UP","B"]`，line-length 120。
- 异步优先：仿真器和 runner 都是 `async`；切勿在异步路径中引入同步阻塞调用（用 `httpx`，不用 `requests`）。
- 日志使用 `loguru`（`from loguru import logger`），不用 `print`。
- 配置通过 `config/settings.py`（`get_settings()`）读取；所有字段可通过 `STT_` 环境变量前缀或
  `backend/.env` 覆盖。不要直接读取环境变量。
- pytest 使用 `asyncio_mode = "auto"`（无需 `@pytest.mark.asyncio`）；长时间运行的集成测试请标记
  `@pytest.mark.slow`。
- 改动后端 API 结构后，用 `pnpm gen:api` 重新生成前端类型。
</content>
