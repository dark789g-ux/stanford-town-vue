<script setup lang="ts">
// SimViewerView - Pixi canvas + controls for live/replay viewing.
// Owns the `focusedAgent` state and bridges the toolbar's zoom emits to the
// MapCanvas template ref. All sim data flows through the sim-session store.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useSimSessionStore } from '@/stores/simSession'
import type { ViewerMode } from '@/types/viewer'
import MapCanvas from '@/components/pixi/MapCanvas.vue'
import AgentCardList from '@/components/sim/AgentCardList.vue'
import SimToolbar from '@/components/sim/SimToolbar.vue'
import StepSlider from '@/components/sim/StepSlider.vue'

const props = defineProps<{
  id: string | number
  mode: ViewerMode
}>()

const session = useSimSessionStore()
const { sim, status, connected, error, currentFrame, mode, maxStep } =
  storeToRefs(session)

// Camera focus is view-local: shared by the toolbar select, the agent list,
// and the canvas adapter.
const focusedAgent = ref<string | null>(null)

// Bridge: toolbar zoom emits -> MapCanvas exposed methods.
const mapCanvasRef = ref<InstanceType<typeof MapCanvas> | null>(null)

const simId = computed(() => Number(props.id))

const headerTime = computed(
  () => currentFrame.value?.curr_time ?? sim.value?.curr_time_iso ?? '—',
)

const statusColor = computed(() => {
  switch (status.value) {
    case 'running':
      return 'green'
    case 'paused':
      return 'orange'
    case 'completed':
      return 'blue'
    case 'failed':
    case 'interrupted':
      return 'red'
    default:
      return 'default'
  }
})

const loading = computed(() => !sim.value && !error.value)

onMounted(() => {
  void session.loadSim(simId.value, props.mode)
})

onBeforeUnmount(() => {
  session.disconnect()
})

function onAgentClick(name: string): void {
  focusedAgent.value = focusedAgent.value === name ? null : name
}

function zoomIn(): void {
  mapCanvasRef.value?.zoomIn()
}
function zoomOut(): void {
  mapCanvasRef.value?.zoomOut()
}
function zoomReset(): void {
  mapCanvasRef.value?.zoomReset()
}
</script>

<template>
  <div class="sim-viewer">
    <a-result
      v-if="error"
      status="error"
      title="Failed to load simulation"
      :sub-title="error"
    />

    <a-spin v-else-if="loading" tip="Loading simulation…" class="sim-viewer__spin">
      <div style="height: 200px" />
    </a-spin>

    <template v-else>
      <!-- header strip -->
      <div class="sim-viewer__header">
        <div class="sim-viewer__title">
          <a-typography-title :level="3" style="margin: 0">
            {{ sim?.sim_code ?? `Sim #${simId}` }}
          </a-typography-title>
          <a-tag :color="statusColor">{{ status }}</a-tag>
          <a-tag>{{ mode }}</a-tag>
        </div>
        <div class="sim-viewer__meta">
          <span class="sim-viewer__time">{{ headerTime }}</span>
          <a-tag v-if="mode === 'live'" :color="connected ? 'green' : 'red'">
            {{ connected ? 'connected' : 'disconnected' }}
          </a-tag>
        </div>
      </div>

      <!-- main area: canvas + agent sidebar -->
      <div class="sim-viewer__body">
        <div class="sim-viewer__canvas">
          <MapCanvas
            ref="mapCanvasRef"
            :focused-agent="focusedAgent"
            @agent-click="onAgentClick"
          />
        </div>
        <div class="sim-viewer__sidebar">
          <AgentCardList v-model:focused-agent="focusedAgent" />
        </div>
      </div>

      <!-- bottom bar: toolbar + slider -->
      <div class="sim-viewer__footer">
        <SimToolbar
          v-model:focused-agent="focusedAgent"
          @zoom-in="zoomIn"
          @zoom-out="zoomOut"
          @zoom-reset="zoomReset"
        />
        <StepSlider />
      </div>

      <a-alert
        v-if="mode === 'live' && !connected"
        type="warning"
        message="Live stream not connected"
        show-icon
        style="margin-top: 8px"
      />
      <div v-if="maxStep === 0" class="sim-viewer__hint">
        No steps available yet.
      </div>
    </template>
  </div>
</template>

<style scoped>
.sim-viewer {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 64px - 32px);
  gap: 12px;
}
.sim-viewer__spin {
  margin: 48px auto;
}
.sim-viewer__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.sim-viewer__title,
.sim-viewer__meta {
  display: flex;
  align-items: center;
  gap: 8px;
}
.sim-viewer__time {
  font-variant-numeric: tabular-nums;
  color: #666;
}
.sim-viewer__body {
  flex: 1;
  display: flex;
  gap: 12px;
  min-height: 0;
}
.sim-viewer__canvas {
  flex: 1;
  min-width: 0;
  border: 1px solid #f0f0f0;
  border-radius: 6px;
  overflow: hidden;
}
.sim-viewer__sidebar {
  width: 280px;
  flex-shrink: 0;
  border: 1px solid #f0f0f0;
  border-radius: 6px;
  padding: 8px;
  overflow: hidden;
}
.sim-viewer__footer {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 8px 4px 0;
}
.sim-viewer__hint {
  font-size: 12px;
  color: #999;
}
</style>
