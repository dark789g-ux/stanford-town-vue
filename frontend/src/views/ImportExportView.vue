<script setup lang="ts">
// ImportExportView - import legacy/fork sim folders into the DB, export sims
// back to disk, and surface the effective backend config.
import { computed, onMounted, ref } from 'vue'
import { message, notification } from 'ant-design-vue'
import { ApiError } from '@/api/client'
import {
  exportSimulation,
  importSimulation,
  listForkCandidates,
  type ExportLayout,
  type ForkInfo,
  type OnConflict,
} from '@/api/imports'
import { useSimulationsStore } from '@/stores/simulations'
import { useAppConfigStore } from '@/stores/appConfig'
import type { Simulation } from '@/types/sim'

// --- backend config panel ----------------------------------------------------
const appConfig = useAppConfigStore()

// --- import: fork list -------------------------------------------------------
const forks = ref<ForkInfo[]>([])
const forksLoading = ref(false)
const forksError = ref<string | null>(null)

const forkColumns = [
  { title: 'Sim code', dataIndex: 'sim_code', key: 'sim_code' },
  { title: 'Source', dataIndex: 'source', key: 'source' },
  { title: 'Personas', dataIndex: 'persona_count', key: 'persona_count' },
  { title: 'Steps', dataIndex: 'step_count', key: 'step_count' },
  { title: 'Path', dataIndex: 'path', key: 'path' },
  { title: '', key: 'action' },
]

function sourceColor(source: string): string {
  switch (source) {
    case 'compressed_storage':
      return 'blue'
    case 'storage':
      return 'green'
    case 'db':
      return 'purple'
    default:
      return 'default'
  }
}

async function loadForks(): Promise<void> {
  forksLoading.value = true
  forksError.value = null
  try {
    forks.value = await listForkCandidates()
  } catch (e) {
    forksError.value = e instanceof Error ? e.message : String(e)
  } finally {
    forksLoading.value = false
  }
}

// --- import: modal -----------------------------------------------------------
const importModalOpen = ref(false)
const importSubmitting = ref(false)
const importSourcePath = ref('')
const importSimCodeOverride = ref('')
const importOnConflict = ref<OnConflict>('fail')

function openImportModal(path = ''): void {
  importSourcePath.value = path
  importSimCodeOverride.value = ''
  importOnConflict.value = 'fail'
  importModalOpen.value = true
}

const simulations = useSimulationsStore()

async function submitImport(): Promise<void> {
  if (!importSourcePath.value.trim()) {
    message.error('Source path is required')
    return
  }
  importSubmitting.value = true
  try {
    const result = await importSimulation({
      source_path: importSourcePath.value.trim(),
      sim_code_override: importSimCodeOverride.value.trim() || undefined,
      on_conflict: importOnConflict.value,
    })
    const counts = result.counts ?? {}
    const lines = Object.entries(counts)
      .map(([k, v]) => `${k}: ${v}`)
      .join('  ·  ')
    notification.success({
      message: `Imported "${result.sim_code}" (#${result.sim_id})`,
      description: lines || 'No row counts reported.',
      duration: 6,
    })
    importModalOpen.value = false
    await Promise.all([loadForks(), simulations.fetchAll()])
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) {
      notification.warning({
        message: 'Simulation already exists',
        description:
          'A simulation with this code already exists. Choose "replace" to overwrite it, or "skip" to keep the existing one.',
        duration: 8,
      })
    } else {
      message.error(
        `Import failed: ${e instanceof Error ? e.message : String(e)}`,
      )
    }
  } finally {
    importSubmitting.value = false
  }
}

// --- export ------------------------------------------------------------------
const simsLoading = computed(() => simulations.loading)
const simOptions = computed(() =>
  simulations.sims.map((s: Simulation) => ({
    label: `${s.sim_code} (#${s.id})`,
    value: s.id,
  })),
)

const exportModalOpen = ref(false)
const exportSubmitting = ref(false)
const exportSimId = ref<number | null>(null)
const exportTargetDir = ref('')
const exportLayout = ref<ExportLayout>('compressed')
const exportResultPath = ref<string | null>(null)

function openExportModal(): void {
  exportSimId.value = simulations.sims[0]?.id ?? null
  exportTargetDir.value = ''
  exportLayout.value = 'compressed'
  exportResultPath.value = null
  exportModalOpen.value = true
}

async function submitExport(): Promise<void> {
  if (exportSimId.value == null) {
    message.error('Pick a simulation to export')
    return
  }
  if (!exportTargetDir.value.trim()) {
    message.error('Target directory is required')
    return
  }
  exportSubmitting.value = true
  exportResultPath.value = null
  try {
    const result = await exportSimulation(exportSimId.value, {
      target_dir: exportTargetDir.value.trim(),
      layout: exportLayout.value,
    })
    exportResultPath.value = result.output_path
    notification.success({
      message: `Exported "${result.sim_code}"`,
      description: result.output_path,
      duration: 6,
    })
  } catch (e) {
    message.error(
      `Export failed: ${e instanceof Error ? e.message : String(e)}`,
    )
  } finally {
    exportSubmitting.value = false
  }
}

onMounted(() => {
  void loadForks()
  void simulations.fetchAll()
  void appConfig.fetchConfig()
})
</script>

