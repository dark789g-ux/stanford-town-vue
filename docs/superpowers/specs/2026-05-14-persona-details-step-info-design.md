# persona details 页面显示 step 信息 — 设计文档

日期：2026-05-14

## 背景与问题

persona 详情页（`PersonaStateView`）展示某个 persona 的 scratch、spatial memory
和 memory stream。页面本身没有任何 step / 时间标识，用户无法判断「眼前这份
detail 对应仿真的哪个时间阶段」。

需要注意：后端的 `persona_state` 端点**不按 step 做快照**——scratch / spatial
返回的始终是该 persona 的最新状态。所以页面数据天然对应「仿真最新 step」这一时间点。

## 目标

在 persona 详情页头部显示两类 step 信息：

1. **仿真最新 step**：`sim.step` + 仿真内游戏时间 `sim.curr_time_iso`。这是页面
   scratch / spatial 数据真正对应的时间点。
2. **来源回放 step**：用户从 `SimViewer` 点击某个 AgentCard 的 "Details" 跳转过来时，
   回放滑块停留的那个 step。用于标注「你是从回放的哪一刻进来的」。

## 非目标

- 不让 persona 详情页随仿真实时刷新 step（不引入 WebSocket 连接）。展示的是
  页面加载时的快照，这与 scratch / spatial 本身也是「加载时快照」的语义一致。
- 不对 `curr_time_iso` 做日期格式化——沿用代码库现状（`StepSlider`、
  `SimulationTable` 均原样展示 ISO 字符串）。
- 不按 step 对 scratch / spatial 做历史快照查询。

## 数据流

```
持久化的 simulations 行  ──┐
  (.step, .curr_time_iso)  ├─► persona_state 端点 ──► PersonaStateOut ──► personaState store ──► 页头标签
scratch / spatial / memory ─┘                          (+step, +curr_time_iso)

AgentCard "Details" 点击 ──► router.push(...?from_step=N) ──► route.query.from_step ──► 页头标签（来源提示）
```

两个数字来源不同，原因：

- **最新 step** 走后端响应：`persona_state` 端点本就调用 `_require_sim()` 拿到了
  `sim` 对象（当前丢弃了返回值），把 `sim.step` / `sim.curr_time_iso` 一并返回，
  与 scratch / spatial 同一次查询，数据天然一致，且不增加请求。
- **来源回放 step** 走路由 query：`SimViewerView` 卸载时会 `session.disconnect()`，
  `simSession` store 会被重置，跳转后无法再读到原回放 step；因此必须在跳转那一刻
  由 `AgentCard` 通过 URL query 把它带过去。

## 详细设计

### 后端 — `backend/app/routes/sims.py`

- `PersonaStateOut` 模型新增字段：
  - `step: int`
  - `curr_time_iso: str | None`
- `persona_state()` 把 `_require_sim()` 的返回值接住（当前被丢弃），在构造
  `PersonaStateOut` 时填入 `step=sim.step`、`curr_time_iso=sim.curr_time_iso`。

无新增数据库查询。

### 前端

**`frontend/src/stores/personaState.ts`**
- `PersonaStateOut` interface 与 `PersonaStateState` 各新增 `step: number` 与
  `currTimeIso: string | null`。
- `load()` 从响应取值存入 state。
- `reset()` 中一并清空（`step` 归 0，`currTimeIso` 归 null）。

**`frontend/src/api/sims.ts`**
- `PersonaState` 当前为 `Record<string, any>`，无需手改。
- 后端 API 结构变更后运行 `pnpm gen:api` 重新生成 `frontend/src/types/api.ts`
  （遵循 CLAUDE.md 约定）。

**`frontend/src/components/sim/AgentCard.vue`**
- `openDetails()` 引入 `useSimSessionStore`，读取 `currentStep`，拼接到跳转 URL：
  `/sims/${simId}/personas/${name}?from_step=${currentStep}`。

**`frontend/src/views/PersonaStateView.vue`**
- 用 `useRoute()` 读取 `route.query.from_step`（可能不存在）。
- 在页头「标题」与「← Back to simulation」之间渲染标签：
  - **最新 step 标签**：`<a-tag>` 显示 `Step {step} · {currTimeIso}`；
    `currTimeIso` 为 null 时退化为只显示 `Step {step}`。
  - **来源回放 step 标签**：仅当 `from_step` query 存在时渲染，灰字 `<a-tag>`
    显示 `从回放 Step {from_step} 跳转而来`。

## 边界情况

- **直接访问 / 刷新 persona 页面**：URL 无 `from_step` query → 来源标签不渲染，
  最新 step 标签照常显示。
- **`curr_time_iso` 为 null**：最新 step 标签退化为只显示 `Step {step}`。
- **仿真正在运行**：`step` 是页面加载时的快照，不随仿真推进刷新——与页面 scratch /
  spatial 数据「加载时快照」的语义一致，可接受。

## 测试

- **后端**：扩展 `backend/tests/test_routes_sims.py::test_persona_state`，断言
  `persona_state` 响应包含 `step` 与 `curr_time_iso`，且值与所属 simulation 行一致。
- **前端**：`pnpm type-check` 通过；`pnpm gen:api` 重新生成类型后无类型冲突。

## 受影响文件清单

| 文件 | 改动 |
| --- | --- |
| `backend/app/routes/sims.py` | `PersonaStateOut` 加字段；`persona_state()` 填值 |
| `backend/tests/test_routes_sims.py` | 扩展 `test_persona_state` 断言 |
| `frontend/src/stores/personaState.ts` | 类型与 state 加 `step` / `currTimeIso`；`load()` / `reset()` 同步 |
| `frontend/src/components/sim/AgentCard.vue` | `openDetails()` 带上 `from_step` query |
| `frontend/src/views/PersonaStateView.vue` | 页头渲染两个 step 标签 |
| `frontend/src/types/api.ts` | `pnpm gen:api` 重新生成 |
