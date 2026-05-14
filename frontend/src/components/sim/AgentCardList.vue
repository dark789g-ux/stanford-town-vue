<script setup lang="ts">
// AgentCardList - sidebar list of persona cards for the current frame.
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useSimSessionStore } from '@/stores/simSession'
import AgentCard from '@/components/sim/AgentCard.vue'

const props = defineProps<{
  focusedAgent: string | null
}>()

const emit = defineEmits<{
  (e: 'update:focusedAgent', name: string | null): void
}>()

const session = useSimSessionStore()
const { currentFrame } = storeToRefs(session)

const agents = computed(() => currentFrame.value?.agents ?? [])

function onSelect(name: string): void {
  // Toggle: clicking the focused agent again releases the camera.
  emit('update:focusedAgent', props.focusedAgent === name ? null : name)
}
</script>

<template>
  <div class="agent-card-list">
    <div class="agent-card-list__title">
      Agents <a-badge :count="agents.length" :number-style="{ backgroundColor: '#999' }" />
    </div>

    <a-empty
      v-if="agents.length === 0"
      :image="undefined"
      description="No agents in this frame"
    />

    <div v-else class="agent-card-list__items">
      <AgentCard
        v-for="agent in agents"
        :key="agent.name"
        :agent="agent"
        :focused="props.focusedAgent === agent.name"
        @select="onSelect"
      />
    </div>
  </div>
</template>

<style scoped>
.agent-card-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  height: 100%;
  overflow-y: auto;
  padding: 4px;
}
.agent-card-list__title {
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 8px;
}
.agent-card-list__items {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
</style>
