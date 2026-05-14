# Original Stanford Town JSON Storage Format

This document describes the on-disk JSON layout used by the original Generative
Agents simulator (the "Stanford Town" / "the Ville" demo from the Joon Sung Park
et al. paper) as it is produced by the vendored simulator in
`backend/simulator/`. Each simulation is a directory under `storage/{sim_code}/`
and is read by the frontend / replay tooling unchanged.

`stanford-town-vue` replaces that on-disk format with SQLite tables. The
**importer** (M2 wave 3) reads these JSON files into the DB and the **exporter**
(M2 wave 3) writes them back out for the legacy replay viewer. This doc is the
contract for both.

The document was produced by inspecting:

- A full multi-step demo:
  `examples/stanford_town/compressed_storage/July1_the_ville_isabella_maria_klaus-step-3-20/`
  (note: this is a "compressed" archive — `master_movement.json` instead of
  `movement/{step}.json` per-step files; everything else has the normal shape).
- The bootstrap-only seed sim:
  `examples/stanford_town/storage/base_the_ville_n25/`.
- A live-run sim:
  `examples/stanford_town/storage/test_sim/` (per-step `movement/{step}.json`).
- LLM logs from `examples/stanford_town/storage/test001/llm_logs.jsonl`.
- Loader code in `backend/simulator/memory/` and `backend/simulator/utils/`.

---

## Top-level layout

```
storage/{sim_code}/
├── reverie/
│   └── meta.json                    # one per sim
├── environment/
│   ├── 0.json                       # one per step
│   ├── 1.json
│   └── ...
├── movement/
│   ├── 0.json                       # one per step
│   ├── 1.json
│   └── ...
├── personas/
│   └── <Persona Name>/              # one dir per persona (space-separated full name)
│       └── bootstrap_memory/
│           ├── scratch.json
│           ├── spatial_memory.json
│           └── associative_memory/
│               ├── nodes.json
│               ├── kw_strength.json
│               └── embeddings.json
└── llm_logs.jsonl                   # one append-only stream per sim (this fork only)
```

Archived / "compressed" sims (e.g. `compressed_storage/...`) collapse all
per-step movement files into a single `master_movement.json` at the sim root,
and drop the `environment/` directory. The compressed format is read-only —
the live runner never writes it, only the replay frontend consumes it.

---

## `reverie/meta.json`

One JSON object per simulation. Cardinality: 1.

| Field           | Type        | Required | Description                                                                                          |
| --------------- | ----------- | -------- | ---------------------------------------------------------------------------------------------------- |
| `fork_sim_code` | str \| null | yes      | The `sim_code` this sim was forked from (the seed). `null` for original seed sims.                   |
| `start_date`    | str         | yes      | Sim start date, format `"%B %d, %Y"` (e.g. `"February 13, 2023"`). No time-of-day component.         |
| `curr_time`     | str         | yes      | Latest tick, format `"%B %d, %Y, %H:%M:%S"` (e.g. `"February 14, 2023, 00:02:30"`).                  |
| `sec_per_step`  | int         | yes      | Seconds of game time advanced per simulator step (typically `10`).                                   |
| `maze_name`     | str         | yes      | Maze identifier — always `"the_ville"` in shipped demos.                                             |
| `persona_names` | list[str]   | yes      | Full names of all personas in the sim. Order is not load-bearing but is preserved by `read_json`.    |
| `step`          | int         | yes      | Current step counter (0-based). Increments by 1 each simulator tick.                                 |

**Sample (demo `July1_..._step-3-20/meta.json`):**

```json
{
  "fork_sim_code": "July1_the_ville_isabella_maria_klaus-step-3-19",
  "start_date": "February 13, 2023",
  "curr_time": "February 14, 2023, 00:02:30",
  "sec_per_step": 10,
  "maze_name": "the_ville",
  "persona_names": ["Isabella Rodriguez", "Maria Lopez", "Klaus Mueller"],
  "step": 8655
}
```

> **Note** the demo file is actually at `<sim_root>/meta.json`, but the
> canonical path used by the live simulator (`mg_ga_transform.get_reverie_meta`)
> is `<sim_root>/reverie/meta.json`. The importer must accept either.

---

## `environment/{step}.json`

One JSON object per step. Keys are persona full names; values describe the
tile each persona currently occupies at the start of that step.

