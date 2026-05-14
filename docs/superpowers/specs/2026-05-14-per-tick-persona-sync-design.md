# 每 tick 增量同步 persona 活动状态 — 设计文档

日期：2026-05-14

## 背景与问题

persona 详情页（`PersonaStateView`）的 Memory Stream / Scratch / Spatial Memory
对一个运行中或被中断的仿真常常是空的。根因：

仿真器在运行期间**只把 `environment/{step}.json` 和 `movement/{step}.json` 落盘**，
associative memory、scratch、spatial memory 都不写盘——它们只活在每个 `STRole`
的进程内对象（`role.a_mem.storage` / `role.scratch` / `role.s_mem.tree`）里。

runner 的每 tick 同步 `sync_step_to_db` 只消费上述两类文件，**不碰 persona 状态**。
persona 状态只在两个时机入库：

1. 运行开始：`_sync_personas_to_db` 从 fork 导入 *bootstrap* 状态；
2. 运行结束：`_persist_final_state` → `role.save_into()` 刷盘后全量重导。

于是：一个 `interrupted`（进程在 `RUNNING` 时挂掉、启动时被 `scan_interrupted`
翻成 `INTERRUPTED`）的仿真从没跑到第 2 步，steps 0–N 期间 agent 在内存里积累的
记忆随进程一起丢失；详情页只能看到（可能为空的）bootstrap 状态。

## 目标

让 runner 每 tick（每跑完一次 `town.env.run()`）就把每个 persona 的活动状态
**增量同步**入库，使运行中 / 被中断的仿真在详情页也能看到 step 0 至当前的
memory stream、scratch、spatial memory。

> 术语：本代码库里 tick 与 step 是同一个东西，1 step = 1 tick = runner 循环跑一圈
> = 一次 `env.run()`。不存在「一个 step 内有多个 tick」。

## 非目标

- 不解决「空 bootstrap fork」问题（从一个自身无记忆的 DB sim fork 出来）。每 tick
  增量同步已让运行中的 sim 跑一两步就有记忆，此问题对运行中的 sim 实质消解。
- 不改前端。`/sims/{id}/personas/{name}/memory` 路由与 `PersonaStateView` 本就正确，
  之前只是查了张空表。
- 不做逐 step 记忆快照。沿用现有模型：同步「当前累积状态」，`memory_nodes.created`
  记录节点产生于哪个 step，API 的 `before_step` 过滤已存在。
- 不引入同步频率配置项，硬编码每 tick。一个 tick 本身要跑多次 LLM 调用（秒~分钟级），
  一次同步是毫秒级，频率不构成性能考量。

## 方案

在 `runner/step_sync.py` 新增一个与 `sync_step_to_db` 并列的函数，读取运行中的
`STRole` 对象、增量 upsert persona 状态。`sync_step_to_db` 保持「纯文件读取器」的
职责不变；新函数是它的「活对象」对应物。

被否决的两个备选：

- **扩展 `sync_step_to_db` 加 `roles` 参数**：把「读文件的世界状态同步」与「读活对象
  的 persona 状态同步」两种性质不同的职责揉进一个函数，破坏其文件纯粹性，现有针对
  它的测试都得 mock role 对象。
- **每 tick `role.save_into()` + 复用 `_sync_personas_to_db` 全量重导**：不是增量
  （每 tick 全量重导所有节点），且每 tick 删/建 persona 行会让 persona ID 不断变化，
  与「增量同步」诉求直接冲突。

## 数据流

```
运行中的 STRole 对象 ─┐
  .a_mem.storage      │
  .scratch            ├─► sync_personas_step ─► repos.memory / repos.personas ─► DB
  .s_mem.tree         │      (每 tick，紧跟 sync_step_to_db 之后)
roles 列表 ───────────┘

st_runner.py step 循环：
  await town.env.run()
  movement_rows = sync_step_to_db(...)        # 不变：文件 → step_environments / step_movements
  sync_personas_step(...)                     # 新增：活对象 → personas / memory_nodes / 关键词表
  advance_step(...) ; emit_step(...)
```

## 新增函数

```
sync_personas_step(session_factory, sim_id, roles, start_dt, sec_per_step) -> None
```

位置：`runner/step_sync.py`。开一个短生命周期 session（同 `sync_step_to_db` 风格）。
对每个 `role` in `roles`，用独立 try/except 包住（一个坏 role 不连累其他，呼应
`env.run` 的 per-role 隔离）：

1. **定位 persona 行**：`repos.personas.get(sim_id, role.name)`。找不到（开局
   `_sync_personas_to_db` 失败的罕见情况）→ 跳过该 role + debug 日志，留给结束时的
   `_persist_final_state` 兜底。
2. **增量同步 memory_nodes + 关键词**（见下「增量记忆机制」）。
3. **覆盖写 scratch**：`repos.personas.save_scratch(persona.id, role.scratch.model_dump())`
   ——单行，幂等。
4. **覆盖写 spatial memory**：`repos.personas.save_spatial_memory(persona.id, role.s_mem.tree)`
   ——单行，幂等。

