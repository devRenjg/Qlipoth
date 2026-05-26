<template>
  <div class="upload-view">
    <h2>上传文档</h2>
    <p class="desc">将文档导入知识库，支持文件上传和腾讯文档链接导入</p>

    <el-tabs v-model="activeTab" class="upload-tabs">
      <el-tab-pane label="文件上传" name="file">
        <p class="tab-desc">支持 Word (.docx)、Excel (.xlsx)、PPT (.pptx)、PDF (.pdf)、Markdown (.md)、文本 (.txt) 格式</p>
        <el-upload
          class="uploader"
          drag
          action="/api/upload"
          :on-success="onSuccess"
          :on-error="onError"
          :before-upload="beforeUpload"
          multiple
          accept=".docx,.xlsx,.xls,.pptx,.pdf,.md,.txt"
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
            <span class="depth-label">递归抓取层数：</span>
            <el-input-number
              v-model="maxDepth"
              :min="0"
              :max="3"
              :step="1"
              :disabled="importing"
              size="small"
              controls-position="right"
            />
            <span class="depth-hint">（0 = 仅当前文档，最多 3 层）</span>
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
            正在通过浏览器抓取文档内容{{ maxDepth > 0 ? '（含嵌套文档，共' + maxDepth + '层）' : '' }}，预计需要 {{ maxDepth > 0 ? '30-120' : '10-30' }} 秒...
          </p>
        </div>
      </el-tab-pane>

      <el-tab-pane :label="'失败重试 (' + failedImports.length + ')'" name="retry" v-if="currentUser?.role === 'admin'">
        <p class="tab-desc">以下文档导入失败，可选择重试</p>
        <div class="retry-actions">
          <el-button type="primary" size="small" :loading="retrying" :disabled="!selectedFailed.length" @click="retrySelected">
            重试选中 ({{ selectedFailed.length }})
          </el-button>
          <el-button size="small" @click="selectAllFailed">全选</el-button>
          <el-button size="small" @click="selectedFailed = []">取消全选</el-button>
          <el-button size="small" @click="loadFailedImports" :loading="loadingFailed">刷新</el-button>
        </div>
        <el-table :data="failedImports" stripe v-loading="loadingFailed" empty-text="暂无失败记录" @selection-change="onFailedSelectionChange" ref="failedTableRef">
          <el-table-column type="selection" width="40" />
          <el-table-column prop="title" label="标题" min-width="150">
            <template #default="{ row }">{{ row.title || '(无标题)' }}</template>
          </el-table-column>
          <el-table-column prop="error" label="失败原因" width="160" />
          <el-table-column prop="url" label="链接" min-width="200">
            <template #default="{ row }">
              <a :href="row.url" target="_blank" class="retry-link">{{ row.url.slice(0, 50) }}...</a>
            </template>
          </el-table-column>
          <el-table-column prop="created_at" label="时间" width="160" />
          <el-table-column label="操作" width="100">
            <template #default="{ row }">
              <el-button size="small" type="danger" text @click="deleteFailedRecord(row.id)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
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
              <el-tag size="small" type="info" style="margin-left: 8px;">{{ record.tree.filter(n => n.stored_as && n.stored_as !== '(已存在)' && !n.error).length }} 个文档</el-tag>
              <span class="history-time">{{ record.imported_at }}</span>
              <el-button
                size="small"
                type="danger"
                text
                @click.stop="deleteTree(record.id, record.root_title)"
                style="margin-left: 8px;"
              >删除</el-button>
            </template>
            <div class="doc-tree">
              <div
                v-for="(node, idx) in record.tree"
                :key="idx"
                class="tree-node"
                :style="{ paddingLeft: node.depth * 24 + 'px' }"
              >
                <span class="tree-indent" v-if="node.depth > 0">└─</span>
                <el-tag :type="node.error ? 'danger' : node.stored_as === '(已存在)' ? 'warning' : 'success'" size="small" class="tree-tag">
                  {{ node.error ? '失败' : node.stored_as === '(已存在)' ? '跳过' : '成功' }}
                </el-tag>
                <span class="tree-title">{{ node.title || node.url }}</span>
                <span class="tree-depth" v-if="node.depth > 0">（第{{ node.depth }}层）</span>
                <a v-if="node.url" :href="node.url" target="_blank" class="tree-link" @click.stop>源链接</a>
                <span class="tree-parent" v-if="node.parent">← {{ node.parent }}</span>
                <span class="tree-error" v-if="node.error">{{ node.error }}</span>
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
import { ref, inject, computed, onMounted } from 'vue'
import { UploadFilled } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import axios from 'axios'

const currentUser = inject('currentUser')

const activeTab = ref('file')
const uploadResults = ref([])
const docUrl = ref('')
const importing = ref(false)
const maxDepth = ref(2)
const importTree = ref([])
const importHistory = ref([])
const loadingHistory = ref(false)
const failedImports = ref([])
const loadingFailed = ref(false)
const selectedFailed = ref([])
const retrying = ref(false)
const failedTableRef = ref(null)

const importSummary = computed(() => {
  if (!importTree.value.length) return null
  const success = importTree.value.filter(n => !n.error && !n.skipped).length
  const skipped = importTree.value.filter(n => n.skipped).length
  const failed = importTree.value.filter(n => n.error).length
  return { success, skipped, failed, total: success + skipped + failed }
})

