<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import {
  DashboardOutlined,
  ApiOutlined,
  ImportOutlined,
} from '@ant-design/icons-vue'
import { getHealth } from '@/api/meta'

const collapsed = ref<boolean>(false)
const route = useRoute()

// Highlight menu by top-level path segment.
const selectedKeys = computed<string[]>(() => {
  const path = route.path
  if (path.startsWith('/settings/llm-profiles')) return ['llm-profiles']
  if (path.startsWith('/settings/imports')) return ['imports']
  return ['dashboard']
})

// Backend reachability — polled against GET /api/health.
const connectionStatus = ref<'unknown' | 'connected' | 'disconnected'>('unknown')
const statusColor = computed(() => {
  switch (connectionStatus.value) {
    case 'connected':
      return '#52c41a'
    case 'disconnected':
      return '#ff4d4f'
    default:
      return '#bfbfbf'
  }
})

let healthTimer: number | null = null

async function pollHealth(): Promise<void> {
  try {
    await getHealth()
    connectionStatus.value = 'connected'
  } catch {
    connectionStatus.value = 'disconnected'
  }
}

onMounted(() => {
  void pollHealth()
  healthTimer = window.setInterval(() => void pollHealth(), 15000)
})

onUnmounted(() => {
  if (healthTimer != null) window.clearInterval(healthTimer)
})
</script>

<template>
  <a-layout style="min-height: 100vh">
    <a-layout-sider
      v-model:collapsed="collapsed"
      collapsible
      :width="200"
      theme="dark"
    >
      <div
        style="
          height: 48px;
          margin: 8px;
          color: #fff;
          display: flex;
          align-items: center;
          justify-content: center;
          font-weight: 600;
          letter-spacing: 0.5px;
        "
      >
        {{ collapsed ? 'ST' : 'Stanford Town' }}
      </div>
      <a-menu
        theme="dark"
        mode="inline"
        :selected-keys="selectedKeys"
      >
        <a-menu-item key="dashboard">
          <template #icon><DashboardOutlined /></template>
          <RouterLink to="/">Dashboard</RouterLink>
        </a-menu-item>
        <a-menu-item key="llm-profiles">
          <template #icon><ApiOutlined /></template>
          <RouterLink to="/settings/llm-profiles">LLM Profiles</RouterLink>
        </a-menu-item>
        <a-menu-item key="imports">
          <template #icon><ImportOutlined /></template>
          <RouterLink to="/settings/imports">Imports</RouterLink>
        </a-menu-item>
      </a-menu>
    </a-layout-sider>

    <a-layout>
      <a-layout-header
        style="
          background: #fff;
          padding: 0 24px;
          display: flex;
          align-items: center;
          justify-content: space-between;
          box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06);
        "
      >
        <span style="font-size: 18px; font-weight: 600">Stanford Town Vue</span>
        <span style="display: inline-flex; align-items: center; gap: 8px">
          <span
            :style="{
              display: 'inline-block',
              width: '8px',
              height: '8px',
              borderRadius: '50%',
              background: statusColor,
            }"
          />
          <span style="color: #666; font-size: 13px">
            {{ connectionStatus }}
          </span>
        </span>
      </a-layout-header>

      <a-layout-content style="margin: 16px; background: #fff; padding: 16px">
        <RouterView />
      </a-layout-content>
    </a-layout>
  </a-layout>
</template>
