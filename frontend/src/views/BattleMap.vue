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
            <div class="bm-year-group" v-for="grp in eventsByYear" :key="grp.year">
              <div class="bm-year-label">{{ grp.year }}年</div>
              <div class="bm-timeline">
                <div v-for="(e, i) in grp.list" :key="i" class="bm-event">
                  <div class="bm-event-head">
                    <span class="bm-event-time">{{ e.time }}</span>
                    <span class="bm-event-name">{{ e.name }}</span>
                  </div>
                  <div class="bm-event-note" v-if="e.note">{{ e.note }}</div>
                  <div class="bm-event-metrics" v-if="e.metrics && e.metrics.length">
                    <div v-for="(m, j) in e.metrics" :key="j" class="bm-metric-row">
                      <span class="bm-metric-k">{{ m.k }}</span>
                      <span class="bm-metric-v" :class="{ 'bm-metric-hot': isHotMetric(m.v) }">{{ m.v }}</span>
                    </div>
                  </div>
                  <div class="bm-event-nodata" v-else>暂无量级数据</div>
                </div>
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
                <ul><li v-for="(x,i) in d.content.key_systems" :key="i" class="bm-item">
                  <div class="bm-item-row"><span class="bm-item-txt">{{ x }}</span>{{ '' }}
                    <span class="bm-fb">
                      <el-tooltip content="点赞：这条对我有帮助" placement="top">
                        <span class="fb-btn" :class="{ on: mine(d,x)==='like' }" @click="vote(d,x,'like')">👍<i v-if="cnt(d,x,'like')">{{ cnt(d,x,'like') }}</i></span>
                      </el-tooltip>
                      <el-tooltip content="疑问：认可方向但想了解更多细节" placement="top">
                        <span class="fb-btn" :class="{ on: mine(d,x)==='question' }" @click="vote(d,x,'question')">❓<i v-if="cnt(d,x,'question')">{{ cnt(d,x,'question') }}</i></span>
                      </el-tooltip>
                    </span>
                  </div>
                  <div class="bm-fb-who" v-if="who(d,x,'like')">👍 {{ who(d,x,'like') }} 认为有用</div>
                  <div class="bm-fb-who q" v-if="who(d,x,'question')">❓ {{ who(d,x,'question') }} 有疑问</div>
                </li></ul>
              </div>
              <div class="bm-sec" v-if="d.content.history?.length">
                <div class="bm-sec-h">📜 历史发生过什么</div>
                <ul><li v-for="(x,i) in d.content.history" :key="i" class="bm-item">
                  <div class="bm-item-row"><span class="bm-item-txt">{{ x }}</span>
                    <span class="bm-fb">
                      <el-tooltip content="点赞：这条对我有帮助" placement="top">
                        <span class="fb-btn" :class="{ on: mine(d,x)==='like' }" @click="vote(d,x,'like')">👍<i v-if="cnt(d,x,'like')">{{ cnt(d,x,'like') }}</i></span>
                      </el-tooltip>
                      <el-tooltip content="疑问：认可方向但想了解更多细节" placement="top">
                        <span class="fb-btn" :class="{ on: mine(d,x)==='question' }" @click="vote(d,x,'question')">❓<i v-if="cnt(d,x,'question')">{{ cnt(d,x,'question') }}</i></span>
                      </el-tooltip>
                    </span>
                  </div>
                  <div class="bm-fb-who" v-if="who(d,x,'like')">👍 {{ who(d,x,'like') }} 认为有用</div>
                  <div class="bm-fb-who q" v-if="who(d,x,'question')">❓ {{ who(d,x,'question') }} 有疑问</div>
                </li></ul>
              </div>
              <div class="bm-sec" v-if="d.content.pitfalls?.length">
                <div class="bm-sec-h">⚠ 水深的地方（重点警惕）</div>
                <ul><li v-for="(x,i) in d.content.pitfalls" :key="i" class="bm-item">
                  <div class="bm-item-row"><span class="bm-item-txt">{{ x }}</span>
                    <span class="bm-fb">
                      <el-tooltip content="点赞：这条对我有帮助" placement="top">
                        <span class="fb-btn" :class="{ on: mine(d,x)==='like' }" @click="vote(d,x,'like')">👍<i v-if="cnt(d,x,'like')">{{ cnt(d,x,'like') }}</i></span>
                      </el-tooltip>
                      <el-tooltip content="疑问：认可方向但想了解更多细节" placement="top">
                        <span class="fb-btn" :class="{ on: mine(d,x)==='question' }" @click="vote(d,x,'question')">❓<i v-if="cnt(d,x,'question')">{{ cnt(d,x,'question') }}</i></span>
                      </el-tooltip>
                    </span>
                  </div>
                  <div class="bm-fb-who" v-if="who(d,x,'like')">👍 {{ who(d,x,'like') }} 认为有用</div>
                  <div class="bm-fb-who q" v-if="who(d,x,'question')">❓ {{ who(d,x,'question') }} 有疑问</div>
                </li></ul>
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
import { ref, computed, onMounted, onUnmounted, inject } from 'vue'
import { ElMessage } from 'element-plus'
import axios from 'axios'
import { TAG_TO_ICON } from '../dimensions.js'

const api = axios.create({ baseURL: '/api' })
const currentUser = inject('currentUser')
const myName = computed(() => currentUser?.value?.username || '')

const dimensions = ref([])
const roles = ref([])
const events = ref([])
const baseline = ref([])
const progress = ref({ status: 'idle', done: 0, total: 0, current: '' })
const loading = ref(false)
const openSet = ref(new Set())   // 默认全部折叠收拢
let timer = null

const generating = computed(() => progress.value.status === 'generating')
const anyContent = computed(() => dimensions.value.some(d => d.content))