function beforeUpload(file) {
  const allowedExts = ['.docx', '.xlsx', '.xls', '.pptx', '.pdf', '.md', '.txt']
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
  loadHistory()
}

function onError(err, file) {
  let msg = `${file.name} 上传失败`
  try {
    const resp = JSON.parse(err.message)
    if (resp.detail) msg = resp.detail
  } catch {}
  uploadResults.value.unshift({
    name: file.name,
    success: false,
    stored_as: '-',
    error: msg,
  })
  ElMessage.warning(msg)
}

async function importFromUrl() {
  if (!docUrl.value.trim()) return
  importing.value = true
  importTree.value = []

  const url = docUrl.value.trim()
  const body = JSON.stringify({ url, max_depth: maxDepth.value })

  try {
    const resp = await fetch('/api/upload/url/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
    })

    if (!resp.ok) {
      const errData = await resp.json().catch(() => ({}))
      throw new Error(errData.detail || `HTTP ${resp.status}`)
    }

    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop()
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        try {
          const msg = JSON.parse(line.slice(6))
          if (msg.type === 'progress') {
            const d = msg.data
            importTree.value.push({
              title: d.title || '',
              url: d.url,
              stored_as: d.stored_as || null,
              depth: d.depth,
              children: [],
              parent: null,
              error: d.status === 'failed' ? d.error : null,
              skipped: d.status === 'skipped',
            })
          } else if (msg.type === 'done') {
            const d = msg.data
            ElMessage.success(`导入完成：成功 ${d.success} 个${d.failed ? `，失败 ${d.failed} 个` : ''}${d.skipped ? `，跳过 ${d.skipped} 个` : ''}`)
            docUrl.value = ''
            loadHistory()
          } else if (msg.type === 'error') {
            ElMessage.error(msg.data.error || '导入失败')
          }
        } catch {}
      }
    }
  } catch (err) {
    const msg = err.message || '导入失败，请检查链接是否正确'
    if (!importTree.value.length) {
      importTree.value = [{ title: '', url, stored_as: null, depth: 0, children: [], parent: null, error: msg, skipped: false }]
    }
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

async function deleteTree(id, title) {
  try {
    await ElMessageBox.confirm(
      `确定删除「${title}」及其所有关联文档？此操作不可恢复。`,
      '删除确认',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    return
  }
  try {
    const { data } = await axios.delete(`/api/upload/trees/${id}`)
    ElMessage.success(data.message)
    loadHistory()
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || '删除失败')
  }
}

onMounted(() => {
  loadHistory()
  loadFailedImports()
})

async function loadFailedImports() {
  loadingFailed.value = true
  try {
    const { data } = await axios.get('/api/upload/failed')
    failedImports.value = data
  } catch {} finally {
    loadingFailed.value = false
  }
}

function onFailedSelectionChange(rows) {
  selectedFailed.value = rows
}

function selectAllFailed() {
  failedTableRef.value?.toggleAllSelection()
}

async function deleteFailedRecord(id) {
  try {
    await axios.delete(`/api/upload/failed/${id}`)
    ElMessage.success('已删除')
    loadFailedImports()
  } catch {}
}

async function retrySelected() {
  if (!selectedFailed.value.length) return
  retrying.value = true
  let success = 0, fail = 0
  for (const item of selectedFailed.value) {
    try {
      const resp = await fetch('/api/upload/url/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: item.url, max_depth: 0 }),
      })
      if (!resp.ok) { fail++; continue }
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = '', imported = false, skipped = false
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop()
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          try {
            const msg = JSON.parse(line.slice(6))
            if (msg.type === 'progress' && msg.data.status === 'success') imported = true
            if (msg.type === 'progress' && msg.data.status === 'skipped') skipped = true
            if (msg.type === 'done' && msg.data.success > 0) imported = true
          } catch {}
        }
      }
      if (imported || skipped) {
        await axios.delete(`/api/upload/failed/${item.id}`)
        success++
      } else {
        fail++
      }
    } catch {
      fail++
    }
  }
  retrying.value = false
  ElMessage.success(`重试完成：成功 ${success} 个${fail ? `，失败 ${fail} 个` : ''}`)
  loadFailedImports()
  loadHistory()
}
</script>

<style scoped>
.upload-view h2 { margin-bottom: 8px; }
.desc { color: #909399; margin-bottom: 24px; }
.tab-desc { color: #909399; margin-bottom: 16px; font-size: 14px; }
.upload-tabs { margin-bottom: 32px; }
.uploader { margin-bottom: 16px; }
.url-import { max-width: 700px; }
.import-options { margin-top: 12px; display: flex; align-items: center; gap: 8px; }
.depth-label { font-size: 14px; color: #606266; }
.depth-hint { font-size: 12px; color: #909399; }
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
.tree-link { color: #409eff; font-size: 12px; text-decoration: none; }
.tree-link:hover { text-decoration: underline; }
.tree-parent { color: #909399; font-size: 12px; font-style: italic; }
.tree-error { color: #f56c6c; font-size: 12px; }
.history-section { margin-top: 40px; }
.history-title { font-weight: 500; }
.history-time { color: #909399; font-size: 12px; margin-left: auto; padding-right: 12px; }
.retry-actions { margin-bottom: 16px; display: flex; gap: 8px; align-items: center; }
.retry-link { color: #409eff; text-decoration: none; font-size: 12px; }
.retry-link:hover { text-decoration: underline; }
</style>