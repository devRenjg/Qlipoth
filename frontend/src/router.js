import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', redirect: '/chat' },
  { path: '/chat', component: () => import('./views/Chat.vue') },
  { path: '/checklist', component: () => import('./views/Checklist.vue') },
  { path: '/battlemap', component: () => import('./views/BattleMap.vue') },
  { path: '/live-calendar', component: () => import('./views/LiveCalendar.vue') },
  { path: '/maoer712', component: () => import('./views/Maoer712.vue') },
  { path: '/upload', component: () => import('./views/Upload.vue') },
  { path: '/documents', component: () => import('./views/Documents.vue') },
  { path: '/profiling', component: () => import('./views/Profiling.vue') },
  { path: '/settings', component: () => import('./views/Settings.vue') },
  { path: '/users', component: () => import('./views/Users.vue') },
  { path: '/openspec', component: () => import('./views/OpenSpec.vue') },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
