<script setup lang="ts">
// StepSlider - scrub through simulation steps; reads/writes the sim-session store.
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useSimSessionStore } from '@/stores/simSession'

const session = useSimSessionStore()
const { currentStep, maxStep, currentFrame } = storeToRefs(session)

// Two-way bridge: the slider edits a local proxy that seeks the store.
const sliderValue = computed<number>({
  get: () => currentStep.value,
  set: (v: number) => session.seek(v),
})

const currTime = computed(() => currentFrame.value?.curr_time ?? '—')
</script>

<template>
  <div class="step-slider">
    <a-slider
      v-model:value="sliderValue"
      :min="0"
      :max="maxStep"
      :disabled="maxStep === 0"
      class="step-slider__slider"
    />
    <div class="step-slider__meta">
      <span>{{ currentStep }} / {{ maxStep }}</span>
      <span class="step-slider__time">{{ currTime }}</span>
    </div>
  </div>
</template>

<style scoped>
.step-slider {
  display: flex;
  align-items: center;
  gap: 16px;
  width: 100%;
}
.step-slider__slider {
  flex: 1;
}
.step-slider__meta {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  font-size: 12px;
  color: #666;
  white-space: nowrap;
  min-width: 120px;
}
.step-slider__time {
  font-variant-numeric: tabular-nums;
}
</style>
