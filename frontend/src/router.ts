import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'dashboard',
    component: () => import('@/views/DashboardView.vue'),
  },
  {
    path: '/sims/new',
    name: 'sim-new',
    component: () => import('@/views/NewSimulationView.vue'),
  },
  {
    path: '/sims/:id/live',
    name: 'sim-live',
    component: () => import('@/views/SimViewerView.vue'),
    props: (route) => ({ id: route.params.id, mode: 'live' as const }),
  },
  {
    path: '/sims/:id/replay',
    name: 'sim-replay',
    component: () => import('@/views/SimViewerView.vue'),
    props: (route) => ({ id: route.params.id, mode: 'replay' as const }),
  },
  {
    path: '/sims/:id/llm-logs',
    name: 'sim-llm-logs',
    component: () => import('@/views/LlmLogsView.vue'),
    props: true,
  },
  {
    path: '/sims/:id/personas/:name',
    name: 'sim-persona-state',
    component: () => import('@/views/PersonaStateView.vue'),
    props: true,
  },
  {
    path: '/settings/llm-profiles',
    name: 'settings-llm-profiles',
    component: () => import('@/views/LlmProfilesView.vue'),
  },
  {
    path: '/settings/imports',
    name: 'settings-imports',
    component: () => import('@/views/ImportExportView.vue'),
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
