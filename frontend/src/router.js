import { createRouter, createWebHistory } from 'vue-router'
import { trackActivity } from './api'

const routes = [
  { path: '/', redirect: '/chat' },
  { path: '/chat', component: () => import('./views/Chat.vue'), meta: { title: '智能问答' } },
  { path: '/checklist', component: () => import('./views/Checklist.vue'), meta: { title: '保障清单' } },
  { path: '/battlemap', component: () => import('./views/BattleMap.vue'), meta: { title: '作战地图' } },
  { path: '/live-calendar', component: () => import('./views/LiveCalendar.vue'), meta: { title: '直播日历' } },
  { path: '/case-analysis', component: () => import('./views/CaseAnalysis.vue'), meta: { title: '案例分析' } },
  { path: '/maoer712', component: () => import('./views/Maoer712.vue'), meta: { title: '猫耳712' } },
  { path: '/upload', component: () => import('./views/Upload.vue'), meta: { title: '上传文档' } },
  { path: '/documents', component: () => import('./views/Documents.vue'), meta: { title: '文档管理' } },
  { path: '/profiling', component: () => import('./views/Profiling.vue'), meta: { title: '性能分析' } },
  { path: '/settings', component: () => import('./views/Settings.vue'), meta: { title: '系统设置' } },
  { path: '/users', component: () => import('./views/Users.vue'), meta: { title: '用户管理' } },
  { path: '/openspec', component: () => import('./views/OpenSpec.vue'), meta: { title: 'OpenSpec' } },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 页面访问埋点：detail = 中文页面名 + URL
router.afterEach((to) => {
  const name = to.meta?.title || to.path
  trackActivity('访问页面', `${name} ${to.path}`)
})

export default router
