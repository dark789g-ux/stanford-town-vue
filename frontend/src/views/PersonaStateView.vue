<script setup lang="ts">
// PersonaStateView - shows scratch, spatial memory, and the memory stream for a persona.
import { computed, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import type { TablePaginationConfig } from 'ant-design-vue'
import { usePersonaStateStore, type MemoryNode } from '@/stores/personaState'

const props = defineProps<{ id: string | number; name: string }>()

const store = usePersonaStateStore()
const {
  persona,
  scratch,
  spatialMemory,
  memory,
  memoryTotal,
  type,
  offset,
  limit,
  loading,
  error,
} = storeToRefs(store)

const simId = computed(() => Number(props.id))

onMounted(async () => {
  await store.load(simId.value, props.name)
  await store.loadMemory(simId.value, props.name)
})

// ---- scratch ---------------------------------------------------------------

function isFlatObject(obj: Record<string, unknown> | null): boolean {
  if (!obj) return false
  return Object.values(obj).every(
    (v) => v === null || typeof v !== 'object',
  )
}

const scratchIsFlat = computed(() => isFlatObject(scratch.value))

function stringify(v: unknown): string {
  return JSON.stringify(v, null, 2)
}

function displayValue(v: unknown): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

// ---- memory stream ---------------------------------------------------------

const NODE_TYPE_COLOR: Record<string, string> = {
  event: 'blue',
  thought: 'purple',
  chat: 'green',
}
function nodeTypeColor(t: string): string {
  return NODE_TYPE_COLOR[t] ?? 'default'
}

const TYPE_OPTIONS = [
  { label: 'All', value: '' },
  { label: 'Event', value: 'event' },
  { label: 'Thought', value: 'thought' },
  { label: 'Chat', value: 'chat' },
]

const memoryColumns = [
  { title: 'Type', key: 'node_type', width: 100 },
  { title: 'Step', dataIndex: 'created', key: 'created', width: 80 },
  { title: 'S / P / O', key: 'spo' },
  { title: 'Description', dataIndex: 'description', key: 'description' },
  { title: 'Poignancy', dataIndex: 'poignancy', key: 'poignancy', width: 100 },
  { title: 'Keywords', key: 'keywords' },
]

const pagination = computed<TablePaginationConfig>(() => ({
  current: Math.floor(offset.value / limit.value) + 1,
  pageSize: limit.value,
  total: memoryTotal.value,
  showSizeChanger: true,
  pageSizeOptions: ['10', '20', '50'],
}))

function onTableChange(pag: TablePaginationConfig): void {
  const pageSize = pag.pageSize ?? limit.value
  const current = pag.current ?? 1
  void store.loadMemory(simId.value, props.name, {
    offset: (current - 1) * pageSize,
    limit: pageSize,
  })
}

function onTypeChange(value: string): void {
  void store.loadMemory(simId.value, props.name, {
    type: value,
    offset: 0,
    limit: limit.value,
  })
}
</script>

<template>
  <div>
    <div
      style="
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 16px;
      "
    >
      <a-typography-title :level="2" style="margin: 0">
        {{ persona?.name ?? props.name }}
      </a-typography-title>
      <router-link
        :to="{ name: 'sim-replay', params: { id: String(props.id) } }"
      >
        ← Back to simulation
      </router-link>
    </div>

    <a-alert
      v-if="error"
      type="error"
      :message="error"
      show-icon
      closable
      style="margin-bottom: 16px"
    />

    <a-spin :spinning="loading">
      <a-descriptions
        bordered
        size="small"
        :column="2"
        style="margin-bottom: 16px"
      >
        <a-descriptions-item label="ID">
          {{ persona?.id ?? '—' }}
        </a-descriptions-item>
        <a-descriptions-item label="Name">
          {{ persona?.name ?? '—' }}
        </a-descriptions-item>
        <a-descriptions-item label="Age">
          {{ persona?.age ?? '—' }}
        </a-descriptions-item>
        <a-descriptions-item label="Plan" :span="2">
          {{ persona?.plan_text ?? '—' }}
        </a-descriptions-item>
      </a-descriptions>

      <a-tabs>
        <a-tab-pane key="scratch" tab="Scratch">
          <a-empty v-if="!scratch" description="No scratch data" />
          <a-descriptions
            v-else-if="scratchIsFlat"
            bordered
            size="small"
            :column="1"
          >
            <a-descriptions-item
              v-for="(value, key) in scratch"
              :key="key"
              :label="key"
            >
              {{ displayValue(value) }}
            </a-descriptions-item>
          </a-descriptions>
          <pre v-else class="json-dump">{{ stringify(scratch) }}</pre>
        </a-tab-pane>

        <a-tab-pane key="spatial" tab="Spatial Memory">
          <a-empty
            v-if="!spatialMemory"
            description="No spatial memory data"
          />
          <pre v-else class="json-dump">{{ stringify(spatialMemory) }}</pre>
        </a-tab-pane>

        <a-tab-pane key="memory" tab="Memory Stream">
          <div style="margin-bottom: 12px">
            <a-select
              :value="type ?? ''"
              :options="TYPE_OPTIONS"
              style="width: 160px"
              @change="(v: unknown) => onTypeChange(String(v))"
            />
          </div>
          <a-table
            :columns="memoryColumns"
            :data-source="memory"
            :row-key="(r: MemoryNode) => r.id"
            :pagination="pagination"
            size="middle"
            @change="onTableChange"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'node_type'">
                <a-tag :color="nodeTypeColor((record as MemoryNode).node_type)">
                  {{ (record as MemoryNode).node_type }}
                </a-tag>
              </template>

              <template v-else-if="column.key === 'spo'">
                {{ (record as MemoryNode).subject }} /
                {{ (record as MemoryNode).predicate }} /
                {{ (record as MemoryNode).object }}
              </template>

              <template v-else-if="column.key === 'keywords'">
                <a-space wrap :size="[4, 4]">
                  <a-tag
                    v-for="kw in (record as MemoryNode).keywords"
                    :key="kw"
                  >
                    {{ kw }}
                  </a-tag>
                  <span
                    v-if="(record as MemoryNode).keywords.length === 0"
                  >
                    —
                  </span>
                </a-space>
              </template>
            </template>
          </a-table>
        </a-tab-pane>
      </a-tabs>
    </a-spin>
  </div>
</template>

<style scoped>
.json-dump {
  background: #fafafa;
  border: 1px solid #f0f0f0;
  border-radius: 6px;
  padding: 12px;
  font-size: 12px;
  max-height: 480px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
