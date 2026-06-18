<template>
  <div class="documents-view">
    <div class="header">
      <h2>文档管理 <span class="doc-count">共 {{ documents.length }} 篇</span></h2>
      <div class="header-controls">
        <el-select
          v-model="filterTags"
          multiple
          collapse-tags
          collapse-tags-tooltip
          clearable
          placeholder="按标签筛选"
          style="width: 240px"
        >
          <el-option
            v-for="t in tagsByCount"
            :key="t.id"
            :label="`${t.name} (${t.doc_count})`"
            :value="t.id"
          />
        </el-select>
        <el-input v-model="filterText" placeholder="筛选文档..." style="width: 200px" clearable />
        <el-button v-if="isAdmin" @click="manageVisible = true">管理标签</el-button>
      </div>
    </div>

    <el-table :data="pagedDocs" stripe v-loading="loading" empty-text="暂无文档">
      <el-table-column label="文件名" min-width="240">
        <template #default="{ row }">
          <span class="src-badge" :class="srcType(row).cls">{{ srcType(row).label }}</span>
          {{ row.original_name }}
        </template>
      </el-table-column>
      <el-table-column label="标签" width="220">
        <template #default="{ row }">
          <span v-for="t in row.tags" :key="t.id" class="doc-tag" :style="tagChipStyle(t.name)">{{ t.name }}</span>
          <span v-if="!row.tags.length" class="no-tag">—</span>
        </template>
      </el-table-column>
      <el-table-column prop="file_type" label="类型" width="80" />
      <el-table-column prop="file_size" label="大小" width="100">
        <template #default="{ row }">{{ formatSize(row.file_size) }}</template>
      </el-table-column>
      <el-table-column prop="uploaded_at" label="上传时间" width="180" />
      <el-table-column label="操作" :width="isAdmin ? 280 : 220">
        <template #default="{ row }">
          <template v-if="row.has_old_version">
            <el-button size="small" type="primary" @click="viewVersion(row, 'new')">查看新</el-button>
            <el-button size="small" @click="viewVersion(row, 'old')">查看旧</el-button>
          </template>
          <el-button v-else size="small" @click="viewDoc(row)">查看</el-button>
          <el-button v-if="isAdmin" size="small" @click="openTagDialog(row)">标签</el-button>
          <el-button v-if="isAdmin" size="small" type="danger" @click="confirmDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="pagination-bar" v-if="filteredDocs.length > pageSize">
      <el-pagination
        background
        layout="prev, pager, next, jumper, total"
        :total="filteredDocs.length"
        :page-size="pageSize"
        v-model:current-page="currentPage"
      />
    </div>

    <el-dialog v-model="dialogVisible" :title="currentDoc?.original_name" width="70%">
      <div class="markdown-body doc-content" v-html="renderedContent"></div>
    </el-dialog>

    <el-dialog v-model="tagDialogVisible" :title="`标签 - ${tagDoc?.original_name || ''}`" width="480px">
      <el-select
        v-model="tagDialogSelected"
        multiple
        filterable
        allow-create
        default-first-option
        placeholder="选择或输入新标签"
        style="width: 100%"
      >
        <el-option v-for="t in tags" :key="t.id" :label="t.name" :value="t.name" />
      </el-select>
      <template #footer>
        <el-button @click="tagDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="tagSaving" @click="saveDocTags">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="manageVisible" title="管理标签" width="640px">
      <div class="tag-create-row">
        <el-input v-model="newTagName" placeholder="新标签名" style="width: 160px" @keyup.enter="addTag" />
        <el-input v-model="newTagDesc" placeholder="标签定义（可选）" @keyup.enter="addTag" />
        <el-button type="primary" @click="addTag">新增</el-button>
      </div>
      <el-table :data="tags" size="small" max-height="420" empty-text="暂无标签">
        <el-table-column prop="name" label="标签" width="110" />
        <el-table-column label="定义">
          <template #default="{ row }">
            <el-input
              v-if="editingTagId === row.id"
              v-model="editingTagDesc"
              size="small"
              type="textarea"
              :autosize="{ minRows: 1, maxRows: 4 }"
            />
            <span v-else class="tag-desc">{{ row.description || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="doc_count" label="引用" width="60" />
        <el-table-column label="操作" width="140">
          <template #default="{ row }">
            <template v-if="editingTagId === row.id">
              <el-button size="small" type="primary" text @click="saveTagEdit(row)">保存</el-button>
              <el-button size="small" text @click="cancelTagEdit">取消</el-button>
            </template>
            <template v-else>
              <el-button size="small" text @click="startTagEdit(row)">编辑</el-button>
              <el-button size="small" type="danger" text @click="removeTag(row)">删除</el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted, inject } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getDocuments, getDocument, deleteDocument,
  getTags, createTag, renameTag, deleteTag, setDocumentTags,
} from '../api/index.js'
import { renderMarkdown } from '../utils/markdown.js'
import { tagChipStyle, isActivityTag } from '../utils/tagColor.js'
import 'github-markdown-css/github-markdown-light.css'

