<script setup lang="ts">
// OnboardingBanner - first-run hint shown when no LLM profiles exist yet.
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { useLlmProfilesStore } from '@/stores/llmProfiles'

const router = useRouter()
const store = useLlmProfilesStore()
const { hasProfiles, profiles, loading } = storeToRefs(store)

onMounted(() => {
  // Ensure hasProfiles is accurate; guard against double-fetch.
  if (!loading.value && profiles.value.length === 0) {
    void store.fetchAll()
  }
})

function goToProfiles(): void {
  router.push('/settings/llm-profiles')
}
</script>

<template>
  <a-alert
    v-if="!hasProfiles"
    type="info"
    banner
    show-icon
    style="margin-bottom: 16px"
    message="Welcome — to run live simulations, first add an LLM profile."
  >
    <template #action>
      <a-button size="small" type="primary" @click="goToProfiles">
        Add LLM Profile
      </a-button>
    </template>
  </a-alert>
</template>
