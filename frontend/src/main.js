import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import './styles/tech-theme.css'
import App from './App.vue'
import router from './router.js'
import { isAllowedHost, renderRedirectNotice } from './utils/domainGuard'

// IP 直连拦截：非正式域名(且非本地开发)访问 → 整页提示引导至正式域名，不加载应用。
if (!isAllowedHost()) {
  renderRedirectNotice()
} else {
  const app = createApp(App)
  app.use(ElementPlus)
  app.use(router)
  app.mount('#app')
}