| Field        | Type | Required | Description                                                  |
| ------------ | ---- | -------- | ------------------------------------------------------------ |
| `maze`       | str  | yes      | Maze name — matches `reverie/meta.json#maze_name`.           |
| `x`          | int  | yes      | Tile-grid X coordinate.                                      |
| `y`          | int  | yes      | Tile-grid Y coordinate.                                      |

Top-level type: `dict[str, PersonaPosition]` keyed by persona name.

**Sample (excerpt from `base_the_ville_n25/environment/0.json`):**

```json
{
  "Latoya Williams": { "maze": "the_ville", "x": 16, "y": 18 },
  "Rajiv Patel":     { "maze": "the_ville", "x": 26, "y": 18 },
  "Isabella Rodriguez": { "maze": "the_ville", "x": 72, "y": 14 }
}
```

---

## `movement/{step}.json`

One JSON object per step, written by `mg_ga_transform.save_movement`. The file
has a fixed two-key envelope: `persona` (per-agent action snapshot) and `meta`
(step-level metadata).

```json
{
  "persona": { "<persona name>": { ... PersonaMovement ... }, ... },
  "meta":    { "curr_time": "February 13, 2023, 00:05:00" }
}
```

### `persona[name]` (PersonaMovement)

| Field          | Type                          | Required | Description                                                                                                                                                          |
| -------------- | ----------------------------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `movement`     | `[int, int]` (length-2)       | yes      | New `(x, y)` tile after this step's pathfinding.                                                                                                                     |
| `pronunciatio` | str                           | yes      | One-or-two-emoji "speech bubble" describing the action (e.g. `"😴"`, `"💤"`).                                                                                        |
| `description`  | str                           | yes      | Free-text action description, often containing the location path delimited by `" @ "`, e.g. `"sleeping @ the Ville:Isabella Rodriguez's apartment:main room:bed"`.   |
| `chat`         | `list[list[str]]` \| null     | yes      | If the persona is in a conversation this step, a list of `[speaker_name, utterance]` pairs spanning the whole conversation so far. `null` otherwise.                 |

### `meta`

| Field       | Type | Required | Description                                                                |
| ----------- | ---- | -------- | -------------------------------------------------------------------------- |
| `curr_time` | str  | yes      | Game time at the end of this step, format `"%B %d, %Y, %H:%M:%S"`.         |

### `master_movement.json` (compressed archives only)

In archived sims, all step movement files are concatenated into a single
file at the sim root with shape:

```json
{
  "0":  { "<persona name>": { movement, pronunciatio, description, chat }, ... },
  "1":  {},
  "2":  {},
  ...
}
```

Note the inner per-persona shape is identical to `persona[name]` above, but the
outer `meta.curr_time` envelope is dropped — empty `{}` values mean "no
movement deltas this step".

---

## `personas/<name>/bootstrap_memory/scratch.json`

One file per persona. Contains the full `Scratch` blob: hyperparameters, core
identity, current action, plan, and chat state. Field set comes from
`backend/simulator/memory/scratch.py` (`class Scratch`).

The file is large and has many optional / nullable fields. Importer and
exporter should `extra="allow"` so forks that add fields don't break parsing.

### Hyperparameters (always int)

| Field                          | Type    | Default | Description                                       |
| ------------------------------ | ------- | ------- | ------------------------------------------------- |
| `vision_r`                     | int     | 4 / 8   | Tile radius the persona can perceive.             |
| `att_bandwidth`                | int     | 3 / 8   | Max simultaneous events attended.                 |
| `retention`                    | int     | 5 / 8   | Working-memory size for recent events.            |
| `concept_forget`               | int     | 100     | Concept forgetting threshold.                     |
| `daily_reflection_time`        | int     | 180     | Minutes of game-time between reflections.         |
| `daily_reflection_size`        | int     | 5       | Reflections produced per cycle.                   |
| `overlap_reflect_th`           | int     | 2 or 4  | Overlap threshold for reflection trigger.         |
| `kw_strg_event_reflect_th`     | int     | 4 or 10 | Event-keyword strength reflection threshold.      |
| `kw_strg_thought_reflect_th`   | int     | 4 or 9  | Thought-keyword strength reflection threshold.    |

### Reflection / retrieval weighting

