<template>
  <el-container class="app-container">
    <el-header class="app-header">
      <div class="logo">克里珀</div>
      <el-menu mode="horizontal" :default-active="activeRoute" router class="nav-menu">
        <el-menu-item index="/chat">智能问答</el-menu-item>
        <el-menu-item index="/upload">上传文档</el-menu-item>
        <el-menu-item index="/documents">文档管理</el-menu-item>
        <el-menu-item index="/profiling">性能分析</el-menu-item>
        <el-menu-item index="/settings">设置</el-menu-item>
      </el-menu>
    </el-header>
    <el-main class="app-main">
      <router-view />
    </el-main>
  </el-container>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const activeRoute = computed(() => route.path)
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
  background: #ffffff;
  border-bottom: 1px solid #e8e8e8;
}
.logo {
  font-size: 26px;
  font-weight: 700;
  margin-right: 40px;
  color: #1a1a2e;
  letter-spacing: 2px;
}
.nav-menu {
  background: transparent !important;
  border-bottom: none !important;
}
.nav-menu .el-menu-item {
  color: #666666 !important;
  border-bottom: 2px solid transparent !important;
}
.nav-menu .el-menu-item:hover {
  color: #1a1a2e !important;
  background: rgba(77, 107, 254, 0.05) !important;
}
.nav-menu .el-menu-item.is-active {
  color: #1a1a2e !important;
  border-bottom-color: #4d6bfe !important;
}
.app-main {
  padding: 24px;
  max-width: 1200px;
  margin: 0 auto;
  width: 100%;
}
</style>
