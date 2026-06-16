<template>
  <div class="bm-view">
    <div class="bm-head">
      <div>
        <h2>作战地图</h2>
        <p class="bm-sub">基于知识库全部文档按保障方向归纳的全局认知，帮新负责人快速建立大局观——这块是什么、涉及哪些系统、历史踩过哪些坑、哪里水深。</p>
      </div>
    </div>

    <el-alert v-if="generating" type="info" :closable="false" show-icon class="bm-progress"
      :title="`正在生成：${progress.current || ''}（${progress.done}/${progress.total} 方向完成），可离开页面稍后回来看`" />

    <div v-loading="loading" class="bm-cards">
      <el-empty v-if="!anyContent && !loading" description="作战地图尚未生成" />

      <!-- 历史大型活动(时间倒序，置于最前) -->
      <div v-if="events && events.length" class="bm-card bm-events" :class="{ open: isOpen('__events__') }">
        <div class="bm-card-bar"></div>
        <div class="bm-card-title" @click="toggle('__events__')">
          <span class="bm-dim-ico">🗓️</span>
          <span class="bm-dim">历史大型活动</span>
          <span class="bm-chevron">▾</span>
        </div>
        <el-collapse-transition>
          <div v-show="isOpen('__events__')">
            <div class="bm-timeline">
              <div v-for="(e, i) in events" :key="i" class="bm-event">
                <div class="bm-event-head">
                  <span class="bm-event-time">{{ e.time }}</span>
                  <span class="bm-event-name">{{ e.name }}</span>
                </div>
                <div class="bm-event-note" v-if="e.note">{{ e.note }}</div>
                <div class="bm-event-metrics" v-if="e.metrics && e.metrics.length">
                  <div v-for="(m, j) in e.metrics" :key="j" class="bm-metric-row">
                    <span class="bm-metric-k">{{ m.k }}</span>
                    <span class="bm-metric-v">{{ m.v }}</span>
                  </div>
                </div>
                <div class="bm-event-nodata" v-else>暂无量级数据</div>
              </div>
            </div>
          </div>
        </el-collapse-transition>
      </div>

      <!-- 保障节奏:什么阶段做什么 -->
      <div v-if="timeline && timeline.length" class="bm-card bm-rhythm" :class="{ open: isOpen('__timeline__') }">
        <div class="bm-card-bar"></div>
        <div class="bm-card-title" @click="toggle('__timeline__')">
          <span class="bm-dim-ico">⏱️</span>
          <span class="bm-dim">保障节奏 · 各阶段重点</span>
          <span class="bm-chevron">▾</span>
        </div>
        <el-collapse-transition>
          <div v-show="isOpen('__timeline__')">
            <div class="bm-rhythm-flow">
              <div v-for="(s, i) in timeline" :key="i" class="bm-phase">
                <div class="bm-phase-name">{{ i + 1 }}. {{ s.stage }}</div>
                <div class="bm-phase-focus">{{ s.focus }}</div>
              </div>
            </div>
          </div>
        </el-collapse-transition>
      </div>

      <!-- 关键角色与团队(置于方向卡片前) -->
      <div v-if="roles && roles.length" class="bm-card bm-roles" :class="{ open: isOpen('__roles__') }">
        <div class="bm-card-bar"></div>
        <div class="bm-card-title" @click="toggle('__roles__')">
          <span class="bm-dim-ico">👥</span>
          <span class="bm-dim">历史保障 · 关键角色与团队</span>
          <span class="bm-chevron">▾</span>
        </div>
        <el-collapse-transition>
          <div v-show="isOpen('__roles__')">
            <div class="bm-roles-grid">
              <div v-for="(r, i) in roles" :key="i" class="bm-role">
                <div class="bm-role-team">{{ r.team }}</div>
                <div class="bm-role-owners">
                  <span v-for="(o, j) in r.owners" :key="j" class="bm-owner-chip">{{ o }}</span>
                </div>
                <div class="bm-role-scope">{{ r.scope }}</div>
              </div>
            </div>
          </div>
        </el-collapse-transition>
      </div>

      <div v-for="d in dimensions" :key="d.dimension" class="bm-card" :class="{ open: isOpen(d.dimension) }" v-show="d.content">
        <div class="bm-card-bar"></div>
        <div class="bm-card-title" @click="toggle(d.dimension)">
          <span class="bm-dim-ico">{{ dimIcon(d.dimension) }}</span>
          <span class="bm-dim">{{ d.label }}</span>
          <span class="bm-chevron">▾</span>
        </div>
        <p class="bm-positioning" v-if="d.content">{{ d.content.positioning }}</p>
        <el-collapse-transition>
          <div v-show="isOpen(d.dimension)">
            <template v-if="d.content">
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
                  <a v-for="(doc,i) in d.content.recommended_docs" :key="i" class="bm-doc-chip"
                    @click="viewDoc(doc)">
                    <span class="chip-ico">🔗</span>
                    <span class="chip-txt">{{ doc.title }}</span>
                  </a>
                </div>
              </div>
            </template>
          </div>
        </el-collapse-transition>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import axios from 'axios'