<template>
  <div class="import-export">
    <a-typography-title :level="2">Import / Export</a-typography-title>
    <a-typography-paragraph type="secondary">
      Import existing simulation folders into the database, or export a
      simulation back to disk.
    </a-typography-paragraph>

    <!-- backend config panel -->
    <a-card size="small" title="Backend config" class="import-export__config">
      <a-spin :spinning="appConfig.loading">
        <a-alert
          v-if="appConfig.error"
          type="error"
          :message="appConfig.error"
          show-icon
        />
        <a-descriptions v-else-if="appConfig.config" :column="2" size="small">
          <a-descriptions-item label="Database">
            {{ appConfig.config.database_url }}
          </a-descriptions-item>
          <a-descriptions-item label="Assets dir">
            {{ appConfig.config.assets_dir }}
          </a-descriptions-item>
          <a-descriptions-item label="Logs dir">
            {{ appConfig.config.logs_dir }}
          </a-descriptions-item>
          <a-descriptions-item label="Frontend dev origin">
            {{ appConfig.config.frontend_dev_origin }}
          </a-descriptions-item>
          <a-descriptions-item label="Secret key">
            <a-tag :color="appConfig.secretKeyPresent ? 'green' : 'red'">
              {{ appConfig.secretKeyPresent ? 'present' : 'missing' }}
            </a-tag>
          </a-descriptions-item>
          <a-descriptions-item label="LLM profiles">
            {{ appConfig.llmProfilesCount }}
          </a-descriptions-item>
        </a-descriptions>
      </a-spin>
    </a-card>

    <!-- import section -->
    <a-card title="Import" class="import-export__card">
      <template #extra>
        <a-space>
          <a-button @click="openImportModal()">Import from a path…</a-button>
          <a-button :loading="forksLoading" @click="loadForks">Refresh</a-button>
        </a-space>
      </template>

      <a-alert
        v-if="forksError"
        type="error"
        :message="forksError"
        show-icon
        style="margin-bottom: 12px"
      />

      <a-table
        :columns="forkColumns"
        :data-source="forks"
        :loading="forksLoading"
        :row-key="(row: ForkInfo) => `${row.source}:${row.path}`"
        size="small"
        :pagination="{ pageSize: 8, hideOnSinglePage: true }"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'source'">
            <a-tag :color="sourceColor((record as ForkInfo).source)">
              {{ (record as ForkInfo).source }}
            </a-tag>
          </template>
          <template v-else-if="column.key === 'persona_count'">
            {{ (record as ForkInfo).persona_names?.length ?? 0 }}
          </template>
          <template v-else-if="column.key === 'path'">
            <a-typography-text
              :ellipsis="{ tooltip: (record as ForkInfo).path }"
              :content="(record as ForkInfo).path"
              style="max-width: 280px"
            />
          </template>
          <template v-else-if="column.key === 'action'">
            <a-button
              type="link"
              size="small"
              @click="openImportModal((record as ForkInfo).path)"
            >
              Import
            </a-button>
          </template>
        </template>
      </a-table>
    </a-card>

    <!-- export section -->
    <a-card title="Export" class="import-export__card">
      <template #extra>
        <a-button
          type="primary"
          :disabled="simulations.sims.length === 0"
          @click="openExportModal"
        >
          Export a simulation…
        </a-button>
      </template>
      <a-typography-paragraph type="secondary" style="margin: 0">
        Export writes a simulation's full history to a directory on the server,
        either as a compressed fork or as a live (uncompressed) layout.
      </a-typography-paragraph>
      <a-spin v-if="simsLoading" size="small" style="margin-top: 8px" />
    </a-card>

    <!-- import modal -->
    <a-modal
      v-model:open="importModalOpen"
      title="Import simulation"
      :confirm-loading="importSubmitting"
      ok-text="Import"
      @ok="submitImport"
    >
      <a-form layout="vertical">
        <a-form-item label="Source path" required>
          <a-input
            v-model:value="importSourcePath"
            placeholder="/path/to/sim/folder"
          />
        </a-form-item>
        <a-form-item label="Sim code override (optional)">
          <a-input
            v-model:value="importSimCodeOverride"
            placeholder="Leave blank to use the folder's sim_code"
          />
        </a-form-item>
        <a-form-item label="On conflict">
          <a-radio-group v-model:value="importOnConflict">
            <a-radio value="fail">Fail</a-radio>
            <a-radio value="replace">Replace</a-radio>
            <a-radio value="skip">Skip</a-radio>
          </a-radio-group>
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- export modal -->
    <a-modal
      v-model:open="exportModalOpen"
      title="Export simulation"
      :confirm-loading="exportSubmitting"
      ok-text="Export"
      @ok="submitExport"
    >
      <a-form layout="vertical">
        <a-form-item label="Simulation" required>
          <a-select
            v-model:value="exportSimId"
            :options="simOptions"
            placeholder="Pick a simulation"
          />
        </a-form-item>
        <a-form-item label="Target directory" required>
          <a-input
            v-model:value="exportTargetDir"
            placeholder="/path/to/output/dir"
          />
        </a-form-item>
        <a-form-item label="Layout">
          <a-radio-group v-model:value="exportLayout">
            <a-radio value="compressed">Compressed</a-radio>
            <a-radio value="live">Live</a-radio>
          </a-radio-group>
        </a-form-item>
        <a-alert
          v-if="exportResultPath"
          type="success"
          show-icon
          :message="`Exported to: ${exportResultPath}`"
        />
      </a-form>
    </a-modal>
  </div>
</template>

<style scoped>
.import-export__config,
.import-export__card {
  margin-top: 16px;
}
</style>
