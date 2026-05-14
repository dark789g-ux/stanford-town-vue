<script setup lang="ts">
// NewSimulationView - form to launch a new simulation.
// Builds a POST /api/sims body and, on success, routes to the live viewer
// (start immediately ON) or back to the dashboard (OFF).
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { message, type FormInstance } from 'ant-design-vue'
import type { Rule } from 'ant-design-vue/es/form'
import { createSim, listForks, type CreateSimBody, type ForkInfo } from '@/api/sims'
import { ApiError } from '@/api/client'
import { useLlmProfilesStore } from '@/stores/llmProfiles'

const router = useRouter()

// --- LLM profiles (frozen contract from Agent B) -------------------------
const llmProfilesStore = useLlmProfilesStore()
const { profiles, loading: profilesLoading } = storeToRefs(llmProfilesStore)
const hasProfiles = computed(() => llmProfilesStore.hasProfiles)

// --- form model ----------------------------------------------------------
interface FormModel {
  sim_code: string
  fork_sim_code: string | undefined
  personas: string[]
  inner_voice: string | undefined
  idea: string
  start_hms: string
  n_round: number
  sec_per_step: number
  llm_profile_id: number | undefined
  start: boolean
}

const form = reactive<FormModel>({
  sim_code: '',
  fork_sim_code: undefined,
  personas: [],
  inner_voice: undefined,
  idea: '',
  start_hms: '07:00:00',
  n_round: 200,
  sec_per_step: 10,
  llm_profile_id: undefined,
  start: true,
})

const formRef = ref<FormInstance>()

const SLUG_RE = /^[A-Za-z0-9_-]+$/
const rules: Record<string, Rule[]> = {
  sim_code: [
    { required: true, message: 'Sim code is required', trigger: 'blur' },
    {
      pattern: SLUG_RE,
      message: 'Only letters, digits, underscores and hyphens',
      trigger: 'blur',
    },
  ],
  fork_sim_code: [
    { required: true, message: 'Select a fork to seed from', trigger: 'change' },
  ],
}

// --- forks ---------------------------------------------------------------
interface NormalizedFork {
  sim_code: string
  source: string
  persona_names: string[]
  step_count: number
}

const forks = ref<NormalizedFork[]>([])
const forksLoading = ref(false)
const forksError = ref<string | null>(null)

function normalizeFork(raw: ForkInfo): NormalizedFork {
  return {
    sim_code: String(raw.sim_code ?? ''),
    source: String(raw.source ?? 'unknown'),
    persona_names: Array.isArray(raw.persona_names) ? (raw.persona_names as string[]) : [],
    step_count: Number(raw.step_count ?? 0),
  }
}

const selectedFork = computed<NormalizedFork | undefined>(() =>
  forks.value.find((f) => f.sim_code === form.fork_sim_code),
)

const personaOptions = computed(() =>
  (selectedFork.value?.persona_names ?? []).map((n) => ({ label: n, value: n })),
)

// `inner_voice` names the persona that receives the whispered `idea` — the
// backend validates it against the *included* personas (the subset, or all
// when none is picked). Mirror that scope here so the dropdown can never
// offer a name the runner would reject.
const innerVoiceOptions = computed(() => {
  const included = form.personas.length
    ? form.personas
    : (selectedFork.value?.persona_names ?? [])
  return included.map((n) => ({ label: n, value: n }))
})

// When the fork changes, default the persona picker to all of the new
// fork's personas (empty would mean "all" to the backend, but explicit is
// clearer for the user).
watch(
  () => form.fork_sim_code,
  () => {
    form.personas = selectedFork.value ? [...selectedFork.value.persona_names] : []
    form.inner_voice = undefined
  },
)

// Drop a stale inner-voice pick when it falls out of the included personas.
watch(
  () => form.personas,
  () => {
    if (
      form.inner_voice &&
      !innerVoiceOptions.value.some((o) => o.value === form.inner_voice)
    ) {
      form.inner_voice = undefined
    }
  },
)