const currentUser = inject('currentUser')
const isAdmin = computed(() => currentUser?.value?.role === 'admin')

// 文档来源标签：Mind / 企微 / Info / 其他
function srcType(row) {
  if (row.doc_format === 'mind') return { label: 'Mind', cls: 'src-mind' }
  const u = row.source_url || ''
  if (u.includes('doc.weixin.qq.com')) return { label: '企微', cls: 'src-wecom' }
  if (u.includes('info.example')) return { label: 'Info', cls: 'src-info' }
  return { label: '其他', cls: 'src-other' }
}

const documents = ref([])
const tags = ref([])
// 筛选下拉：按文档数降序（用户要求），并在选项上展示数字
const tagsByCount = computed(() =>
  [...tags.value].sort((a, b) => (b.doc_count || 0) - (a.doc_count || 0)))
const loading = ref(false)
const filterText = ref('')
const filterTags = ref([])
const dialogVisible = ref(false)
const currentDoc = ref(null)
const currentPage = ref(1)
const pageSize = 50

const tagDialogVisible = ref(false)
const tagDoc = ref(null)
const tagDialogSelected = ref([])
const tagSaving = ref(false)
const manageVisible = ref(false)
const newTagName = ref('')
const newTagDesc = ref('')
const editingTagId = ref(null)
const editingTagDesc = ref('')

const renderedContent = computed(() => renderMarkdown(currentDoc.value?.content))

const filteredDocs = computed(() => {
  let list = documents.value
  if (filterText.value) {
    const q = filterText.value.toLowerCase()
    list = list.filter(d => d.original_name.toLowerCase().includes(q))
  }
  if (filterTags.value.length) {
    const sel = new Set(filterTags.value)
    list = list.filter(d => d.tags.some(t => sel.has(t.id)))
  }
  return list
})

const pagedDocs = computed(() => {
  const start = (currentPage.value - 1) * pageSize
  return filteredDocs.value.slice(start, start + pageSize)
})

// 筛选变化时回到第 1 页
watch([filterText, filterTags], () => { currentPage.value = 1 })

onMounted(() => { loadDocs(); loadTags(); startAutoRefresh() })
onUnmounted(() => { if (autoTimer) clearInterval(autoTimer) })

let autoTimer = null
const reimportCount = ref(0)
function startAutoRefresh() {
  // 后台批量重导期间，每 20s 静默刷新一次列表，新出现的「查看新/查看旧」会自动显示。
  // 检测到重导数量增加时提示一次。
  autoTimer = setInterval(() => silentRefresh(), 20000)
}
async function silentRefresh() {
  try {
    const { data } = await getDocuments()
    const n = data.filter(d => d.has_old_version).length
    if (n > reimportCount.value && reimportCount.value > 0) {
      ElMessage.success(`已重导 ${n} 篇（+${n - reimportCount.value}），列表已刷新`)
    }
    reimportCount.value = n
    documents.value = data   // 原地更新，不动当前筛选/分页
  } catch {}
}

async function loadDocs() {
  loading.value = true
  try {
    const { data } = await getDocuments()
    documents.value = data
    reimportCount.value = data.filter(d => d.has_old_version).length
  } finally {
    loading.value = false
  }
}

async function loadTags() {
  try {
    const { data } = await getTags()
    // 活动维度标签置前，便于按大型活动筛选
    tags.value = [...data].sort((a, b) => {
      const av = isActivityTag(a.name) ? 0 : 1
      const bv = isActivityTag(b.name) ? 0 : 1
      return av - bv
    })
  } catch {}
}

async function viewDoc(row) {
  const { data } = await getDocument(row.id)
  currentDoc.value = data
  dialogVisible.value = true
}

