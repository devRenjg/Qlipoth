<template>
  <div class="upload-view">
    <h2>上传文档</h2>
    <p class="desc">将文档导入知识库，支持文件上传和腾讯文档链接导入</p>

    <el-tabs v-model="activeTab" class="upload-tabs">
      <el-tab-pane label="文件上传" name="file">
        <p class="tab-desc">支持 Word (.docx)、Excel (.xlsx)、PPT (.pptx)、Markdown (.md)、文本 (.txt) 格式</p>
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
      </el-tab-pane>

      <el-tab-pane label="链接导入" name="url">
        <p class="tab-desc">输入腾讯文档/企业微信文档链接，系统将自动抓取内容导入知识库</p>
        <div class="url-import">
          <el-input
            v-model="docUrl"
            placeholder="请输入文档链接，如 https://doc.weixin.qq.com/doc/..."
            size="large"
            clearable
            :disabled="importing"
          >
            <template #prepend>链接</template>
          </el-input>
          <div class="import-options">
            <el-checkbox v-model="recursive" :disabled="importing">
              递归导入嵌套文档（最多5层深度）
            </el-checkbox>
          </div>
          <el-button
            type="primary"
            size="large"
            :loading="importing"
            :disabled="!docUrl.trim()"
            @click="importFromUrl"
            class="import-btn"
          >
            {{ importing ? '正在抓取...' : '导入' }}
          </el-button>
          <p v-if="importing" class="import-tip">
            正在通过浏览器抓取文档内容{{ recursive ? '（含嵌套文档）' : '' }}，预计需要 {{ recursive ? '30-120' : '10-30' }} 秒...
          </p>
        </div>
      </el-tab-pane>
    </el-tabs>

    <!-- 文件上传结果 -->
    <div v-if="uploadResults.length" class="results">
      <h3>文件上传结果</h3>
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

    <!-- 链接导入结果（树形） -->
    <div v-if="importTree.length" class="results">
      <h3>链接导入结果</h3>
      <div class="import-summary" v-if="importSummary">
        <el-tag type="success">成功 {{ importSummary.success }}</el-tag>
        <el-tag v-if="importSummary.skipped" type="warning">跳过 {{ importSummary.skipped }}</el-tag>
        <el-tag v-if="importSummary.failed" type="danger">失败 {{ importSummary.failed }}</el-tag>
        <span class="summary-text">共 {{ importSummary.total }} 个文档</span>
      </div>
      <div class="doc-tree">
        <div
          v-for="(node, idx) in importTree"
          :key="idx"
          class="tree-node"
          :style="{ paddingLeft: node.depth * 24 + 'px' }"
        >
          <span class="tree-indent" v-if="node.depth > 0">└─</span>
          <el-tag :type="node.error ? 'danger' : node.skipped ? 'warning' : 'success'" size="small" class="tree-tag">
            {{ node.error ? '失败' : node.skipped ? '跳过' : '成功' }}
          </el-tag>
          <span class="tree-title">{{ node.title || node.url }}</span>
          <span class="tree-depth" v-if="node.depth > 0">（第{{ node.depth }}层子文档）</span>
          <span class="tree-stored" v-if="node.stored_as"> → {{ node.stored_as }}</span>
          <span class="tree-error" v-if="node.error"> {{ node.error }}</span>
        </div>
      </div>
    </div>

    <!-- 导入历史 -->
    <div class="results history-section">
      <h3>
        导入历史
        <el-button size="small" @click="loadHistory" :loading="loadingHistory" style="margin-left: 12px;">刷新</el-button>
      </h3>
      <div v-if="importHistory.length" class="history-list">
        <el-collapse>
          <el-collapse-item v-for="record in importHistory" :key="record.id" :name="record.id">
            <template #title>
              <span class="history-title">{{ record.root_title }}</span>
              <el-tag size="small" type="info" style="margin-left: 8px;">{{ record.doc_count }} 个文档</el-tag>
              <span class="history-time">{{ record.imported_at }}</span>
            </template>
            <div class="doc-tree">
              <div
                v-for="(node, idx) in record.tree"
                :key="idx"
                class="tree-node"
                :style="{ paddingLeft: node.depth * 24 + 'px' }"
              >
                <span class="tree-indent" v-if="node.depth > 0">└─</span>
                <el-tag :type="node.error ? 'danger' : 'success'" size="small" class="tree-tag">
                  {{ node.error ? '失败' : '成功' }}
                </el-tag>
                <span class="tree-title">{{ node.title || node.url }}</span>
                <span class="tree-depth" v-if="node.depth > 0">（第{{ node.depth }}层）</span>
              </div>
            </div>
          </el-collapse-item>
        </el-collapse>
      </div>
      <el-empty v-else-if="!loadingHistory" description="暂无导入记录" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { UploadFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import axios from 'axios'

const activeTab = ref('file')
const uploadResults = ref([])
const docUrl = ref('')
const importing = ref(false)
const recursive = ref(true)
const importTree = ref([])
const importHistory = ref([])
const loadingHistory = ref(false)

const importSummary = computed(() => {
  if (!importTree.value.length) return null
  const success = importTree.value.filter(n => !n.error && !n.skipped).length
  const skipped = importTree.value.filter(n => n.skipped).length
  const failed = importTree.value.filter(n => n.error).length
  return { success, skipped, failed, total: success + skipped + failed }
})

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

async function importFromUrl() {
  if (!docUrl.value.trim()) return
  importing.value = true
  importTree.value = []
  try {
    const { data } = await axios.post('/api/upload/url', {
      url: docUrl.value.trim(),
      recursive: recursive.value,
      max_depth: 5,
    })

    const nodes = []
    if (data.documents) {
      for (const doc of data.documents) {
        nodes.push({
          title: doc.title,
          url: doc.url,
          stored_as: doc.stored_as,
          depth: doc.depth,
          children: doc.children,
          parent: doc.parent,
          error: null,
          skipped: false,
        })
      }
    }
    if (data.skipped) {
      for (const s of data.skipped) {
        nodes.push({
          title: s.title,
          url: s.url,
          stored_as: null,
          depth: s.depth,
          children: [],
          parent: null,
          error: null,
          skipped: true,
        })
      }
    }
    if (data.failed) {
      for (const f of data.failed) {
        nodes.push({
          title: '',
          url: f.url,
          stored_as: null,
          depth: f.depth,
          children: [],
          parent: null,
          error: f.error,
          skipped: false,
        })
      }
    }
    nodes.sort((a, b) => a.depth - b.depth)
    importTree.value = nodes

    const successCount = data.documents?.length || 0
    const failCount = data.failed?.length || 0
    ElMessage.success(`导入完成：成功 ${successCount} 个${failCount ? `，失败 ${failCount} 个` : ''}`)
    docUrl.value = ''
  } catch (err) {
    const msg = err.response?.data?.detail || '导入失败，请检查链接是否正确'
    importTree.value = [{ title: '', url: docUrl.value, stored_as: null, depth: 0, children: [], parent: null, error: msg }]
    ElMessage.error(msg)
  } finally {
    importing.value = false
  }
}

async function loadHistory() {
  loadingHistory.value = true
  try {
    const { data } = await axios.get('/api/upload/trees')
    importHistory.value = data
  } catch (err) {
    console.error('Failed to load history', err)
  } finally {
    loadingHistory.value = false
  }
}

onMounted(() => {
  loadHistory()
})
</script>

<style scoped>
.upload-view h2 { margin-bottom: 8px; }
.desc { color: #909399; margin-bottom: 24px; }
.tab-desc { color: #909399; margin-bottom: 16px; font-size: 14px; }
.upload-tabs { margin-bottom: 32px; }
.uploader { margin-bottom: 16px; }
.url-import { max-width: 700px; }
.import-options { margin-top: 12px; }
.import-btn { margin-top: 16px; }
.import-tip { color: #e6a23c; font-size: 13px; margin-top: 12px; }
.results { margin-top: 24px; }
.results h3 { margin-bottom: 12px; }
.import-summary { margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }
.summary-text { color: #909399; font-size: 13px; }
.doc-tree { border: 1px solid #ebeef5; border-radius: 4px; padding: 12px; }
.tree-node { padding: 8px 4px; display: flex; align-items: center; gap: 8px; border-bottom: 1px solid #f5f5f5; }
.tree-node:last-child { border-bottom: none; }
.tree-indent { color: #c0c4cc; font-family: monospace; }
.tree-tag { flex-shrink: 0; }
.tree-title { font-weight: 500; }
.tree-depth { color: #909399; font-size: 12px; }
.tree-stored { color: #67c23a; font-size: 12px; }
.tree-error { color: #f56c6c; font-size: 12px; }
.history-section { margin-top: 40px; }
.history-title { font-weight: 500; }
.history-time { color: #909399; font-size: 12px; margin-left: auto; padding-right: 12px; }
</style>