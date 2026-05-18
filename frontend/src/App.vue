<template>
  <el-container class="app-container">
    <el-header class="app-header">
      <div class="logo">Qlipoth</div>
      <el-menu mode="horizontal" :default-active="activeRoute" router>
        <el-menu-item index="/chat">智能问答</el-menu-item>
        <el-menu-item index="/upload">上传文档</el-menu-item>
        <el-menu-item index="/documents">文档管理</el-menu-item>
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
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }
.app-container { min-height: 100vh; }
.app-header {
  display: flex;
  align-items: center;
  border-bottom: 1px solid #e4e7ed;
  padding: 0 20px;
}
.logo {
  font-size: 22px;
  font-weight: 700;
  margin-right: 40px;
  color: #409eff;
}
.app-main { padding: 24px; max-width: 1200px; margin: 0 auto; width: 100%; }
</style>