// 企微重导过的文档：新版/旧版分别开后端渲染页对比
function viewVersion(row, which) {
  const name = encodeURIComponent(row.stored_path)
  const path = which === 'old' ? `/api/documents/view-old/${name}` : `/api/documents/view/${name}`
  window.open(path, '_blank')
}

function openTagDialog(row) {
  tagDoc.value = row
  tagDialogSelected.value = row.tags.map(t => t.name)
  tagDialogVisible.value = true
}

async function saveDocTags() {
  tagSaving.value = true
  try {
    // 选项值为标签名；新建的名字先建标签拿到 id，已有的查 id
    const ids = []
    for (const name of tagDialogSelected.value) {
      let tag = tags.value.find(t => t.name === name)
      if (!tag) {
        const { data } = await createTag(name)
        tag = data
        await loadTags()
      }
      ids.push(tag.id)
    }
    await setDocumentTags(tagDoc.value.id, ids)
    ElMessage.success('标签已更新')
    tagDialogVisible.value = false
    await loadDocs()
    await loadTags()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败')
  } finally {
    tagSaving.value = false
  }
}

async function addTag() {
  const name = newTagName.value.trim()
  if (!name) return
  try {
    await createTag(name, newTagDesc.value.trim())
    newTagName.value = ''
    newTagDesc.value = ''
    await loadTags()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '新增失败')
  }
}

function startTagEdit(row) {
  editingTagId.value = row.id
  editingTagDesc.value = row.description || ''
}

function cancelTagEdit() {
  editingTagId.value = null
  editingTagDesc.value = ''
}

async function saveTagEdit(row) {
  try {
    await renameTag(row.id, row.name, editingTagDesc.value.trim())
    ElMessage.success('已保存')
    cancelTagEdit()
    await loadTags()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败')
  }
}

async function removeTag(row) {
  await ElMessageBox.confirm(`删除标签 "${row.name}"？将同时移除所有文档对它的引用`, '确认删除', { type: 'warning' })
  await deleteTag(row.id)
  ElMessage.success('删除成功')
  await loadTags()
  await loadDocs()
  filterTags.value = filterTags.value.filter(id => id !== row.id)
}

async function confirmDelete(row) {
  await ElMessageBox.confirm(`确定删除 "${row.original_name}"？`, '确认删除', { type: 'warning' })
  await deleteDocument(row.id)
  ElMessage.success('删除成功')
  await loadDocs()
  // 删除后若当前页已无数据，回退一页
  const maxPage = Math.max(1, Math.ceil(filteredDocs.value.length / pageSize))
  if (currentPage.value > maxPage) currentPage.value = maxPage
}

function formatSize(bytes) {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / 1024 / 1024).toFixed(1) + ' MB'
}
</script>

<style scoped>
.src-badge {
  display: inline-block; font-size: 11px; line-height: 1.4;
  padding: 1px 7px; border-radius: 4px; margin-right: 8px;
  font-weight: 600; vertical-align: middle;
}
.src-wecom { background: #eef4ff; color: #2f6bd6; border: 1px solid #c9ddff; }
.src-info { background: #fff3e6; color: #d97a1a; border: 1px solid #ffd9a8; }
.src-mind { background: #f0ebff; color: #7c4dd6; border: 1px solid #d9c9ff; }
.src-other { background: #f0f2f5; color: #909399; border: 1px solid #e0e3e8; }
.header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.header-controls { display: flex; gap: 10px; align-items: center; }
.doc-count { font-size: 14px; font-weight: normal; color: #909399; margin-left: 8px; }
.doc-content { max-height: 65vh; overflow-y: auto; padding: 16px; font-size: 14px; }
.doc-content :deep(img) { max-width: 100%; height: auto; border: 1px solid #ebeef5; border-radius: 4px; margin: 8px 0; display: block; }
.doc-tag {
  display: inline-flex;
  align-items: center;
  padding: 2px 10px;
  margin-right: 4px;
  margin-bottom: 2px;
  font-size: 12px;
  line-height: 1.5;
  border: 1px solid transparent;
  border-radius: 12px;
}
.no-tag { color: #c0c4cc; }
.tag-create-row { display: flex; gap: 10px; margin-bottom: 16px; }
.tag-desc { font-size: 13px; color: #606266; white-space: pre-wrap; word-break: break-word; }
.pagination-bar { margin-top: 20px; display: flex; justify-content: center; }
</style>