async function loadForks(): Promise<void> {
  forksLoading.value = true
  forksError.value = null
  try {
    const raw = await listForks()
    forks.value = raw.map(normalizeFork).filter((f) => f.sim_code)
    if (!form.fork_sim_code && forks.value.length > 0) {
      form.fork_sim_code = forks.value[0].sim_code
      // watch() fires async; seed personas eagerly too.
      form.personas = [...forks.value[0].persona_names]
    }
  } catch (e) {
    forksError.value = e instanceof ApiError ? e.message : String(e)
  } finally {
    forksLoading.value = false
  }
}

onMounted(() => {
  void loadForks()
  void llmProfilesStore.fetchAll()
})

// --- submit --------------------------------------------------------------
const submitting = ref(false)
const submitError = ref<string | null>(null)
// Server-side error attached to the sim_code field (e.g. a 409 conflict).
// FormInstance has no setFields(), so we drive the field state manually.
const simCodeServerError = ref<string | null>(null)

// Clearing the conflict as soon as the user edits the code.
watch(
  () => form.sim_code,
  () => {
    simCodeServerError.value = null
  },
)

function buildBody(): CreateSimBody {
  const body: CreateSimBody = {
    sim_code: form.sim_code.trim(),
    fork_sim_code: form.fork_sim_code,
    personas: form.personas,
    n_round: form.n_round,
    start_hms: form.start_hms,
    sec_per_step: form.sec_per_step,
    start: form.start,
  }
  if (form.inner_voice) body.inner_voice = form.inner_voice
  if (form.idea.trim()) body.idea = form.idea.trim()
  if (form.llm_profile_id != null) body.llm_profile_id = form.llm_profile_id
  return body
}

async function onSubmit(): Promise<void> {
  submitError.value = null
  try {
    await formRef.value?.validate()
  } catch {
    return // validation errors are shown inline by a-form
  }

  submitting.value = true
  try {
    const sim = await createSim(buildBody())
    message.success(`Simulation "${sim.sim_code}" created`)
    if (form.start) {
      void router.push(`/sims/${sim.id}/live`)
    } else {
      void router.push('/')
    }
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) {
      simCodeServerError.value = 'A simulation with that sim_code already exists'
    } else {
      submitError.value = e instanceof ApiError ? e.message : String(e)
    }
  } finally {
    submitting.value = false
  }
}

function onCancel(): void {
  void router.push('/')
}

function goToProfiles(): void {
  void router.push('/settings/llm-profiles')
}
</script>