### 增量记忆机制（watermark）

每 tick、对每个 persona：

1. `watermark = repos.memory.get_max_node_count(persona.id)` —— 库里该 persona 已有
   的最大 `node_count`（无则 0）。该方法已存在，无需新增。
2. 从 `role.a_mem.storage`（`BasicMemory` 列表）挑出 `memory_count > watermark` 的新节点。
3. 转成行 dict，`repos.memory.add_nodes_bulk(persona.id, rows)`。
4. 为这些新节点收集 `(keyword, node_id)`，按 node_type 分桶，
   `repos.memory.add_keywords_bulk(...)`。

**正确性兜底**：`add_nodes_bulk` / `add_keywords_bulk` 本身是 `on_conflict_do_nothing`
（冲突键 `(persona_id, node_id)`）。watermark 只是性能优化——避免每 tick 重复转换
老节点；即使 `memory_count` 排序偶发异常，最坏只是多转换几个老节点，**永不插入重复行**。

### `BasicMemory → 行` 转换

转换辅助函数把一个 `BasicMemory` 映射成 `add_nodes_bulk` 期望的行 dict（`MemoryNode`
模型的列）。**复用 importer 的 `_node_to_row` 作为单一真源**：

- `BasicMemory.save_to_dict()` 已输出规范的 GA `nodes.json` 节点 dict（键 `node_count`
  / `type` / `type_count` / `depth` / `created`(已序列化为 `"%Y-%m-%d %H:%M:%S"` 字符串)
  / `expiration` / `subject` / `predicate` / `object` / `description` / `embedding_key`
  / `poignancy` / `keywords` / `filling` / `cause_by`）。
- `storage.json_schemas.AssociativeMemoryNode` 的必填字段是 `node_count`/`type_count`
  /`type`/`created`/`subject`/`predicate`/`object`/`description`/`embedding_key`，且
  `model_config = ConfigDict(extra="allow")`。`save_to_dict()` 的输出键是其超集，
  `created` 已是它要求的字符串格式，多出的 `cause_by` 被 `extra="allow"` 接纳。
  因此可直接 `AssociativeMemoryNode(**node_dict)`。
- 再喂给现成的 `importer._node_to_row(node_id, amn, start_dt, sec_per_step)`，产出
  与开局导入、结束重导完全一致的行（含 `created` 由 `_game_time_to_step` 从游戏时间
  换算成 step int、`filling_json` 归一化等）。

实现时先用一个小测试锁定 `save_to_dict()` → `AssociativeMemoryNode` 的可构造性；
若发现键名出入就在转换函数里补一层适配，仍走 `_node_to_row`。

## 与现有同步点的关系

- **开局 `_sync_personas_to_db`**（循环前）：建 persona 行 + 导 bootstrap 记忆。
  `sync_personas_step` 依附这些行。不动。
- **结束 `_persist_final_state` → `_sync_personas_to_db`**（循环后）：仍做删重建 +
  全量重导，给出最终权威状态。`sync_personas_step` 在运行期间纯增量补充，结束时的
  全量重导给一个干净终态，二者不冲突。不动。
- 改动面：`st_runner.py` 加 1 个调用点；`step_sync.py` 加 1 个新函数 + 1 个转换辅助
  函数。repos / models / importer / 开局与结束的同步逻辑都不碰。

## 错误处理

- 调用点整体 best-effort：`sync_personas_step` 抛任何异常 → `logger.warning` 后继续
  仿真，绝不中断（与开局那次 `_sync_personas_to_db` 的 best-effort 一致）。

  ```python
  movement_rows = sync_step_to_db(ctx.session_factory, ctx.sim_id, step, work_dir)
  try:
      sync_personas_step(ctx.session_factory, ctx.sim_id, roles, start_dt, sec_per_step)
  except Exception as exc:  # noqa: BLE001 — best-effort，绝不中断仿真
      logger.warning("sync_personas_step failed at step {}: {}", step, exc)
  ```

- 函数内 per-role try/except：单个 role 失败只记日志，不连累其他 role。
- persona 行缺失 → 跳过该 role + debug 日志。

## 测试

`backend/tests/test_persona_sync.py`，pytest（`asyncio_mode=auto`），配 in-memory
SQLite + repos，用轻量 fake role（只暴露 `.name` / `.a_mem.storage` / `.scratch`
/ `.s_mem.tree`）：

- 首次调用：插入 N 个节点 + scratch + spatial。
- 追加 2 个节点后再调：只新增 2 行（验证 watermark 增量），scratch / spatial 被覆盖。
- 幂等：同状态连调两次 → 无重复节点行。
- persona 行缺失 → 不抛异常，干净返回。
- 关键词：新节点的 keyword 进对应的 `memory_keywords_to_event/chat/thought` 表。
- 一个小测试锁定 `save_to_dict()` → `AssociativeMemoryNode(**dict)` 的可构造性。
- 视 `test_st_runner.py` 现有 slow 集成测试情况，可补一条断言：跑到中途的 sim，
  persona 已有 `memory_nodes` 行。
