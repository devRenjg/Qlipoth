<template>
  <div class="bm-view">
    <div class="bm-head">
      <div>
        <h2>作战地图</h2>
        <p class="bm-sub">基于知识库全部文档按保障方向归纳的全局认知，帮新负责人快速建立大局观——这块是什么、涉及哪些系统、历史踩过哪些坑、哪里水深。</p>
      </div>
      <div class="bm-actions">
        <el-button v-if="isAdmin" type="primary" :loading="generating" :disabled="generating" @click="doGenerate">
          {{ generating ? `生成中 ${progress.done}/${progress.total}` : '重新生成' }}
        </el-button>
      </div>
    </div>

    <el-alert v-if="generating" type="info" :closable="false" show-icon class="bm-progress"
      :title="`正在生成：${progress.current || ''}（${progress.done}/${progress.total} 方向完成），可离开页面稍后回来看`" />

    <div v-loading="loading" class="bm-cards">
      <el-empty v-if="!anyContent && !loading" description="作战地图还没生成">
        <span v-if="!isAdmin" class="bm-empty-hint">请管理员在本页生成</span>
      </el-empty>
      <div v-for="d in dimensions" :key="d.dimension" class="bm-card" v-show="d.content">
        <div class="bm-card-title">
          <span class="bm-dim">{{ d.label }}</span>
          <span class="bm-meta">{{ d.source_doc_count }} 篇相关文档 · {{ d.updated_at || '' }}</span>
        </div>
        <template v-if="d.content">
          <p class="bm-positioning">{{ d.content.positioning }}</p>

          <div class="bm-sec" v-if="d.content.key_systems?.length">
            <div class="bm-sec-h">🔧 关键系统 / 链路</div>
            <ul><li v-for="(x,i) in d.content.key_systems" :key="i">{{ x }}</li></ul>
          </div>
          <div class="bm-sec" v-if="d.content.history?.length">
            <div class="bm-sec-h">📜 历史发生过什么</div>
            <ul><li v-for="(x,i) in d.content.history" :key="i">{{ x }}</li></ul>
          </div>
          <div class="bm-sec" v-if="d.content.pitfalls?.length">
            <div class="bm-sec-h">⚠ 水深的地方（重点警惕）</div>
            <ul><li v-for="(x,i) in d.content.pitfalls" :key="i">{{ x }}</li></ul>
          </div>
          <div class="bm-sec" v-if="d.content.recommended_docs?.length">
            <div class="bm-sec-h">📌 建议先看</div>
            <div class="bm-docs">
              <el-button v-for="(doc,i) in d.content.recommended_docs" :key="i" size="small" text
                @click="viewDoc(doc)">{{ doc.title }}</el-button>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, inject, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import axios from 'axios'

const api = axios.create({ baseURL: '/api' })
const currentUser = inject('currentUser')
const isAdmin = computed(() => currentUser?.value?.role === 'admin')

const dimensions = ref([])
const progress = ref({ status: 'idle', done: 0, total: 0, current: '' })
const loading = ref(false)
let timer = null

const generating = computed(() => progress.value.status === 'generating')
const anyContent = computed(() => dimensions.value.some(d => d.content))

onMounted(() => { load(); timer = setInterval(pollIfGenerating, 5000) })
onUnmounted(() => { if (timer) clearInterval(timer) })

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/battlemap')
    dimensions.value = data.dimensions
    progress.value = data.progress || progress.value
  } finally {
    loading.value = false
  }
}

async function pollIfGenerating() {
  if (progress.value.status !== 'generating') return
  try {
    const { data } = await api.get('/battlemap/progress')
    const was = progress.value.done
    progress.value = data
    if (data.status !== 'generating' || data.done !== was) load()  // 有方向完成就刷新卡片
  } catch {}
}

async function doGenerate() {
  try {
    await api.post('/battlemap/generate')
    progress.value = { status: 'generating', done: 0, total: 6, current: '' }
    ElMessage.success('开始生成，约需几分钟，完成的方向会陆续显示')
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || '生成失败')
  }
}

function viewDoc(doc) {
  // 优先跳原始企微/info 在线文档；无源链接才回退本地渲染页
  if (doc.url) {
    window.open(doc.url, '_blank')
  } else {
    window.open(`/api/documents/view/${encodeURIComponent(doc.path)}`, '_blank')
  }
}
</script>

<style scoped>
.bm-view { padding: 4px 2px; }
.bm-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
.bm-head h2 { margin: 0 0 6px; }
.bm-sub { color: #909399; font-size: 13px; max-width: 760px; line-height: 1.6; margin: 0; }
.bm-progress { margin: 14px 0; }
.bm-cards { margin-top: 18px; display: grid; grid-template-columns: repeat(auto-fill, minmax(420px, 1fr)); gap: 16px; }
.bm-card { border: 1px solid #ebeef5; border-radius: 10px; padding: 16px 18px; background: #fff; }
.bm-card-title { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }
.bm-dim { font-size: 16px; font-weight: 600; color: #303133; }
.bm-meta { font-size: 12px; color: #c0c4cc; }
.bm-positioning { color: #409eff; font-size: 13px; margin: 0 0 12px; line-height: 1.6; }
.bm-sec { margin-bottom: 12px; }
.bm-sec-h { font-size: 13px; font-weight: 600; color: #606266; margin-bottom: 4px; }
.bm-sec ul { margin: 0; padding-left: 18px; }
.bm-sec li { font-size: 13px; color: #606266; line-height: 1.7; }
.bm-docs { display: flex; flex-wrap: wrap; gap: 4px; }
.bm-empty-hint { color: #c0c4cc; font-size: 13px; }
</style>