| Field                       | Type  | Default | Description                                              |
| --------------------------- | ----- | ------- | -------------------------------------------------------- |
| `recency_w`                 | int   | 1       | Recency weight for memory retrieval.                     |
| `relevance_w`               | int   | 1       | Relevance weight.                                        |
| `importance_w`              | int   | 1       | Importance/poignancy weight.                             |
| `recency_decay`             | float | 0.99 / 0.995 | Per-step decay applied to recency score.            |
| `importance_trigger_max`    | int   | 150 / 250 | Reflection trigger max budget.                         |
| `importance_trigger_curr`   | int   | (varies) | Remaining budget; decremented per new memory.           |
| `importance_ele_n`          | int   | 0       | Count of importance-bearing events since last reflect.   |
| `thought_count`             | int   | 5       | Number of recent thoughts tracked.                       |

### World snapshot

| Field            | Type                          | Description                                                                       |
| ---------------- | ----------------------------- | --------------------------------------------------------------------------------- |
| `curr_time`      | str \| null                   | `"%B %d, %Y, %H:%M:%S"`; `null` in pristine bootstrap.                            |
| `curr_tile`      | `[int, int]` \| null          | Persona's current tile.                                                           |
| `daily_plan_req` | str \| null                   | Free-text daily plan requirement seeded by the author.                            |

### Core identity

| Field         | Type       | Description                                                          |
| ------------- | ---------- | -------------------------------------------------------------------- |
| `name`        | str        | Full name (matches the persona directory name).                      |
| `first_name`  | str        |                                                                      |
| `last_name`   | str        |                                                                      |
| `age`         | int        |                                                                      |
| `innate`      | str        | L0 permanent traits, comma-separated.                                |
| `learned`     | str        | L1 stable backstory.                                                 |
| `currently`   | str        | L2 free-text "what is the persona doing now / lately".               |
| `lifestyle`   | str        | Sleep/wake/meal pattern.                                             |
| `living_area` | str        | Colon-delimited address: `"the Ville:<sector>:<arena>"`.             |

### Daily plan

| Field                          | Type                              | Description                                                                                                                |
| ------------------------------ | --------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `daily_req`                    | list[str]                         | Bullet list of daily requirements derived by the LLM, e.g. `["wake up at 6am", ...]`. Empty `[]` for pristine bootstrap.   |
| `f_daily_schedule`             | `list[[str, int]]`                | Decomposed action sequence: `[task_text, duration_minutes]`. **Note**: `Scratch` declares `list[Union[int, str]]` but the on-disk order is always `[str, int]`. |
| `f_daily_schedule_hourly_org`  | `list[[str, int]]`                | Same shape — the original hourly schedule, kept around for reference / regeneration.                                       |

### Current action

| Field                    | Type                            | Description                                                                          |
| ------------------------ | ------------------------------- | ------------------------------------------------------------------------------------ |
| `act_address`            | str \| null                     | Full colon-delimited address of the action site.                                     |
| `act_start_time`         | str \| null                     | `"%B %d, %Y, %H:%M:%S"`.                                                             |
| `act_duration`           | int \| null                     | Duration in minutes.                                                                 |
| `act_description`        | str \| null                     | Verb-phrase description (e.g. `"sleeping"`).                                         |
| `act_pronunciatio`       | str \| null                     | Emoji bubble for this action.                                                        |
| `act_event`              | `[str, str \| null, str \| null]` | `(subject, predicate, object)` triple. Subject is always the persona name; pred/obj may be `null` when idle. |
| `act_obj_description`    | str \| null                     | Description of the object being interacted with.                                     |
| `act_obj_pronunciatio`   | str \| null                     | Emoji for the object's state.                                                        |
| `act_obj_event`          | `[str \| null, str \| null, str \| null]` | `(object_name, predicate, object_state)` triple for the touched game object. |
| `chatting_with`          | str \| null                     | Other persona currently being chatted with.                                          |
| `chat`                   | str \| null                     | **Note**: the `Scratch` model declares this as a `str`, but in practice the simulator stores `list[[str, str]]` here at runtime. The on-disk bootstrap value is always `null`. |
| `chatting_with_buffer`   | `dict[str, int]`                | `persona_name -> last-chatted-step-offset` (negative for past).                      |
| `chatting_end_time`      | str \| null                     | `"%B %d, %Y, %H:%M:%S"`; when the active chat is scheduled to end.                   |
| `act_path_set`           | bool                            | Whether the pathfinder has produced a tile sequence for the current action.          |
| `planned_path`           | `list[[int, int]]`              | Remaining tile sequence the persona will walk. Empty `[]` when arrived or idle.      |

