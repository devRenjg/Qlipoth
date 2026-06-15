<template>
  <div v-if="ready && !user" class="login-page">
    <div class="login-card">
      <div class="login-logo">克里珀</div>
      <p class="login-desc">大型直播活动保障知识库</p>
      <el-input
        v-model="username"
        placeholder="用户名"
        size="large"
        maxlength="20"
        autocomplete="off"
        class="login-input"
      />
      <el-input
        v-model="password"
        placeholder="密码"
        size="large"
        type="password"
        show-password
        autocomplete="new-password"
        class="login-input"
        @keyup.enter="isRegisterMode ? handleRegister() : handleLogin()"
      />
      <el-input
        v-if="isRegisterMode"
        v-model="confirmPassword"
        placeholder="确认密码"
        size="large"
        type="password"
        show-password
        class="login-input"
        @keyup.enter="handleRegister"
      />
      <el-button
        type="primary"
        size="large"
        :loading="submitting"
        :disabled="!username.trim() || !password"
        @click="isRegisterMode ? handleRegister() : handleLogin()"
        class="login-btn"
      >{{ isRegisterMode ? '注册' : '登录' }}</el-button>
      <div class="login-switch">
        <span v-if="!isRegisterMode">没有账号？<a href="#" @click.prevent="isRegisterMode = true">注册</a></span>
        <span v-else>已有账号？<a href="#" @click.prevent="isRegisterMode = false">登录</a></span>
      </div>
      <p v-if="loginError" class="login-error">{{ loginError }}</p>
      <p v-if="isRegisterMode" class="login-hint">密码要求：8位以上，含大小写字母、数字和特殊字符</p>
    </div>
  </div>
  <el-container class="app-container" v-if="ready && user">
    <el-header class="app-header">
      <div class="logo"><span class="logo-mark">◆</span>克里珀</div>
      <el-menu mode="horizontal" :default-active="activeRoute" router class="nav-menu">
        <el-menu-item index="/chat">智能问答</el-menu-item>
        <el-menu-item index="/checklist">保障清单</el-menu-item>
        <el-menu-item index="/battlemap">作战地图</el-menu-item>
        <el-menu-item index="/upload" v-if="user.role !== 'user'">上传文档</el-menu-item>
        <el-menu-item index="/documents" v-if="user.role !== 'user'">文档管理</el-menu-item>
        <el-menu-item index="/profiling" v-if="user.role === 'admin'">性能分析</el-menu-item>
        <el-menu-item index="/settings" v-if="user.role === 'admin'">设置</el-menu-item>
        <el-menu-item index="/users" v-if="user.role === 'admin'">用户管理</el-menu-item>
        <el-menu-item index="/openspec" v-if="user.role === 'admin'">OpenSpec</el-menu-item>
      </el-menu>
      <div class="user-info">
        <span class="username">{{ user.username }}</span>
        <el-button size="small" text type="info" @click="handleLogout">退出</el-button>
      </div>
    </el-header>
    <el-main class="app-main" :class="{ 'full-width': isChatPage }">
      <router-view />
    </el-main>
  </el-container>
</template>

<script setup>
import { ref, computed, onMounted, provide } from 'vue'
import { useRoute } from 'vue-router'
import { getCurrentUser, registerUser, loginUser, logoutUser } from './api/index.js'
import { ElMessage } from 'element-plus'

const route = useRoute()
const activeRoute = computed(() => route.path)
const isChatPage = computed(() => route.path === '/chat')
const user = ref(null)
const ready = ref(false)
const username = ref('')
const password = ref('')
const confirmPassword = ref('')
const isRegisterMode = ref(false)
const submitting = ref(false)
const loginError = ref('')

provide('currentUser', user)

onMounted(async () => {
  try {
    const { data } = await getCurrentUser()
    user.value = data.user
  } catch {}
  ready.value = true
})

async function handleLogin() {
  if (!username.value.trim() || !password.value) return
  submitting.value = true
  loginError.value = ''
  try {
    const { data } = await loginUser(username.value.trim(), password.value)
    user.value = data.user
  } catch (err) {
    loginError.value = err.response?.data?.detail || '登录失败'
  } finally {
    submitting.value = false
  }
}

