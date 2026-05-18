<template>
  <div class="upload-view">
    <h2>上传文档</h2>
    <p class="desc">支持 Word (.docx)、Excel (.xlsx)、PPT (.pptx)、Markdown (.md)、文本 (.txt) 格式</p>

    <el-upload
      class="uploader"
      drag
      action="/api/upload"
      :on-success="onSuccess"
      :on-error="onError"
      :before-upload="beforeUpload"
      multiple
      accept=".docx,.xlsx,.xls,.pptx,.md,.txt"
    >
      <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
      <div class="el-upload__text">拖拽文件到此处，或 <em>点击上传</em></div>
      <template #tip>
        <div class="el-upload__tip">文件将被转换为文本格式存储到知识库中</div>
      </template>
    </el-upload>

    <div v-if="uploadResults.length" class="results">
      <h3>上传结果</h3>
      <el-table :data="uploadResults" stripe>
        <el-table-column prop="name" label="文件名" />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.success ? 'success' : 'danger'">
              {{ row.success ? '成功' : '失败' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="stored_as" label="存储为" />
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { UploadFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

const uploadResults = ref([])

function beforeUpload(file) {
  const allowedExts = ['.docx', '.xlsx', '.xls', '.pptx', '.md', '.txt']
  const ext = '.' + file.name.split('.').pop().toLowerCase()
  if (!allowedExts.includes(ext)) {
    ElMessage.error(`不支持的文件格式: ${ext}`)
    return false
  }
  return true
}

function onSuccess(response, file) {
  uploadResults.value.unshift({
    name: file.name,
    success: true,
    stored_as: response.stored_as,
  })
  ElMessage.success(`${file.name} 上传成功`)
}

function onError(err, file) {
  uploadResults.value.unshift({
    name: file.name,
    success: false,
    stored_as: '-',
  })
  ElMessage.error(`${file.name} 上传失败`)
}
</script>

<style scoped>
.upload-view h2 { margin-bottom: 8px; }
.desc { color: #909399; margin-bottom: 24px; }
.uploader { margin-bottom: 32px; }
.results h3 { margin-bottom: 12px; }
</style>