// 历史大型活动:按年份分组。年份倒序(新→旧),年内按月份正序(早→晚)。
const eventsByYear = computed(() => {
  const groups = {}
  for (const e of (events.value || [])) {
    const ym = String(e.time || '')
    const y = (ym.match(/^(\d{4})/) || [])[1] || '其他'
    ;(groups[y] = groups[y] || []).push(e)
  }
  // 年内按 time 字符串升序(如 2024.02 < 2024.11)
  for (const y in groups) groups[y].sort((a, b) => String(a.time).localeCompare(String(b.time)))
  // 年份倒序
  return Object.keys(groups).sort((a, b) => b.localeCompare(a)).map(y => ({ year: y, list: groups[y] }))
})

// PCU>300万判定:从 metrics 里任一 value 解析出"万"数值,超过300万则高亮
function isHotMetric(v) {
  const m = String(v || '').match(/([\d.]+)\s*万/)
  return m ? parseFloat(m[1]) > 300 : false
}

function isOpen(dim) { return openSet.value.has(dim) }
function toggle(dim) {
  const s = new Set(openSet.value)
  const opening = !s.has(dim)
  s.has(dim) ? s.delete(dim) : s.add(dim)
  openSet.value = s
  if (opening) refreshFeedback()   // 展开时拉取最新反馈(看到他人点赞/疑问)
}
function dimIcon(dim) { return TAG_TO_ICON[dim] || '📂' }

// —— 条目反馈(点赞/疑问) ——
function _fb(d, x) { return (d.feedback && d.feedback[x]) || { like: [], question: [] } }
function cnt(d, x, type) { return _fb(d, x)[type]?.length || 0 }
function who(d, x, type) {
  const arr = _fb(d, x)[type] || []
  if (!arr.length) return ''
  return arr.length <= 3 ? arr.join('、') : `${arr.slice(0, 3).join('、')} 等${arr.length}人`
}
function mine(d, x) {
  const f = _fb(d, x), me = myName.value
  if (f.like?.includes(me)) return 'like'
  if (f.question?.includes(me)) return 'question'
  return ''
}
async function vote(d, x, type) {
  if (!myName.value) { ElMessage.warning('请先登录'); return }
  try {
    const { data } = await api.post('/battlemap/feedback', { dimension: d.dimension, item_text: x, type })
    // 整体替换 feedback 对象，确保 Vue 响应式更新视图
    d.feedback = { ...(d.feedback || {}), [x]: data.feedback }
  } catch (e) {
    ElMessage.error('操作失败：' + (e.response?.data?.detail || e.message))
  }
}

// 刷新所有维度的反馈数据(看到他人最新点赞/疑问)
async function refreshFeedback() {
  try {
    const { data } = await api.get('/battlemap')
    const fbByDim = {}
    for (const dd of data.dimensions) fbByDim[dd.dimension] = dd.feedback || {}
    for (const d of dimensions.value) d.feedback = fbByDim[d.dimension] || {}
  } catch (e) { /* 静默 */ }
}

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
.bm-item { margin-bottom: 4px; }
.bm-item-row { display: flex; align-items: flex-start; gap: 8px; }
.bm-item-txt { flex: 1; }
.bm-fb { flex: 0 0 auto; display: inline-flex; gap: 4px; white-space: nowrap; }
.fb-btn {
  cursor: pointer; user-select: none; font-size: 13px; line-height: 1;
  padding: 2px 6px; border-radius: 10px; border: 1px solid transparent;
  display: inline-flex; align-items: center; gap: 3px; transition: background .15s, border-color .15s;
}
.fb-btn:hover { background: #eef4ff; }
.fb-btn.on { background: #e3edff; border-color: #9cc0ff; }
.fb-btn i { font-style: normal; font-size: 11px; color: #2f6bd6; font-weight: 600; }
.bm-fb-who { font-size: 11.5px; color: #5a7bb0; margin: 1px 0 0 2px; }
.bm-fb-who.q { color: #d97a1a; }

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
.bm-year-group { margin-top: 14px; }
.bm-year-group:first-child { margin-top: 12px; }
.bm-year-label { font-size: 15px; font-weight: 700; color: #2f80ff; padding: 4px 0 2px; border-bottom: 2px solid #e3ebf6; margin-bottom: 4px; }
.bm-timeline { margin-top: 8px; display: flex; flex-direction: column; gap: 8px; }
.bm-metric-hot { color: #e4393c; font-weight: 800; background: #ffecec; border-color: #ffc2c2; letter-spacing: 0.3px; }
.bm-metric-hot::before { content: "🔥"; margin-right: 3px; font-size: 12px; }
.bm-event { padding: 10px 14px; border-radius: 8px; background: #fafcff; border: 1px solid #e3ebf6; border-left: 3px solid #2f80ff; }
.bm-event-head { display: flex; align-items: baseline; gap: 10px; }
.bm-event-time { font-size: 12px; color: #5a7bb0; font-weight: 600; min-width: 64px; }
.bm-event-name { font-size: 14px; font-weight: 600; color: #1c2f5e; }
.bm-event-note { font-size: 12.5px; color: #5a6b85; line-height: 1.5; margin-top: 4px; }
.bm-event-metrics { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 6px; margin-top: 8px; }
.bm-metric-row { display: flex; gap: 8px; font-size: 12.5px; line-height: 1.5; }
.bm-metric-k { flex: 0 0 auto; color: #5a6b85; min-width: 72px; font-weight: 600; }
.bm-metric-v { color: #2c4a7c; display: inline-flex; align-items: center; padding: 0 8px; border: 1px solid transparent; border-radius: 5px; }
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
