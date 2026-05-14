<script setup lang="ts">
// LlmProfilesView - manage LLM provider/model profiles.
import { onMounted, reactive, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { message } from 'ant-design-vue'
import type { Rule } from 'ant-design-vue/es/form'
import type { FormInstance } from 'ant-design-vue'
import { useLlmProfilesStore } from '@/stores/llmProfiles'
import { ApiError } from '@/api/client'
import type {
  LlmProfileOut,
  LlmProfileCreate,
  LlmProfileUpdate,
  LlmProfileTestResult,
  LlmProvider,
} from '@/api/llmProfiles'

const store = useLlmProfilesStore()
const { profiles, loading, error } = storeToRefs(store)

onMounted(() => {
  void store.fetchAll()
})

// ---- table -----------------------------------------------------------------

const columns = [
  { title: 'Name', dataIndex: 'name', key: 'name' },
  { title: 'Provider', key: 'provider' },
  { title: 'Model', dataIndex: 'model', key: 'model' },
  { title: 'Base URL', key: 'base_url' },
  { title: 'Max Tokens', dataIndex: 'max_tokens', key: 'max_tokens' },
  { title: 'Temperature', dataIndex: 'temperature', key: 'temperature' },
  { title: 'Created', dataIndex: 'created_at', key: 'created_at' },
  { title: 'Actions', key: 'actions' },
]

type TagColor = 'green' | 'blue' | 'purple' | 'default'
const PROVIDER_COLOR: Record<string, TagColor> = {
  openai: 'green',
  deepseek: 'blue',
  anthropic: 'purple',
}
function providerColor(provider: string): TagColor {
  return PROVIDER_COLOR[provider] ?? 'default'
}

// ---- test ------------------------------------------------------------------

const testing = ref<Set<number>>(new Set())
const testResults = reactive<Record<number, LlmProfileTestResult>>({})

async function runTest(id: number): Promise<void> {
  testing.value.add(id)
  testing.value = new Set(testing.value)
  try {
    const result = await store.test(id)
    testResults[id] = result
    if (result.ok) {
      message.success(`Test passed (${result.elapsed_ms} ms)`)
    } else {
      message.error(`Test failed: ${result.error ?? 'unknown error'}`)
    }
  } catch (e) {
    message.error(
      `Test failed: ${e instanceof Error ? e.message : String(e)}`,
    )
  } finally {
    testing.value.delete(id)
    testing.value = new Set(testing.value)
  }
}

// ---- delete ----------------------------------------------------------------

async function remove(id: number): Promise<void> {
  try {
    await store.remove(id)
    delete testResults[id]
    message.success('Profile deleted')
  } catch (e) {
    message.error(
      `Delete failed: ${e instanceof Error ? e.message : String(e)}`,
    )
  }
}

// ---- create / edit modal ---------------------------------------------------

interface ProfileForm {
  name: string
  provider: LlmProvider
  model: string
  api_key: string
  base_url: string
  max_tokens: number
  temperature: number
}

function emptyForm(): ProfileForm {
  return {
    name: '',
    provider: 'openai',
    model: '',
    api_key: '',
    base_url: '',
    max_tokens: 4096,
    temperature: 0.5,
  }
}

const modalOpen = ref(false)
const editingId = ref<number | null>(null)
const submitting = ref(false)
const modalError = ref<string | null>(null)
const formRef = ref<FormInstance>()
const form = reactive<ProfileForm>(emptyForm())

const PROVIDER_OPTIONS: { label: string; value: LlmProvider }[] = [
  { label: 'OpenAI', value: 'openai' },
  { label: 'DeepSeek', value: 'deepseek' },
  { label: 'Anthropic', value: 'anthropic' },
]

const rules: Record<string, Rule[]> = {
  name: [{ required: true, message: 'Name is required', trigger: 'blur' }],
  provider: [
    { required: true, message: 'Provider is required', trigger: 'change' },
  ],
  model: [{ required: true, message: 'Model is required', trigger: 'blur' }],
  api_key: [
    {
      validator: (_rule: Rule, value: string): Promise<void> => {
        if (editingId.value === null && !value) {
          return Promise.reject('API key is required')
        }
        return Promise.resolve()
      },
      trigger: 'blur',
    },
  ],
}

function resetForm(): void {
  Object.assign(form, emptyForm())
  formRef.value?.clearValidate()
}

function openCreate(): void {
  editingId.value = null
  modalError.value = null
  resetForm()
  modalOpen.value = true
}

function openEdit(record: LlmProfileOut): void {
  editingId.value = record.id
  modalError.value = null
  Object.assign(form, {
    name: record.name,
    provider: (record.provider as LlmProvider) ?? 'openai',
    model: record.model,
    api_key: '',
    base_url: record.base_url ?? '',
    max_tokens: record.max_tokens,
    temperature: record.temperature,
  })
  formRef.value?.clearValidate()
  modalOpen.value = true
}

function closeModal(): void {
  modalOpen.value = false
}

async function submitForm(): Promise<void> {
  try {
    await formRef.value?.validate()
  } catch {
    return
  }

  submitting.value = true
  modalError.value = null

  const base_url = form.base_url.trim() === '' ? null : form.base_url.trim()

  try {
    if (editingId.value === null) {
      const body: LlmProfileCreate = {
        name: form.name.trim(),
        provider: form.provider,
        model: form.model.trim(),
        api_key: form.api_key,
        base_url,
        max_tokens: form.max_tokens,
        temperature: form.temperature,
      }
      await store.create(body)
      message.success('Profile created')
    } else {
      const body: LlmProfileUpdate = {
        name: form.name.trim(),
        provider: form.provider,
        model: form.model.trim(),
        base_url,
        max_tokens: form.max_tokens,
        temperature: form.temperature,
      }
      // Only send api_key when the user actually typed a new one;
      // the backend treats an omitted api_key as "unchanged".
      if (form.api_key.trim() !== '') {
        body.api_key = form.api_key
      }
      await store.update(editingId.value, body)
      message.success('Profile updated')
    }
    modalOpen.value = false
  } catch (e) {
    if (
      editingId.value === null &&
      e instanceof ApiError &&
      e.status === 409
    ) {
      modalError.value = null
      nameStatus.value = 'error'
      nameHelp.value = 'A profile with that name already exists'
    } else {
      modalError.value = e instanceof Error ? e.message : String(e)
    }
  } finally {
    submitting.value = false
  }
}

// Manual name field status for the 409 duplicate-name case.
const nameStatus = ref<'' | 'error'>('')
const nameHelp = ref<string>('')
function clearNameError(): void {
  nameStatus.value = ''
  nameHelp.value = ''
}
</script>

<template>
  <div>
    <div
      style="
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 16px;
      "
    >
      <a-typography-title :level="2" style="margin: 0">
        LLM Profiles
      </a-typography-title>
      <a-button type="primary" @click="openCreate">New Profile</a-button>
    </div>

    <a-alert
      v-if="error"
      type="error"
      :message="error"
      show-icon
      closable
      style="margin-bottom: 16px"
    />

    <a-spin :spinning="loading">
      <a-empty
        v-if="!loading && profiles.length === 0"
        description="No LLM profiles yet"
      >
        <a-button type="primary" @click="openCreate">
          Create your first profile
        </a-button>
      </a-empty>

      <a-table
        v-else
        :columns="columns"
        :data-source="profiles"
        :row-key="(r: LlmProfileOut) => r.id"
        :pagination="{ pageSize: 10, hideOnSinglePage: true }"
        size="middle"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'provider'">
            <a-tag :color="providerColor((record as LlmProfileOut).provider)">
              {{ (record as LlmProfileOut).provider }}
            </a-tag>
          </template>

          <template v-else-if="column.key === 'base_url'">
            {{ (record as LlmProfileOut).base_url ?? '—' }}
          </template>

          <template v-else-if="column.key === 'actions'">
            <a-space wrap>
              <a-button
                size="small"
                :loading="testing.has((record as LlmProfileOut).id)"
                @click="runTest((record as LlmProfileOut).id)"
              >
                Test
              </a-button>
              <a-button
                size="small"
                type="link"
                @click="openEdit(record as LlmProfileOut)"
              >
                Edit
              </a-button>
              <a-popconfirm
                title="Delete this profile?"
                ok-text="Delete"
                cancel-text="Cancel"
                @confirm="remove((record as LlmProfileOut).id)"
              >
                <a-button size="small" type="link" danger>Delete</a-button>
              </a-popconfirm>
            </a-space>

            <div
              v-if="testResults[(record as LlmProfileOut).id]"
              style="margin-top: 8px"
            >
              <a-typography-text
                v-if="testResults[(record as LlmProfileOut).id].ok"
                type="success"
              >
                ✓
                {{
                  testResults[(record as LlmProfileOut).id].sample_response ??
                  'OK'
                }}
                ({{ testResults[(record as LlmProfileOut).id].elapsed_ms }} ms)
              </a-typography-text>
              <a-typography-text v-else type="danger">
                ✗
                {{
                  testResults[(record as LlmProfileOut).id].error ??
                  'Test failed'
                }}
              </a-typography-text>
            </div>
          </template>
        </template>
      </a-table>
    </a-spin>

    <a-modal
      v-model:open="modalOpen"
      :title="editingId === null ? 'New LLM Profile' : 'Edit LLM Profile'"
      :confirm-loading="submitting"
      ok-text="Save"
      @ok="submitForm"
      @cancel="closeModal"
    >
      <a-alert
        v-if="modalError"
        type="error"
        :message="modalError"
        show-icon
        style="margin-bottom: 16px"
      />

      <a-form
        ref="formRef"
        :model="form"
        :rules="rules"
        layout="vertical"
      >
        <a-form-item
          label="Name"
          name="name"
          :validate-status="nameStatus || undefined"
          :help="nameHelp || undefined"
        >
          <a-input
            v-model:value="form.name"
            placeholder="My GPT-4o profile"
            @change="clearNameError"
          />
        </a-form-item>

        <a-form-item label="Provider" name="provider">
          <a-select v-model:value="form.provider" :options="PROVIDER_OPTIONS" />
        </a-form-item>

        <a-form-item
          label="Model"
          name="model"
          extra="e.g. gpt-4o-mini, deepseek-chat, claude-sonnet-4-6"
        >
          <a-input v-model:value="form.model" placeholder="gpt-4o-mini" />
        </a-form-item>

        <a-form-item
          label="API Key"
          name="api_key"
          :extra="
            editingId === null
              ? undefined
              : 'Leave blank to keep the current key'
          "
        >
          <a-input-password
            v-model:value="form.api_key"
            :placeholder="
              editingId === null
                ? 'sk-...'
                : 'leave blank to keep current'
            "
          />
        </a-form-item>

        <a-form-item
          label="Base URL"
          name="base_url"
          extra="Override the provider's default endpoint"
        >
          <a-input
            v-model:value="form.base_url"
            placeholder="https://api.openai.com/v1"
          />
        </a-form-item>

        <a-form-item label="Max Tokens" name="max_tokens">
          <a-input-number
            v-model:value="form.max_tokens"
            :min="1"
            style="width: 100%"
          />
        </a-form-item>

        <a-form-item label="Temperature" name="temperature">
          <a-input-number
            v-model:value="form.temperature"
            :min="0"
            :max="2"
            :step="0.1"
            style="width: 100%"
          />
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>