<template>
  <div style="max-width: 720px; margin: 0 auto">
    <a-typography-title :level="2">New Simulation</a-typography-title>

    <a-alert
      v-if="forksError"
      type="error"
      :message="`Could not load forks: ${forksError}`"
      show-icon
      style="margin-bottom: 16px"
    />
    <a-alert
      v-if="submitError"
      type="error"
      :message="submitError"
      show-icon
      closable
      style="margin-bottom: 16px"
      @close="submitError = null"
    />

    <a-card>
      <a-form
        ref="formRef"
        :model="form"
        :rules="rules"
        layout="vertical"
        @finish="onSubmit"
      >
        <a-form-item
          label="Sim code"
          name="sim_code"
          :validate-status="simCodeServerError ? 'error' : undefined"
          :help="simCodeServerError || undefined"
        >
          <a-input
            v-model:value="form.sim_code"
            placeholder="my-experiment-1"
            allow-clear
          />
        </a-form-item>

        <a-form-item label="Fork from" name="fork_sim_code">
          <a-select
            v-model:value="form.fork_sim_code"
            :loading="forksLoading"
            placeholder="Select an existing fork to seed from"
            :options="
              forks.map((f) => ({
                label: `${f.sim_code} (${f.source}) — ${f.persona_names.length} personas`,
                value: f.sim_code,
              }))
            "
          />
        </a-form-item>

        <a-form-item label="Personas" name="personas">
          <a-select
            v-model:value="form.personas"
            mode="multiple"
            :options="personaOptions"
            :disabled="!selectedFork"
            placeholder="All personas (default: all selected)"
          />
          <template #extra>
            Subset of the fork's personas to include. Leaving this empty also
            means "all".
          </template>
        </a-form-item>

        <a-form-item label="Idea" name="idea">
          <a-textarea
            v-model:value="form.idea"
            :rows="2"
            placeholder="A whispered idea seeded into the simulation."
          />
          <template #extra>
            The thought broadcast into the run. The persona chosen below
            receives it as an inner voice.
          </template>
        </a-form-item>

        <a-form-item label="Inner voice persona" name="inner_voice">
          <a-select
            v-model:value="form.inner_voice"
            allow-clear
            :disabled="!selectedFork"
            placeholder="First included persona (default)"
            :options="innerVoiceOptions"
          />
          <template #extra>
            Which persona hears the idea above. Leave empty to default to the
            first included persona.
          </template>
        </a-form-item>

        <a-row :gutter="16">
          <a-col :span="8">
            <a-form-item label="Start time" name="start_hms">
              <a-time-picker
                v-model:value="form.start_hms"
                format="HH:mm:ss"
                value-format="HH:mm:ss"
                :allow-clear="false"
                style="width: 100%"
              />
            </a-form-item>
          </a-col>
          <a-col :span="8">
            <a-form-item label="Rounds" name="n_round">
              <a-input-number
                v-model:value="form.n_round"
                :min="1"
                style="width: 100%"
              />
            </a-form-item>
          </a-col>
          <a-col :span="8">
            <a-form-item label="Seconds per step" name="sec_per_step">
              <a-input-number
                v-model:value="form.sec_per_step"
                :min="1"
                style="width: 100%"
              />
            </a-form-item>
          </a-col>
        </a-row>

        <a-form-item label="LLM profile" name="llm_profile_id">
          <a-select
            v-model:value="form.llm_profile_id"
            :loading="profilesLoading"
            allow-clear
            placeholder="Use ambient config (default)"
            :options="
              profiles.map((p) => ({
                label: `${p.name} — ${p.provider}/${p.model}`,
                value: p.id,
              }))
            "
          />
          <template #extra>
            Optional — leave unset to use the backend's ambient LLM config.
          </template>
        </a-form-item>

        <a-alert
          v-if="!profilesLoading && !hasProfiles"
          type="warning"
          show-icon
          style="margin-bottom: 16px"
          message="No LLM profile configured"
          description="The simulation will fall back to the backend's ambient config. You can configure profiles in settings."
        >
          <template #action>
            <a-button size="small" @click="goToProfiles">Configure</a-button>
          </template>
        </a-alert>

        <a-collapse style="margin-bottom: 16px">
          <a-collapse-panel key="llm-override" header="LLM override (advanced)">
            <a-alert
              type="info"
              show-icon
              message="Coming soon"
              description="Per-run model override (placeholder). Use the LLM profile dropdown above as the primary path for M5."
            />
          </a-collapse-panel>
        </a-collapse>

        <a-form-item label="Start immediately" name="start">
          <a-switch v-model:checked="form.start" />
          <a-typography-text type="secondary" style="margin-left: 12px">
            {{
              form.start
                ? 'The run spawns now and you are taken to the live viewer.'
                : 'The simulation is created but not started.'
            }}
          </a-typography-text>
        </a-form-item>

        <a-form-item>
          <a-space>
            <a-button
              type="primary"
              html-type="submit"
              :loading="submitting"
            >
              {{ form.start ? 'Create & Start' : 'Create' }}
            </a-button>
            <a-button :disabled="submitting" @click="onCancel">Cancel</a-button>
          </a-space>
        </a-form-item>
      </a-form>
    </a-card>
  </div>
</template>
