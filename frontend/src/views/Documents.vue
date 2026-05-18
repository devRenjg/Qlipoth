<template>
  <div class="documents-view">
    <div class="header">
      <h2>文档管理</h2>
      <el-input v-model="filterText" placeholder="筛选文档..." style="width: 240px" clearable />
    </div>

    <el-table :data="filteredDocs" stripe v-loading="loading" empty-text="暂无文档">
      <el-table-column prop="original_name" label="文件名" />
      <el-table-column prop="file_type" label="类型" width="80" />
      <el-table-column prop="file_size" label="大小" width="100">
        <template #default="{ row }">{{ formatSize(row.file_size) }}</template>
      </el-table-column>
      <el-table-column prop="uploaded_at" label="上传时间" width="180" />
      <el-table-column label="操作" width="160">
        <template #default="{ row }">
          <el-button size="small" @click="viewDoc(row)">查看</el-button>
          <el-button size="small" type="danger" @click="confirmDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" :title="currentDoc?.original_name" width="70%">
      <pre class="doc-content">{{ currentDoc?.content }}</pre>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getDocuments, getDocument, deleteDocument } from '../api/index.js'

const documents = ref([])
const loading = ref(false)
const filterText = ref('')
const dialogVisible = ref(false)
const currentDoc = ref(null)

const filteredDocs = computed(() => {
  if (!filterText.value) return documents.value
  const q = filterText.value.toLowerCase()
  return documents.value.filter(d => d.original_name.toLowerCase().includes(q))
})

onMounted(loadDocs)

async function loadDocs() {
  loading.value = true
  try {
    const { data } = await getDocuments()
    documents.value = data
  } finally {
    loading.value = false
  }
}

async function viewDoc(row) {
  const { data } = await getDocument(row.id)
  currentDoc.value = data
  dialogVisible.value = true
}

async function confirmDelete(row) {
  await ElMessageBox.confirm(`确定删除 "${row.original_name}"？`, '确认删除', { type: 'warning' })
  await deleteDocument(row.id)
  ElMessage.success('删除成功')
  await loadDocs()
}

function formatSize(bytes) {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / 1024 / 1024).toFixed(1) + ' MB'
}
</script>

<style scoped>
.header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.doc-content { white-space: pre-wrap; word-break: break-word; max-height: 60vh; overflow-y: auto; font-size: 14px; line-height: 1.6; }
</style>
