<script setup lang="ts">
// LlmLogsView - browse LLM call logs for a simulation.
import { onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useLlmLogsStore } from '@/stores/llmLogs'
import type { LlmCallSummary } from '@/stores/llmLogs'

const props = defineProps<{ id: string | number }>()

const store = useLlmLogsStore()
const { logs, total, offset, limit, loading, error } = storeToRefs(store)

const simId = Number(props.id)

const personaInput = ref('')
const modelInput = ref('')

const drawerOpen = ref(false)
const detailLoading = ref(false)
const { selectedCall } = storeToRefs(store)

const columns = [
  { title: 'ID', dataIndex: 'id', key: 'id' },
  { title: 'Persona', key: 'persona_name' },
  { title: 'Step', key: 'step' },
  { title: 'Time', dataIndex: 'ts', key: 'ts' },
  { title: 'Provider', dataIndex: 'provider', key: 'provider' },
  { title: 'Model', dataIndex: 'model', key: 'model' },
  { title: 'Prompt Tokens', dataIndex: 'prompt_tokens', key: 'prompt_tokens' },
  {
    title: 'Completion Tokens',
    dataIndex: 'completion_tokens',
    key: 'completion_tokens',
  },
  { title: 'Latency (ms)', dataIndex: 'latency_ms', key: 'latency_ms' },
  { title: 'Error', key: 'error' },
]

onMounted(() => {
  void store.fetch(simId)
})

function applyFilters(): void {
  store.setFilters(personaInput.value, modelInput.value)
  void store.fetch(simId, { offset: 0 })
}

function clearFilters(): void {
  personaInput.value = ''
  modelInput.value = ''
  store.setFilters('', '')
  void store.fetch(simId, { offset: 0 })
}

function onPageChange(page: number, pageSize: number): void {
  void store.fetch(simId, { offset: (page - 1) * pageSize, limit: pageSize })
}

async function openDetail(record: LlmCallSummary): Promise<void> {
  drawerOpen.value = true
  detailLoading.value = true
  try {
    await store.loadCall(simId, record.id)
  } finally {
    detailLoading.value = false
  }
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
        LLM Logs
      </a-typography-title>
    </div>

    <a-alert
      v-if="error"
      type="error"
      :message="error"
      show-icon
      closable
      style="margin-bottom: 16px"
    />

    <a-space style="margin-bottom: 16px" wrap>
      <a-input
        v-model:value="personaInput"
        placeholder="Persona"
        allow-clear
        style="width: 200px"
        @press-enter="applyFilters"
      />
      <a-input
        v-model:value="modelInput"
        placeholder="Model"
        allow-clear
        style="width: 200px"
        @press-enter="applyFilters"
      />
      <a-button type="primary" @click="applyFilters">Filter</a-button>
      <a-button @click="clearFilters">Clear</a-button>
    </a-space>

    <a-spin :spinning="loading">
      <a-empty
        v-if="!loading && logs.length === 0"
        description="No LLM calls logged yet"
      />

      <a-table
        v-else
        :columns="columns"
        :data-source="logs"
        :row-key="(r: LlmCallSummary) => r.id"
        :pagination="{
          total,
          current: Math.floor(offset / limit) + 1,
          pageSize: limit,
          showSizeChanger: true,
          onChange: onPageChange,
        }"
        size="middle"
        :custom-row="
          (record: LlmCallSummary) => ({
            onClick: () => openDetail(record),
            style: { cursor: 'pointer' },
          })
        "
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'persona_name'">
            {{ (record as LlmCallSummary).persona_name ?? '—' }}
          </template>

          <template v-else-if="column.key === 'step'">
            {{ (record as LlmCallSummary).step ?? '—' }}
          </template>

          <template v-else-if="column.key === 'error'">
            <a-tag v-if="(record as LlmCallSummary).error" color="red">
              {{ (record as LlmCallSummary).error }}
            </a-tag>
            <span v-else>—</span>
          </template>
        </template>
      </a-table>
    </a-spin>

    <a-drawer
      v-model:open="drawerOpen"
      title="LLM Call Detail"
      :width="640"
    >
      <a-spin :spinning="detailLoading">
        <a-descriptions
          v-if="selectedCall"
          bordered
          :column="1"
          size="small"
        >
          <a-descriptions-item label="ID">
            {{ selectedCall.id }}
          </a-descriptions-item>
          <a-descriptions-item label="Persona">
            {{ selectedCall.persona_name ?? '—' }}
          </a-descriptions-item>
          <a-descriptions-item label="Step">
            {{ selectedCall.step ?? '—' }}
          </a-descriptions-item>
          <a-descriptions-item label="Time">
            {{ selectedCall.ts }}
          </a-descriptions-item>
          <a-descriptions-item label="Provider">
            {{ selectedCall.provider }}
          </a-descriptions-item>
          <a-descriptions-item label="Model">
            {{ selectedCall.model }}
          </a-descriptions-item>
          <a-descriptions-item label="Prompt Tokens">
            {{ selectedCall.prompt_tokens }}
          </a-descriptions-item>
          <a-descriptions-item label="Completion Tokens">
            {{ selectedCall.completion_tokens }}
          </a-descriptions-item>
          <a-descriptions-item label="Latency (ms)">
            {{ selectedCall.latency_ms }}
          </a-descriptions-item>
          <a-descriptions-item label="Error">
            <a-tag v-if="selectedCall.error" color="red">
              {{ selectedCall.error }}
            </a-tag>
            <span v-else>—</span>
          </a-descriptions-item>
        </a-descriptions>

        <template v-if="selectedCall">
          <a-typography-title :level="5" style="margin-top: 16px">
            Prompt
          </a-typography-title>
          <pre class="llm-log-block">{{ selectedCall.prompt }}</pre>

          <a-typography-title :level="5" style="margin-top: 16px">
            Response
          </a-typography-title>
          <pre class="llm-log-block">{{ selectedCall.response }}</pre>
        </template>
      </a-spin>
    </a-drawer>
  </div>
</template>

<style scoped>
.llm-log-block {
  font-family: 'Menlo', 'Consolas', monospace;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 320px;
  overflow: auto;
  background: #f5f5f5;
  border: 1px solid #e8e8e8;
  border-radius: 4px;
  padding: 8px;
  margin: 0;
}
</style>
