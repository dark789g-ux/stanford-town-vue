<script setup lang="ts">
// SimToolbar - transport controls, speed, zoom, and camera-follow.
// Playback/speed talk to the store directly; zoom + follow are emitted up so
// SimViewerView can route them to the MapCanvas (the Pixi adapter).
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import {
  CaretRightOutlined,
  PauseOutlined,
  StepBackwardOutlined,
  StepForwardOutlined,
  ZoomInOutlined,
  ZoomOutOutlined,
  AimOutlined,
} from '@ant-design/icons-vue'
import { useSimSessionStore } from '@/stores/simSession'

const props = defineProps<{
  focusedAgent: string | null
}>()

const emit = defineEmits<{
  (e: 'zoomIn'): void
  (e: 'zoomOut'): void
  (e: 'zoomReset'): void
  (e: 'update:focusedAgent', name: string | null): void
}>()

const session = useSimSessionStore()
const { isPlaying, maxStep, currentStep, speed, currentFrame } = storeToRefs(session)

const hasSteps = computed(() => maxStep.value > 0)

const speedOptions = [
  { label: '0.5×', value: 0.5 },
  { label: '1×', value: 1 },
  { label: '2×', value: 2 },
  { label: '4×', value: 4 },
]

const speedProxy = computed<number>({
  get: () => speed.value,
  set: (v: number) => session.setSpeed(v),
})

function togglePlay(): void {
  if (isPlaying.value) session.pause()
  else session.play()
}

function stepBack(): void {
  session.stepBack()
}

function stepForward(): void {
  session.stepForward()
}

// Persona names for the "Follow" select, sourced from the current frame.
const agentNames = computed<string[]>(() =>
  (currentFrame.value?.agents ?? []).map((a) => a.name).sort(),
)

const followProxy = computed<string | null>({
  get: () => props.focusedAgent,
  set: (name: string | null) => emit('update:focusedAgent', name ?? null),
})
</script>

<template>
  <div class="sim-toolbar">
    <a-space>
      <a-button :disabled="!hasSteps" @click="stepBack">
        <template #icon><StepBackwardOutlined /></template>
      </a-button>
      <a-button type="primary" :disabled="!hasSteps" @click="togglePlay">
        <template #icon>
          <PauseOutlined v-if="isPlaying" />
          <CaretRightOutlined v-else />
        </template>
        {{ isPlaying ? 'Pause' : 'Play' }}
      </a-button>
      <a-button :disabled="!hasSteps" @click="stepForward">
        <template #icon><StepForwardOutlined /></template>
      </a-button>
    </a-space>

    <a-divider type="vertical" />

    <a-segmented v-model:value="speedProxy" :options="speedOptions" />

    <a-divider type="vertical" />

    <a-space>
      <a-button @click="emit('zoomOut')">
        <template #icon><ZoomOutOutlined /></template>
      </a-button>
      <a-button @click="emit('zoomReset')">Reset</a-button>
      <a-button @click="emit('zoomIn')">
        <template #icon><ZoomInOutlined /></template>
      </a-button>
    </a-space>

    <a-divider type="vertical" />

    <a-select
      v-model:value="followProxy"
      class="sim-toolbar__follow"
      placeholder="Follow agent…"
      allow-clear
      show-search
      :options="agentNames.map((n) => ({ label: n, value: n }))"
    >
      <template #suffixIcon><AimOutlined /></template>
    </a-select>

    <span class="sim-toolbar__step">step {{ currentStep }}</span>
  </div>
</template>

<style scoped>
.sim-toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.sim-toolbar__follow {
  min-width: 180px;
}
.sim-toolbar__step {
  margin-left: auto;
  font-size: 12px;
  color: #888;
  font-variant-numeric: tabular-nums;
}
</style>
