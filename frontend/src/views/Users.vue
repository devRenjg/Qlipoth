<template>
  <div class="users-view">
    <h2>用户管理</h2>
    <el-table :data="users" stripe v-loading="loading" empty-text="暂无用户">
      <el-table-column prop="username" label="用户名" />
      <el-table-column prop="role" label="角色" width="120">
        <template #default="{ row }">
          <el-tag :type="roleTag(row.role)">{{ roleLabel(row.role) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="last_seen" label="最后活跃" width="180" />
      <el-table-column label="操作" width="280">
        <template #default="{ row }">
          <el-select
            v-if="row.username !== currentUser?.username"
            :model-value="row.role"
            size="small"
            style="width: 100px; margin-right: 8px;"
            @change="(val) => changeRole(row.id, val)"
          >
            <el-option label="管理员" value="admin" />
            <el-option label="超级用户" value="super" />
            <el-option label="普通用户" value="user" />
          </el-select>
          <el-button size="small" text type="primary" @click="openActivity(row)">行为日志</el-button>
          <el-button
            v-if="row.username !== currentUser?.username"
            size="small"
            type="danger"
            text
            @click="deleteUser(row.id, row.username)"
          >删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-drawer v-model="actVisible" :title="`行为日志 - ${actUser?.username || ''}`" size="46%">
      <div v-loading="actLoading">
        <div v-if="actStats.length" class="act-stats">
          <el-tag v-for="s in actStats" :key="s.action" type="info" class="act-stat">
            {{ s.action }} <b>{{ s.c }}</b>
          </el-tag>
        </div>
        <el-table :data="actItems" stripe size="small" empty-text="暂无行为记录" max-height="640">
          <el-table-column prop="created_at" label="时间" width="160" />
          <el-table-column prop="action" label="功能" width="90">
            <template #default="{ row }"><el-tag size="small" :type="actTag(row.action)">{{ row.action }}</el-tag></template>
          </el-table-column>
          <el-table-column prop="detail" label="详情" show-overflow-tooltip />
        </el-table>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, inject, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import axios from 'axios'

const currentUser = inject('currentUser')
const users = ref([])
const loading = ref(false)

const actVisible = ref(false)
const actLoading = ref(false)
const actUser = ref(null)
const actItems = ref([])
const actStats = ref([])

const api = axios.create({ baseURL: '/api' })

async function openActivity(row) {
  actUser.value = row
  actVisible.value = true
  actLoading.value = true
  actItems.value = []
  actStats.value = []
  try {
    const { data } = await api.get(`/user/${row.id}/activity`)
    actItems.value = data.items || []
    actStats.value = data.stats || []
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || '加载行为日志失败')
  } finally {
    actLoading.value = false
  }
}

function actTag(action) {
  return { '问答': '', '生成清单': 'success', '导出清单': 'warning', '导入文档': 'info', '删除文档': 'danger', '访问页面': '', '查看内容': 'success', '登录': 'info' }[action] || 'info'
}

onMounted(loadUsers)

async function loadUsers() {
  loading.value = true
  try {
    const { data } = await api.get('/user/list')
    users.value = data
  } finally {
    loading.value = false
  }
}

async function changeRole(userId, newRole) {
  try {
    await api.put(`/user/${userId}/role`, { role: newRole })
    ElMessage.success('角色已更新')
    loadUsers()
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || '更新失败')
  }
}

async function deleteUser(userId, username) {
  try {
    await ElMessageBox.confirm(`确定删除用户「${username}」？`, '删除确认', { type: 'warning' })
  } catch { return }
  try {
    await api.delete(`/user/${userId}`)
    ElMessage.success('已删除')
    loadUsers()
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || '删除失败')
  }
}

function roleTag(role) {
  return { admin: 'danger', super: 'warning', user: '' }[role] || ''
}

function roleLabel(role) {
  return { admin: '管理员', super: '超级用户', user: '普通用户' }[role] || role
}
</script>

<style scoped>
.users-view h2 { margin-bottom: 20px; }
.act-stats { margin-bottom: 14px; display: flex; flex-wrap: wrap; gap: 8px; }
.act-stat b { margin-left: 4px; }
</style>