**Sample (pristine bootstrap from `base_the_ville_n25/Isabella Rodriguez/.../scratch.json`):** see the file directly — most action fields are `null`, `daily_req`/`f_daily_schedule` are `[]`, `act_event` is `["Isabella Rodriguez", null, null]`, `act_obj_event` is `[null, null, null]`, `chatting_with_buffer` is `{}`.

---

## `personas/<name>/bootstrap_memory/spatial_memory.json`

A nested dict tree:

```
world (str) → sector (str) → arena (str) → list[game_object (str)]
```

i.e. `dict[str, dict[str, dict[str, list[str]]]]`.

Worlds, sectors, arenas, and game objects are arbitrary string identifiers
(spaces allowed). Loaded into `MemoryTree.tree` verbatim — see
`backend/simulator/memory/spatial_memory.py`.

**Sample (excerpt from Isabella Rodriguez):**

```json
{
  "the Ville": {
    "Hobbs Cafe": {
      "cafe": ["refrigerator", "cafe customer seating", "cooking area", "kitchen sink", "behind the cafe counter", "piano"]
    },
    "Isabella Rodriguez's apartment": {
      "main room": ["bed", "desk", "refrigerator", "closet", "shelf"]
    }
  }
}
```

---

## `personas/<name>/bootstrap_memory/associative_memory/nodes.json`

One file per persona. A flat dict keyed by `node_id` (the string `"node_N"`,
1-based, in **insertion order**). The simulator writes nodes in **reverse**
order (newest first), so the first key in the file is the highest-numbered.

### Node fields

| Field           | Type                  | Required | Description                                                                                                |
| --------------- | --------------------- | -------- | ---------------------------------------------------------------------------------------------------------- |
| `node_count`    | int                   | yes      | 1-based serial across all node types for this persona. Matches the trailing digit of the `node_id` key.    |
| `type_count`    | int                   | yes      | 1-based serial within the node's `type`.                                                                   |
| `type`          | `"event" \| "thought" \| "chat"` | yes | Discriminator. Always lowercase in observed data.                                                  |
| `depth`         | int                   | yes      | `0` for raw events, `1` for first-order thoughts/chats, `>=2` for thoughts derived from other thoughts.    |
| `created`       | str                   | yes      | Game-time creation timestamp, format `"%Y-%m-%d %H:%M:%S"`. **Different format** from `scratch.curr_time`. |
| `expiration`    | str \| null           | yes      | Same format as `created`; `null` for events (most common).                                                 |
| `subject`       | str                   | yes      | Triple subject (usually a persona name or a colon-delimited address).                                      |
| `predicate`     | str                   | yes      | Triple predicate (lowercase verb, sometimes `"is"`/`"be"`).                                                |
| `object`        | str                   | yes      | Triple object.                                                                                             |
| `description`   | str                   | yes      | Human-readable phrase. For events, often just `"{subject} {predicate} {object}"`.                          |
| `embedding_key` | str                   | yes      | Key into `embeddings.json`. Typically duplicates `description`.                                            |
| `poignancy`     | int                   | yes      | Importance score `1..10`. `1` for trivial events, higher for plans/reflections.                            |
| `keywords`      | list[str]             | yes      | Keyword strings used for retrieval. Case-preserved here, but lowercased into `*_keywords` dicts in memory. |
| `filling`       | list \| null          | yes      | For thoughts/chats: list of constituent `node_id`s used to derive this node. For events: `[]`. Can be `null`. |

**Sample (one event + one thought from `Klaus Mueller/.../nodes.json`):**

```json
{
  "node_593": {
    "node_count": 593, "type_count": 478, "type": "event", "depth": 0,
    "created": "2023-02-14 00:02:10", "expiration": null,
    "subject": "the Ville:Dorm for Oak Hill College:Klaus Mueller's room:bed",
    "predicate": "be", "object": "used",
    "description": "bed is being used", "embedding_key": "bed is being used",
    "poignancy": 1, "keywords": ["used", "bed"], "filling": []
  },
  "node_591": {
    "node_count": 591, "type_count": 109, "type": "thought", "depth": 1,
    "created": "2023-02-14 00:00:00", "expiration": "2023-03-16 00:00:00",
    "subject": "Klaus Mueller", "predicate": "plan", "object": "Tuesday February 14",
    "description": "This is Klaus Mueller's plan for Tuesday February 14: ...",
    "embedding_key": "This is Klaus Mueller's plan for Tuesday February 14: ...",
    "poignancy": 5, "keywords": ["plan"], "filling": null
  }
}
```