import { TAG_TO_ICON } from '../dimensions.js'

const api = axios.create({ baseURL: '/api' })

const dimensions = ref([])
const roles = ref([])
const events = ref([])
const baseline = ref([])
const timeline = ref([])
const progress = ref({ status: 'idle', done: 0, total: 0, current: '' })
const loading = ref(false)
const openSet = ref(new Set())   // 默认全部折叠收拢
let timer = null

const generating = computed(() => progress.value.status === 'generating')
const anyContent = computed(() => dimensions.value.some(d => d.content))

function isOpen(dim) { return openSet.value.has(dim) }
function toggle(dim) {
  const s = new Set(openSet.value)
  s.has(dim) ? s.delete(dim) : s.add(dim)
  openSet.value = s
}
function dimIcon(dim) { return TAG_TO_ICON[dim] || '📂' }

onMounted(() => { load(); timer = setInterval(pollIfGenerating, 5000) })
onUnmounted(() => { if (timer) clearInterval(timer) })

async function load() {
  loading.value = true
  try {
    const { data } = await api.get('/battlemap')
    dimensions.value = data.dimensions
    roles.value = data.roles || []
    events.value = data.events || []
    baseline.value = data.baseline || []
    timeline.value = data.timeline || []
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

.bm-cards { margin-top: 18px; display: flex; flex-direction: column; gap: 14px; }

/* 卡片：白底 + 左侧蓝青渐变光条 + 蓝调阴影，呼应导航栏深空蓝 */
.bm-card {
  position: relative;
  border: 1px solid #dbe5f2;
  border-radius: 12px;
  padding: 16px 20px 16px 22px;
  background: linear-gradient(180deg, #fbfdff 0%, #ffffff 60%);
  box-shadow: 0 2px 10px rgba(31, 58, 110, 0.06);
  overflow: hidden;
  transition: box-shadow .25s ease, border-color .25s ease, transform .12s ease;
}
.bm-card:hover { border-color: rgba(47,128,255,.4); box-shadow: 0 6px 22px rgba(47,128,255,.16); }
.bm-card.open { border-color: rgba(47,128,255,.5); box-shadow: 0 6px 24px rgba(47,128,255,.18); }
/* 左侧装饰光条 */
.bm-card-bar {
  position: absolute; left: 0; top: 0; bottom: 0; width: 4px;
  background: linear-gradient(180deg, #2f80ff, #36d1c4);
  opacity: .65; transition: opacity .25s, width .25s;
}
.bm-card.open .bm-card-bar, .bm-card:hover .bm-card-bar { opacity: 1; width: 5px; }

.bm-card-title { display: flex; align-items: center; gap: 10px; cursor: pointer; user-select: none; }
.bm-dim-ico { font-size: 18px; }
.bm-dim { font-size: 16px; font-weight: 600; color: #1c2f5e; }
.bm-meta {
  font-size: 12px; color: #5a7bb0;
  background: #eef3fb; border: 1px solid #dbe5f2;
  padding: 1px 8px; border-radius: 10px;
}
.bm-chevron { margin-left: auto; color: #8aa6d0; font-size: 14px; transition: transform .25s ease; }
.bm-card.open .bm-chevron { transform: rotate(180deg); color: #2f80ff; }

.bm-positioning {
  color: #2c4a7c; font-size: 13px; margin: 10px 0 0; line-height: 1.7;
  padding: 9px 12px;
  background: linear-gradient(90deg, rgba(47,128,255,.08), rgba(54,209,196,.05));
  border-left: 3px solid #2f80ff;
  border-radius: 0 8px 8px 0;
}
.bm-card.open .bm-positioning { margin-bottom: 4px; }

.bm-sec { margin-top: 14px; }
.bm-sec-h {
  font-size: 13px; font-weight: 600; color: #2c4a7c; margin-bottom: 6px;
  padding-bottom: 4px; border-bottom: 1px dashed #dbe5f2;
}
.bm-sec ul { margin: 0; padding-left: 18px; }
.bm-sec li { font-size: 13px; color: #475569; line-height: 1.75; }

/* 建议先看：做成可点击的胶囊标签，区别于纯文字 */
.bm-docs { display: flex; flex-wrap: wrap; gap: 8px; }
.bm-doc-chip {
  display: inline-flex; align-items: center; gap: 5px;
  max-width: 100%;
  padding: 5px 12px; border-radius: 16px;
  font-size: 12.5px; color: #1d4ed8; text-decoration: none;
  background: #eef4ff; border: 1px solid #c9ddff;
  transition: background .2s, border-color .2s, transform .1s, box-shadow .2s;
  cursor: pointer;
}
.bm-doc-chip:hover {
  background: #2f80ff; color: #fff; border-color: #2f80ff;
  transform: translateY(-1px); box-shadow: 0 3px 10px rgba(47,128,255,.3);
}
.bm-doc-chip .chip-ico { font-size: 12px; opacity: .85; }
.bm-doc-chip .chip-txt { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 260px; }

/* 关键角色与团队卡片 */
.bm-roles-head { display: flex; align-items: center; gap: 10px; }
.bm-roles-head .bm-dim { font-size: 16px; font-weight: 600; color: #1c2f5e; }
.bm-roles-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 12px; margin-top: 12px; }
.bm-role { border: 1px solid #e3ebf6; border-radius: 10px; padding: 10px 12px; background: #fafcff; }
.bm-role-team { font-size: 13.5px; font-weight: 600; color: #2c4a7c; margin-bottom: 6px; }
.bm-role-owners { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 6px; }
.bm-owner-chip { font-size: 12px; color: #1d4ed8; background: #eef4ff; border: 1px solid #c9ddff; border-radius: 12px; padding: 1px 9px; }
.bm-role-scope { font-size: 12.5px; color: #5a6b85; line-height: 1.6; }

/* 历史大型活动时间线 */
.bm-timeline { margin-top: 12px; display: flex; flex-direction: column; gap: 8px; }
.bm-event { padding: 10px 14px; border-radius: 8px; background: #fafcff; border: 1px solid #e3ebf6; border-left: 3px solid #2f80ff; }
.bm-event-head { display: flex; align-items: baseline; gap: 10px; }
.bm-event-time { font-size: 12px; color: #5a7bb0; font-weight: 600; min-width: 64px; }
.bm-event-name { font-size: 14px; font-weight: 600; color: #1c2f5e; }
.bm-event-note { font-size: 12.5px; color: #5a6b85; line-height: 1.5; margin-top: 4px; }
.bm-event-metrics { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 6px; margin-top: 8px; }
.bm-metric-row { display: flex; gap: 8px; font-size: 12.5px; line-height: 1.5; }
.bm-metric-k { flex: 0 0 auto; color: #5a6b85; min-width: 72px; font-weight: 600; }
.bm-metric-v { color: #2c4a7c; }
.bm-event-nodata { font-size: 12px; color: #aab4c5; margin-top: 6px; }

/* 规模基线 */
.bm-baseline-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 10px; margin-top: 12px; }
.bm-metric { border: 1px solid #e3ebf6; border-radius: 10px; padding: 9px 12px; background: #fafcff; }
.bm-metric-top { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
.bm-metric-name { font-size: 12.5px; color: #5a6b85; }
.bm-metric-val { font-size: 14px; font-weight: 700; color: #2f80ff; white-space: nowrap; }
.bm-metric-note { font-size: 11.5px; color: #8a9bb5; line-height: 1.5; margin-top: 3px; }
/* 保障节奏 */
.bm-rhythm-flow { display: flex; flex-direction: column; gap: 8px; margin-top: 12px; }
.bm-phase { border-left: 3px solid #36d1c4; background: #fafcff; border: 1px solid #e3ebf6; border-left: 3px solid #36d1c4; border-radius: 8px; padding: 8px 12px; }
.bm-phase-name { font-size: 13px; font-weight: 600; color: #1c2f5e; margin-bottom: 3px; }
.bm-phase-focus { font-size: 12.5px; color: #5a6b85; line-height: 1.6; }
</style>
