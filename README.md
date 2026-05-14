# Stanford Town Vue

MetaGPT 中 `examples/stanford_town` 生成式智能体仿真（“小镇 the Ville”）的独立重实现，
以自包含的 Vue 3 + FastAPI + SQLite 应用形式重建。它提供了一个用于启动仿真的仪表盘、
实时与回放仿真查看器、逐次 LLM 调用日志，以及一个 persona 状态检视器。

仿真器所需的全部 MetaGPT 框架代码都已 **vendor（内联）**进本仓库（位于 `backend/core/`
和 `backend/simulator/` 下），因此本项目**对 `metagpt` 包零外部依赖**，可独立克隆并运行。

## 架构

后端是**单个 FastAPI 进程**，同时承载 REST API、用于实时更新的 WebSocket hub，以及一个
进程内的 asyncio `SimulationManager`——它为每个运行中的仿真持有一个 worker 任务。
**SQLite 是唯一数据源**，保存全部仿真状态（13 张表）；一个双向的 importer/exporter
负责读写原版 Stanford Town 的磁盘 JSON 格式。

前端是一个 Vue 3 SPA（Pinia stores、Ant Design Vue 组件、Vue Router）。地图使用
**PixiJS v8** 渲染，针对的是由 `scripts/flatten_map.py` 离线生成的预压平地图图片
（而不是在运行时合成约 18 个 Tiled tileset）。

```
                        +-----------------------------------------+
   浏览器 (Vue 3 SPA)   |          FastAPI 单进程                 |
  +------------------+  |  +-----------+  +--------------------+   |
  | Pinia stores     |--+->| REST API  |  | SimulationManager  |   |
  | AntD 组件        |  |  | routers   |  | (每个 sim 一个     |   |
  | PixiJS v8 画布   |<-+--| WebSocket |<-| asyncio worker 任务)|   |
  +------------------+  |  | hub       |  +---------+----------+   |
                        |  +-----+-----+            |              |
                        |        |        +---------v----------+   |
                        |        +------->|   SQLite 数据库    |   |
                        |                 |   (唯一数据源)     |   |
                        |                 +---------+----------+   |
                        |                           |              |
                        |                  JSON 导入 / 导出        |
                        +-----------------------------------------+
```

## 项目结构

```
stanford-town-vue/
  backend/      FastAPI 应用、vendored 的 MetaGPT core + simulator、SQLite 存储、
                Alembic 迁移、runner（SimulationManager + StanfordTown runner），
                以及打包的地图 / 角色 / persona 素材。
  frontend/     Vue 3 + Vite SPA：仪表盘、查看器、LLM 日志、persona 状态。
  scripts/      离线预处理：flatten_map.py（地图图片）与
                copy_assets.py（将原始素材拉取进 backend/assets/）。
  docs/         设计文档：json_format.md（存储格式约定）与 deployment.md。
```

`backend/` 内部：

```
  app/        FastAPI 入口（app/main.py）、HTTP 路由、WebSocket hub。
  core/       VENDORED 的 MetaGPT 框架代码（LLM 提供商、config、context）。
  simulator/  VENDORED 的 Stanford Town 仿真器（roles、memory、plan、actions）。
  runner/     SimulationManager、实时 StanfordTown runner、LLM 配置粘合层。
  storage/    SQLAlchemy 模型、repos、JSON importer/exporter。
  config/     运行时配置（pydantic-settings）。
  alembic/    数据库迁移。
  assets/     打包的迷宫、角色精灵图、persona 引导记忆。
  data/       SQLite 数据库文件（运行时创建；已 gitignore）。
```

## 前置条件

- **Python >= 3.10**
- **Node.js**（LTS）与 **pnpm**（前端使用 `pnpm-lock.yaml`）

## 后端安装与运行

所有后端命令都在 `backend/` 目录下执行。

```bash
cd backend

# 1. 创建并激活虚拟环境
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS / Linux:
# source .venv/bin/activate

# 2. 可编辑安装后端
pip install -e .
# 安装开发工具（pytest、ruff、mypy）：
# pip install -e ".[dev]"

# 3. 应用数据库迁移
alembic upgrade head

# 4. 启动服务
uvicorn app.main:app --reload
```

注意事项：

- **密钥**：首次启动时，应用会在 `~/.stanford-town-vue/secret.key` 生成一个 Fernet
  密钥，并打印一条警告提示备份它。该密钥用于加密 LLM profile 的 API key；一旦丢失，
  已有的加密 profile 将无法读取。参见下方的 LLM 配置一节。
- **迁移**：`app/main.py` 在启动时也会自动执行 `alembic upgrade head`，因此第 3 步
  主要用于提前查看数据库 schema。
- **地图压平**：PixiJS 查看器需要预压平的地图图片。在仓库根目录执行一次即可生成：

  ```bash
  python scripts/flatten_map.py            # 生成 the_ville_ground.png / _foreground.png
  python scripts/flatten_map.py --force    # 覆盖已有输出
  ```

  它读取 `backend/assets/maze/the_ville/visuals/the_ville_jan7.json`，需要 Pillow
  （`pip install Pillow`）。如果打包的素材不存在，请先运行
  `python scripts/copy_assets.py`，从某个 `examples/stanford_town` 源码树中拉取素材。