### Field-type ambiguities (flagged for follow-up)

1. **`filling`** can be `null`, `[]`, or `list[str]`. The loader (`agent_memory.py:52`) normalizes `None → []`. Importer should preserve `null` vs `[]` distinction only if downstream cares (current SQLite model uses nullable JSON, so `null` is preserved).
2. **`created`/`expiration`** use `"%Y-%m-%d %H:%M:%S"` here but `scratch.json#curr_time` uses `"%B %d, %Y, %H:%M:%S"`. Don't share a parser.
3. **`keywords`** is `list[str]` on disk but `set[str]` in memory after load — order is not semantically meaningful.

---

## `personas/<name>/bootstrap_memory/associative_memory/kw_strength.json`

Tracks how many times each keyword has been seen across this persona's event /
thought memories. Used to gate reflection.

```json
{
  "kw_strength_event":   { "<keyword>": int, ... },
  "kw_strength_thought": { "<keyword>": int, ... }
}
```

Both top-level keys are always present. Keywords are **lowercased** here (the
simulator lowercases on write; see `agent_memory.py:236`). Counts are non-negative integers.

Pristine bootstrap: both inner dicts are `{}`.

> **Cross-reference:** keyword strings here do **not** need to appear in any
> single node's `keywords` list — they are aggregate strength counters, not an
> index. The per-keyword node index (`event_keywords` / `thought_keywords` /
> `chat_keywords` in `AgentMemory`) is rebuilt from `nodes.json` on load and
> is **never persisted**. The SQLite `memory_keywords_to_*` tables in this fork
> are the moral equivalent of those in-memory indices, NOT of `kw_strength.json`.

---

## `personas/<name>/bootstrap_memory/associative_memory/embeddings.json`

```json
{ "<embedding_key>": [float, float, ...], ... }
```

Flat dict mapping `embedding_key` (the same string stored on each node) to a
fixed-dimensional embedding vector (1536 floats for OpenAI `text-embedding-ada-002`,
likely different for other models). Pristine bootstrap: `{}`.

> **NOTE — stanford-town-vue does not persist embeddings.** Per the M2 spec,
> the importer **skips this file** and the exporter writes an empty `{}` (or
> regenerates lazily on demand). This section documents the original format for
> compatibility / forensic reading of legacy sims only.

---

## `llm_logs.jsonl`

One append-only JSON-Lines stream per sim, written by
`backend/simulator/utils/llm_logger.py`. Each line is a single object.

| Field               | Type        | Required | Description                                                                                       |
| ------------------- | ----------- | -------- | ------------------------------------------------------------------------------------------------- |
| `seq`               | int         | yes      | 0-based monotonic call counter within the process.                                                |
| `ts`                | str         | yes      | Wall-clock timestamp, ISO 8601 with `.milliseconds` precision and local TZ offset (`...+08:00`).  |
| `step`              | int \| null | yes      | Sim step this call originated in; `null` if no step context was set.                              |
| `persona`           | str \| null | yes      | Persona that triggered the call; `null` for sim-wide calls.                                       |
| `action`            | str \| null | yes      | Action class name (e.g. `"WakeUp"`, `"AgentEventTriple"`); `null` if not set.                     |
| `model`             | str \| null | yes      | Model identifier (e.g. `"deepseek-v4-flash"`).                                                    |
| `params`            | dict        | yes      | Call-time LLM params (e.g. `{"temperature": 0.0, "max_tokens": 64}`). Shape is provider-specific. |
| `prompt`            | str         | yes      | Full prompt text.                                                                                 |
| `response`          | str \| null | yes      | Raw response text; `null` when the call errored before producing a response.                      |
| `usage`             | dict \| null | yes     | Provider usage block, e.g. `{"prompt_tokens": 205, "completion_tokens": 1, "total_tokens": 206}`. |
| `cost_usd`          | float \| null | yes    | Estimated USD cost.                                                                               |
| `latency_ms`        | int         | yes      | Wall-clock latency in milliseconds.                                                               |
| `retry_idx`         | int         | yes      | 0 for the first attempt, increments on retry.                                                     |
| `used_fail_default` | bool        | yes      | `true` if the call fell through to a hard-coded fallback response.                                |
| `error`             | str \| null | yes      | Error message; `null` on success.                                                                 |

**Sample:**