async function handleRegister() {
  if (!username.value.trim() || !password.value) return
  if (password.value !== confirmPassword.value) {
    loginError.value = '两次密码不一致'
    return
  }
  submitting.value = true
  loginError.value = ''
  try {
    const { data } = await registerUser(username.value.trim(), password.value)
    user.value = data.user
    ElMessage.success('注册成功')
  } catch (err) {
    loginError.value = err.response?.data?.detail || '注册失败'
  } finally {
    submitting.value = false
  }
}

async function handleLogout() {
  try {
    await logoutUser()
  } catch {}
  user.value = null
  username.value = ''
  password.value = ''
  confirmPassword.value = ''
}
</script>

<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: #ffffff;
  min-height: 100vh;
}
.app-container { min-height: 100vh; }
.app-header {
  display: flex;
  align-items: center;
  padding: 0 24px;
  background: linear-gradient(100deg, #1c2f5e 0%, #25438a 55%, #2b4e9e 100%);
  border-bottom: 1px solid #3a5499;
  box-shadow: 0 2px 12px rgba(20, 35, 80, 0.3);
  position: relative;
}
.app-header::after {
  content: ""; position: absolute; left: 0; right: 0; bottom: -1px; height: 2px;
  background: linear-gradient(90deg, transparent, #2f80ff 40%, #36d1c4 60%, transparent);
  opacity: 0.7;
}
.logo {
  display: flex; align-items: center; gap: 8px;
  font-size: 25px;
  font-weight: 700;
  margin-right: 44px;
  color: #ffffff;
  letter-spacing: 3px;
}
.logo-mark {
  color: #36d1c4; font-size: 16px;
  text-shadow: 0 0 8px rgba(54, 209, 196, 0.8);
}
.nav-menu {
  background: transparent !important;
  border-bottom: none !important;
  flex: 1;
}
.nav-menu .el-menu-item {
  color: rgba(255, 255, 255, 0.65) !important;
  border-bottom: 2px solid transparent !important;
  font-size: 15px;
  transition: color .2s, background .2s;
}
.nav-menu .el-menu-item:hover {
  color: #ffffff !important;
  background: rgba(90, 160, 255, 0.2) !important;
}
.nav-menu .el-menu-item.is-active {
  color: #ffffff !important;
  font-weight: 600;
  border-bottom-color: #5ab0ff !important;
  background: linear-gradient(180deg, rgba(90, 176, 255, 0.28), rgba(90, 176, 255, 0.12)) !important;
  box-shadow: inset 0 -2px 10px rgba(90, 176, 255, 0.35);
  text-shadow: 0 0 12px rgba(120, 200, 255, 0.9);
}
.user-info {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-left: 16px;
}
.username { font-size: 14px; color: rgba(255, 255, 255, 0.9); }
.username.anonymous { color: rgba(255, 255, 255, 0.5); }
.user-info .el-button { color: rgba(255, 255, 255, 0.7) !important; }
.user-info .el-button:hover { color: #ffffff !important; }
.app-main {
  padding: 24px;
  max-width: 1200px;
  margin: 0 auto;
  width: 100%;
}
.app-main.full-width {
  max-width: none;
  padding: 0;
}
.login-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  background: #f5f7fa;
}
.login-card {
  background: #fff;
  padding: 48px 40px;
  border-radius: 12px;
  box-shadow: 0 2px 12px rgba(0,0,0,0.08);
  width: 380px;
  text-align: center;
}
.login-logo {
  font-size: 32px;
  font-weight: 700;
  color: #1a1a2e;
  letter-spacing: 2px;
  margin-bottom: 8px;
}
.login-desc {
  color: #909399;
  font-size: 14px;
  margin-bottom: 32px;
}
.login-btn {
  width: 100%;
  margin-top: 16px;
}
.login-input { margin-bottom: 12px; }
.login-switch { margin-top: 16px; font-size: 13px; color: #666; }
.login-switch a { color: #4d6bfe; text-decoration: none; }
.login-switch a:hover { text-decoration: underline; }
.login-error {
  color: #f56c6c;
  font-size: 13px;
  margin-top: 12px;
}
.login-hint {
  color: #909399;
  font-size: 12px;
  margin-top: 8px;
}
</style>
