<script setup lang="ts">
// SimulationTable - tabular list of simulations with contextual actions.
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import type { Simulation, SimStatus } from '@/types/sim'
import { useSimulationsStore } from '@/stores/simulations'

const props = defineProps<{
  sims: Simulation[]
}>()

const router = useRouter()
const store = useSimulationsStore()

type TagColor = 'green' | 'orange' | 'blue' | 'red' | 'default'

const STATUS_COLOR: Record<SimStatus, TagColor> = {
  running: 'green',
  paused: 'orange',
  completed: 'blue',
  failed: 'red',
  idle: 'default',
  stopped: 'default',
  interrupted: 'default',
}

const LIVE_STATUSES: SimStatus[] = ['running', 'paused']

function statusColor(status: SimStatus): TagColor {
  return STATUS_COLOR[status] ?? 'default'
}

function isLive(status: SimStatus): boolean {
  return LIVE_STATUSES.includes(status)
}

function view(sim: Simulation): void {
  const kind = isLive(sim.status) ? 'live' : 'replay'
  router.push(`/sims/${sim.id}/${kind}`)
}

async function refresh(): Promise<void> {
  await store.fetchAll()
}

async function runAction(
  label: string,
  fn: () => Promise<unknown>,
): Promise<void> {
  try {
    await fn()
    await refresh()
  } catch (err) {
    message.error(`${label} failed: ${(err as Error).message}`)
  }
}

function pause(sim: Simulation): void {
  void runAction('Pause', () => store.pause(sim.id))
}

function resume(sim: Simulation): void {
  void runAction('Resume', () => store.resume(sim.id))
}

function stop(sim: Simulation): void {
  void runAction('Stop', () => store.stop(sim.id))
}

function remove(sim: Simulation): void {
  void runAction('Delete', () => store.deleteSim(sim.id))
}

function llmLogs(sim: Simulation): void {
  router.push(`/sims/${sim.id}/llm-logs`)
}

const columns = [
  { title: 'Sim Code', dataIndex: 'sim_code', key: 'sim_code' },
  { title: 'Status', key: 'status' },
  { title: 'Progress', key: 'progress' },
  { title: 'Current Time', dataIndex: 'curr_time_iso', key: 'curr_time_iso' },
  { title: 'Created', dataIndex: 'created_at', key: 'created_at' },
  { title: 'Actions', key: 'actions' },
]
</script>

<template>
  <a-table
    :columns="columns"
    :data-source="props.sims"
    :row-key="(r: Simulation) => r.id"
    :pagination="{ pageSize: 10, hideOnSinglePage: true }"
    size="middle"
  >
    <template #bodyCell="{ column, record }">
      <template v-if="column.key === 'status'">
        <a-tag :color="statusColor((record as Simulation).status)">
          {{ (record as Simulation).status }}
        </a-tag>
      </template>

      <template v-else-if="column.key === 'progress'">
        {{ (record as Simulation).step }} / {{ (record as Simulation).n_round }}
      </template>

      <template v-else-if="column.key === 'curr_time_iso'">
        {{ (record as Simulation).curr_time_iso ?? '—' }}
      </template>

      <template v-else-if="column.key === 'created_at'">
        {{ (record as Simulation).created_at ?? '—' }}
      </template>

      <template v-else-if="column.key === 'actions'">
        <a-space wrap>
          <a-button size="small" type="link" @click="view(record as Simulation)">
            View
          </a-button>

          <a-button
            size="small"
            type="link"
            @click="llmLogs(record as Simulation)"
          >
            LLM Logs
          </a-button>

          <a-button
            v-if="(record as Simulation).status === 'running'"
            size="small"
            @click="pause(record as Simulation)"
          >
            Pause
          </a-button>
          <a-button
            v-if="(record as Simulation).status === 'paused'"
            size="small"
            @click="resume(record as Simulation)"
          >
            Resume
          </a-button>
          <a-button
            v-if="isLive((record as Simulation).status)"
            size="small"
            danger
            @click="stop(record as Simulation)"
          >
            Stop
          </a-button>

          <a-popconfirm
            title="Delete this simulation?"
            ok-text="Delete"
            cancel-text="Cancel"
            @confirm="remove(record as Simulation)"
          >
            <a-button size="small" type="link" danger>Delete</a-button>
          </a-popconfirm>
        </a-space>
      </template>
    </template>
  </a-table>
</template>