```json
{"seq": 0, "ts": "2026-05-12T19:21:21.376+08:00", "step": 0, "persona": "Klaus Mueller", "action": "WakeUp", "model": "deepseek-v4-flash", "params": {"temperature": 0.0, "max_tokens": 64}, "prompt": "Name: Klaus Mueller...", "response": "7", "usage": {"prompt_tokens": 205, "completion_tokens": 1, "total_tokens": 206}, "cost_usd": 2.9e-05, "latency_ms": 735, "retry_idx": 0, "used_fail_default": false, "error": null}
```

> **Schema drift from the SQLite `llm_calls` table:** the table has columns
> `provider`, `prompt_tokens`, `completion_tokens`, `error` and does **not**
> carry `params` / `cost_usd` / `retry_idx` / `used_fail_default` / `seq` /
> `action`. The importer must split `usage` into `prompt_tokens` and
> `completion_tokens`, infer `provider` from `model` (or stash it in
> `extra_json` on the LLM profile), and drop the extra fields. The exporter
> can leave them blank / synthesise reasonable defaults.

---

## Mapping to SQLite tables

| Source file & path                                                                | Target table.column                                                             |
| --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| `reverie/meta.json#$dir-name (sim_code)`                                          | `simulations.sim_code`                                                          |
| `reverie/meta.json#fork_sim_code`                                                 | `simulations.fork_sim_code`                                                     |
| `reverie/meta.json#start_date` (+ user-chosen HH:MM:SS)                           | `simulations.start_time_iso` (ISO 8601)                                         |
| `reverie/meta.json#curr_time`                                                     | `simulations.curr_time_iso` (ISO 8601)                                          |
| `reverie/meta.json#sec_per_step`                                                  | `simulations.sec_per_step`                                                      |
| `reverie/meta.json#maze_name`                                                     | `simulations.maze_name`                                                         |
| `reverie/meta.json#step`                                                          | `simulations.step`                                                              |
| `reverie/meta.json#persona_names[i]`                                              | one row per name in `personas` (`personas.name`, `personas.sim_id`)             |
| `environment/{step}.json`                                                         | `step_environments` (one row per step, full dict in `payload_json`)             |
| `environment/{step}.json#<persona>.x/y`                                           | also denormalised into `step_movements.x/y` if no movement file exists yet      |
| `movement/{step}.json#persona[name].movement`                                     | `step_movements.x`, `step_movements.y`                                          |
| `movement/{step}.json#persona[name].description`                                  | `step_movements.description`                                                    |
| `movement/{step}.json#persona[name].pronunciatio`                                 | `step_movements.pronunciatio`                                                   |
| `movement/{step}.json#persona[name].chat`                                         | `step_movements.chat_json`                                                      |
| `movement/{step}.json#persona[name].description` (`X @ ADDRESS` suffix)           | `step_movements.location_path` (parsed from `description.split(" @ ", 1)[1]`)   |
| `movement/{step}.json#meta.curr_time`                                             | (informational; reconciled with `simulations.curr_time_iso`)                    |
| `personas/<name>/bootstrap_memory/scratch.json` (whole blob)                      | `personas.scratch_json`                                                         |
| `personas/<name>/bootstrap_memory/scratch.json#age`                               | `personas.age`                                                                  |
| `personas/<name>/bootstrap_memory/scratch.json#daily_plan_req`                    | `personas.plan_text`                                                            |
| `personas/<name>/bootstrap_memory/spatial_memory.json` (whole tree)               | `spatial_memory_trees.tree_json`                                                |
| `personas/<name>/.../associative_memory/nodes.json#<node_id>.node_count`          | `memory_nodes.node_count`                                                       |
| `... #<node_id>` (the key itself)                                                 | `memory_nodes.node_id`                                                          |
| `... #<node_id>.type_count`                                                       | `memory_nodes.type_count`                                                       |
| `... #<node_id>.type`                                                             | `memory_nodes.node_type`                                                        |
| `... #<node_id>.depth`                                                            | `memory_nodes.depth`                                                            |
| `... #<node_id>.created` (game-time `"%Y-%m-%d %H:%M:%S"` → step index)            | `memory_nodes.created` (integer step). **Importer must convert game-time to a step number using `simulations.start_time_iso` + `sec_per_step`.** |
| `... #<node_id>.expiration`                                                       | `memory_nodes.expiration_step` (nullable, same conversion)                      |
| `... #<node_id>.subject / predicate / object`                                     | `memory_nodes.subject / predicate / object`                                     |
| `... #<node_id>.description`                                                      | `memory_nodes.description`                                                      |
| `... #<node_id>.poignancy`                                                        | `memory_nodes.poignancy`                                                        |
| `... #<node_id>.keywords`                                                         | `memory_nodes.keywords_json`                                                    |
| `... #<node_id>.filling`                                                          | `memory_nodes.filling_json`                                                    |
| `... #<node_id>.embedding_key`                                                    | **dropped on import** (no embeddings in this fork)                              |
| `kw_strength.json#kw_strength_event` / `kw_strength_thought`                      | **not persisted** in current schema. Exporter rebuilds them by walking `memory_nodes` and counting keywords per `(persona_id, node_type)`. Importer drops them. |
| `embeddings.json` (whole file)                                                    | **not persisted**. Importer skips; exporter writes `{}`.                        |
| `llm_logs.jsonl#ts`                                                               | `llm_calls.ts`                                                                  |
| `llm_logs.jsonl#step`                                                             | `llm_calls.step`                                                                |
| `llm_logs.jsonl#persona`                                                          | `llm_calls.persona_name`                                                        |
| `llm_logs.jsonl#model`                                                            | `llm_calls.model`                                                               |
| `llm_logs.jsonl#prompt`                                                           | `llm_calls.prompt`                                                              |
| `llm_logs.jsonl#response`                                                         | `llm_calls.response` (empty string if `null`)                                   |
| `llm_logs.jsonl#usage.prompt_tokens`                                              | `llm_calls.prompt_tokens`                                                       |
| `llm_logs.jsonl#usage.completion_tokens`                                          | `llm_calls.completion_tokens`                                                   |
| `llm_logs.jsonl#latency_ms`                                                       | `llm_calls.latency_ms`                                                          |
| `llm_logs.jsonl#error`                                                            | `llm_calls.error`                                                               |
| `llm_logs.jsonl#action / params / cost_usd / retry_idx / used_fail_default / seq` | **dropped on import**. Exporter writes plausible defaults (`action: null`, `params: {}`, etc.). |
| `llm_logs.jsonl` — `provider` field                                               | **not present** in the JSONL. Importer derives from `model` prefix (`deepseek-*` → `"deepseek"`, etc.); writes to `llm_calls.provider`. |

