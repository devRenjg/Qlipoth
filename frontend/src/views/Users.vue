<template>
  <div class="users-view">
    <h2>用户管理</h2>
    <el-table :data="users" stripe v-loading="loading" empty-text="暂无用户">
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column prop="username" label="用户名" />
      <el-table-column prop="role" label="角色" width="120">
        <template #default="{ row }">
          <el-tag :type="roleTag(row.role)">{{ roleLabel(row.role) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="last_seen" label="最后活跃" width="180" />
      <el-table-column label="操作" width="200">
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
  </div>
</template>

<script setup>
import { ref, inject, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import axios from 'axios'

const currentUser = inject('currentUser')
const users = ref([])
const loading = ref(false)

const api = axios.create({ baseURL: '/api' })

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
</style>
