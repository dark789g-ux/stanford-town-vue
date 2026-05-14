<script setup lang="ts">
// MapCanvas - the ONLY bridge between Vue and the imperative Pixi engine.
// Watches the sim-session store for frames + focus, drives TownRenderer.
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { TownRenderer } from '@/pixi'
import { useSimSessionStore } from '@/stores/simSession'

const props = defineProps<{
  focusedAgent?: string | null
}>()

const emit = defineEmits<{
  (e: 'agentClick', name: string): void
}>()

const containerEl = ref<HTMLDivElement | null>(null)
const canvasEl = ref<HTMLCanvasElement | null>(null)

const session = useSimSessionStore()
const { currentFrame } = storeToRefs(session)

let renderer: TownRenderer | null = null
let resizeObserver: ResizeObserver | null = null

// --- public API for the toolbar (used via a template ref) -----------------
function zoomIn(): void {
  if (!renderer) return
  renderer.setCameraZoom(renderer.getCameraZoom() * 1.25)
}

function zoomOut(): void {
  if (!renderer) return
  renderer.setCameraZoom(renderer.getCameraZoom() / 1.25)
}

function zoomReset(): void {
  renderer?.setCameraZoom(1)
}

defineExpose({ zoomIn, zoomOut, zoomReset })

// --- lifecycle ------------------------------------------------------------
onMounted(async () => {
  if (!canvasEl.value || !containerEl.value) return

  renderer = new TownRenderer({
    canvas: canvasEl.value,
    onAgentClick: (name: string) => emit('agentClick', name),
  })
  await renderer.load()

  // Push the frame that may already be present, then react to changes.
  if (currentFrame.value) renderer.setStep(currentFrame.value)
  if (props.focusedAgent != null) renderer.focusOnAgent(props.focusedAgent)

  const rect = containerEl.value.getBoundingClientRect()
  renderer.resize(rect.width, rect.height)

  resizeObserver = new ResizeObserver((entries) => {
    const entry = entries[0]
    if (!entry || !renderer) return
    const { width, height } = entry.contentRect
    renderer.resize(width, height)
  })
  resizeObserver.observe(containerEl.value)
})

watch(currentFrame, (frame) => {
  if (frame && renderer) renderer.setStep(frame)
})

watch(
  () => props.focusedAgent,
  (name) => {
    renderer?.focusOnAgent(name ?? null)
  },
)

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
  renderer?.destroy()
  renderer = null
})
</script>

<template>
  <div ref="containerEl" class="map-canvas">
    <canvas ref="canvasEl" class="map-canvas__canvas" />
  </div>
</template>

<style scoped>
.map-canvas {
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: #1f1f1f;
}
.map-canvas__canvas {
  display: block;
  width: 100%;
  height: 100%;
}
</style>
