<script setup lang="ts">
// AgentCard - one persona's current state; clicking focuses the camera.
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { AgentFrame } from '@/types/viewer'

const props = defineProps<{
  agent: AgentFrame
  focused?: boolean
}>()

const emit = defineEmits<{
  (e: 'select', name: string): void
}>()

const route = useRoute()
const router = useRouter()

const simId = computed(() => String(route.params.id ?? ''))

function onClick(): void {
  emit('select', props.agent.name)
}

function openDetails(): void {
  void router.push(
    `/sims/${simId.value}/personas/${encodeURIComponent(props.agent.name)}`,
  )
}
</script>

<template>
  <a-card
    size="small"
    hoverable
    class="agent-card"
    :class="{ 'agent-card--focused': props.focused }"
    @click="onClick"
  >
    <div class="agent-card__head">
      <span class="agent-card__emoji">{{ props.agent.pronunciatio ?? '·' }}</span>
      <span class="agent-card__name">{{ props.agent.name }}</span>
      <a-tag class="agent-card__tile">{{ props.agent.x }}, {{ props.agent.y }}</a-tag>
      <a-button size="small" type="link" @click.stop="openDetails">
        Details
      </a-button>
    </div>
    <div class="agent-card__desc">
      {{ props.agent.description ?? 'idle' }}
    </div>
  </a-card>
</template>

<style scoped>
.agent-card {
  cursor: pointer;
  transition: border-color 0.15s ease;
}
.agent-card--focused {
  border-color: #1677ff;
  box-shadow: 0 0 0 2px rgba(22, 119, 255, 0.15);
}
.agent-card__head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.agent-card__emoji {
  font-size: 18px;
  line-height: 1;
}
.agent-card__name {
  font-weight: 600;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.agent-card__tile {
  margin: 0;
  font-variant-numeric: tabular-nums;
}
.agent-card__desc {
  margin-top: 6px;
  font-size: 12px;
  color: #666;
}
</style>