---

## Loader / writer cross-references (for the importer & exporter authors)

- `backend/simulator/utils/mg_ga_transform.py`
  - `get_reverie_meta(sim_code)` → reads `reverie/meta.json`
  - `save_movement(role_name, role_move, step, sim_code, curr_time)` → writes `movement/{step}.json`
  - `save_environment(role_name, step, sim_code, movement)` → writes `environment/{step}.json`
- `backend/simulator/memory/scratch.py` — `Scratch.init_scratch_from_path` / `Scratch.save`
- `backend/simulator/memory/spatial_memory.py` — `MemoryTree.set_mem_path` / `MemoryTree.save`
- `backend/simulator/memory/agent_memory.py` — `AgentMemory.load` / `AgentMemory.save` (reads/writes all three associative-memory files)
- `backend/simulator/utils/llm_logger.py` — `log_call` writes `llm_logs.jsonl`

---

## Known inconsistencies (importer should be lenient)

1. **`meta.json` location:** demo archives place it at `<sim_root>/meta.json`; the live simulator expects `<sim_root>/reverie/meta.json`. Try `reverie/meta.json` first, then fall back to `<sim_root>/meta.json`.
2. **`movement` envelope:** live sims use `{ "persona": {...}, "meta": {...} }`; compressed archives use a flat `<step> → {persona → ...}` mapping in `master_movement.json` with no `meta` block.
3. **`Scratch.chat`:** declared `Optional[str]` in `scratch.py`, but at runtime the value is set to `list[[str, str]]` (a transcript). In the bootstrap on-disk form it is always `null`, so the discrepancy never surfaces in stored files — but the pydantic schema here uses `list[list[str]] | str | None` to tolerate both.
4. **`f_daily_schedule` element type:** declared `list[Union[int, str]]` in `scratch.py`, but observed data is always `[str, int]` in that order.
5. **`nodes.json` is written newest-first:** `agent_memory.AgentMemory.save` iterates `range(len(storage))` in reverse. The loader compensates by indexing into `f"node_{count+1}"`, so insertion order is preserved logically. Importers should NOT rely on key iteration order of the dict.