- **可选：通过 MetaGPT 配置 LLM**：如果你不在 UI 中配置 LLM profile，仿真器会回退到
  环境中的 MetaGPT `core` 配置——即 `~/.metagpt/config2.yaml`（或打包的 `default.yaml`
  / 环境变量）。参见下方的 LLM 配置。

配置由 `config/settings.py` 通过 pydantic-settings 读取。所有字段都可通过 `STT_`
环境变量前缀或 `backend/.env` 文件覆盖：

| 配置项                | 环境变量                  | 默认值                               |
| --------------------- | ------------------------- | ------------------------------------ |
| `database_url`        | `STT_DATABASE_URL`        | `sqlite:///./data/stanford_town.db`  |
| `secret_key_path`     | `STT_SECRET_KEY_PATH`     | `~/.stanford-town-vue/secret.key`    |
| `logs_dir`            | `STT_LOGS_DIR`            | `~/.stanford-town-vue/logs`          |
| `assets_dir`          | `STT_ASSETS_DIR`          | `assets`（相对于 `backend/`）        |
| `forks_dir`           | `STT_FORKS_DIR`           | `~/.stanford-town-vue/forks`         |
| `frontend_dev_origin` | `STT_FRONTEND_DEV_ORIGIN` | `http://localhost:5173`              |

`forks_dir` 是 runner 和 `/api/sims/import/forks` 端点查找原版 Stanford Town 磁盘
fork demo 的位置，分别在其 `storage/` 和 `compressed_storage/` 子目录下。本仓库不打包
任何 fork——请将 fork 目录放到那里（或通过 API 导入仿真），即可从中启动。

## 前端安装与运行

所有前端命令都在 `frontend/` 目录下执行。

```bash
cd frontend

# 安装依赖
pnpm install

# 启动开发服务器（Vite，端口 5173）
pnpm dev

# 类型检查并产出生产构建到 frontend/dist/
pnpm build
```

Vite 开发服务器监听 **http://localhost:5173**，并将 `/api`、`/ws`、`/static`、`/assets`
代理到位于 `http://localhost:8000` 的后端，所以请先在另一个终端启动后端。两者都运行后，
打开 http://localhost:5173。

`pnpm gen:api` 会从后端运行中的 `/openapi.json` 重新生成 `src/types/api.ts`（后端必须
处于运行状态）。

## LLM 配置

仿真器可以通过多个配置来源访问 LLM，按优先级从高到低解析：

1. **UI 单次启动覆盖项** —— 在某次运行的启动表单上选择的值。
2. **UI LLM profile** —— 附加到该仿真的已保存 profile（提供商、模型、base URL、参数、
   API key）。runner 查找该 profile，解密其 key，并为该次运行构建专用 context。
3. **`default.yaml`** —— 打包的 MetaGPT 风格默认配置（如果存在）。
4. **环境变量** —— 从环境中获取提供商 / 模型 / key。
5. **`~/.metagpt/config2.yaml`** —— 用户环境中的 MetaGPT 配置。

未附加 UI profile 时，runner 回退到环境中的 MetaGPT `core` 配置（来源 3-5）。对于已经
拥有可用 `~/.metagpt/config2.yaml` 的用户，这很方便。

LLM profile 通过 `/api/llm-profiles` 端点管理。每个 profile 的 API key 以 **Fernet
加密**形式存储，使用应用密钥；API 永不返回该 key——只有非敏感字段（名称、提供商、模型、
base URL、参数）会序列化回客户端。支持的提供商：`openai`、`deepseek`、`anthropic`。

## 四个页面

- **仪表盘**（`/`）—— 一个启动表单（选择 fork、personas、起始时间、LLM profile），
  外加一张展示所有仿真及其状态的表格。
- **实时查看器**（`/sims/:id/live`）—— PixiJS 地图，随着仿真步进，智能体通过 WebSocket
  实时更新。
- **回放查看器**（`/sims/:id/replay`）—— 同一张地图，由步进滑块驱动，用于在已完成仿真的
  历史记录中来回拖动。
- **LLM 日志 + persona 状态**（`/sims/:id/llm-logs`、`/sims/:id/personas/:name`）——
  某个仿真的每一次 LLM 调用（prompt、响应、token、延迟、错误），以及一个逐 persona 的
  检视器，用于查看 scratch 状态、日程与联想记忆。

## 导入 / 导出

后端可以将一个原版 Stanford Town 仿真目录（磁盘 JSON fork 格式——`reverie/meta.json`、
`environment/`、`movement/`、`personas/`、`llm_logs.jsonl`）**导入**进 SQLite 数据库，
也可以将任意仿真**导出**回该 JSON 格式（`compressed` 或 `live` 布局）。磁盘 JSON 约定
记录在 `docs/json_format.md` 中。导入 / 导出通过 `/api/sims/import` 和
`/api/sims/{id}/export` 端点，以及 UI 中的“导入 / 导出”设置页面暴露。
