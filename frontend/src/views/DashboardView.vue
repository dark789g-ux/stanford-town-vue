<script setup lang="ts">
// DashboardView - lists simulations, entry point for creating new ones.
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { useSimulationsStore } from '@/stores/simulations'
import type { SimStatus } from '@/types/sim'
import SimulationTable from '@/components/sim/SimulationTable.vue'
import OnboardingBanner from '@/components/common/OnboardingBanner.vue'

const router = useRouter()
const store = useSimulationsStore()
const { sims, loading, error } = storeToRefs(store)

type Filter = 'all' | SimStatus
const filter = ref<Filter>('all')

const filterOptions: { label: string; value: Filter }[] = [
  { label: 'All', value: 'all' },
  { label: 'Running', value: 'running' },
  { label: 'Paused', value: 'paused' },
  { label: 'Completed', value: 'completed' },
  { label: 'Failed', value: 'failed' },
  { label: 'Stopped', value: 'stopped' },
]

const filteredSims = computed(() => {
  if (filter.value === 'all') return sims.value
  return sims.value.filter((s) => s.status === filter.value)
})

onMounted(() => {
  void store.fetchAll()
})

function goNew(): void {
  router.push('/sims/new')
}

function goImport(): void {
  router.push('/settings/imports')
}
</script>

<template>
  <div>
    <OnboardingBanner />

    <div
      style="
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 16px;
      "
    >
      <a-typography-title :level="2" style="margin: 0">Simulations</a-typography-title>
      <a-space>
        <a-button @click="goImport">Import</a-button>
        <a-button type="primary" @click="goNew">New Simulation</a-button>
      </a-space>
    </div>

    <a-radio-group
      v-model:value="filter"
      style="margin-bottom: 16px"
      button-style="solid"
    >
      <a-radio-button
        v-for="opt in filterOptions"
        :key="opt.value"
        :value="opt.value"
      >
        {{ opt.label }}
      </a-radio-button>
    </a-radio-group>

    <a-alert
      v-if="error"
      type="error"
      :message="error"
      show-icon
      style="margin-bottom: 16px"
    />

    <a-spin :spinning="loading">
      <a-empty
        v-if="!loading && filteredSims.length === 0"
        description="No simulations yet"
      >
        <a-button type="primary" @click="goNew">New Simulation</a-button>
      </a-empty>
      <SimulationTable v-else :sims="filteredSims" />
    </a-spin>
  </div>
</template>
