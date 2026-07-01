<template>
  <div class="lc">
    <div class="lc-head">
      <h2>📅 直播日历</h2>
      <p class="sub">回看过去重要直播的实际 PCU，前瞻未来场次的预约热度</p>
      <div class="lc-toolbar">
        <el-radio-group v-model="viewMode" size="small">
          <el-radio-button label="month">月视图</el-radio-button>
          <el-radio-button label="week">周视图</el-radio-button>
        </el-radio-group>
        <div class="nav">
          <el-button size="small" @click="shift(-1)">‹ 上{{ viewMode==='month'?'月':'周' }}</el-button>
          <span class="cur-label">{{ rangeLabel }}</span>
          <el-button size="small" @click="shift(1)">下{{ viewMode==='month'?'月':'周' }} ›</el-button>
          <el-button size="small" @click="goToday">今天</el-button>
        </div>
      </div>
    </div>

    <div v-loading="loading" class="cal-grid" :class="viewMode">
      <div class="weekday" v-for="w in weekNames" :key="w">{{ w }}</div>
      <template v-for="(cell, i) in cells" :key="i">
        <div v-if="cell.blank" class="day-cell blank"></div>
        <div v-else class="day-cell"
             :class="{ 'is-today': cell.isToday, 'has-sess': cell.sessions.length }"
             @click="openDay(cell)">
          <div class="day-num">{{ cell.day }}</div>
          <div class="sess-list">
            <div v-for="s in cell.sessions" :key="s.id" class="sess"
                 :class="sessClass(s)"
                 @click.stop="openDetail(s)">
              <div class="sess-title">
                <span v-if="isMega(s)" class="mega-badge" title="百万级PCU 顶流场次">🔥</span>
                <span v-else-if="isVip(s)" class="vip-star" title="重点关注官号/高优">★</span>
                <span v-if="s.pcu==null" class="sess-time">{{ hhmm(s.session_time) }}</span>{{ s.title }}<span v-if="isContFlow(s)" class="cont-flow-tag" title="长期持续流：标题为开流初始值，不代表当天实际内容">持续流</span>
              </div>
              <div class="sess-metric">
                <span v-if="s.anchor_name" class="anchor">{{ s.anchor_name }}</span>
                <span v-if="s.pcu!=null" class="peak-time">峰值 {{ hhmm(s.session_time) }}</span>
                <span v-if="s.pcu!=null" class="pcu">PCU {{ fmt(s.pcu) }}</span>
                <span v-if="s.reservation!=null" class="rsv">预约 {{ fmt(s.reservation) }}</span>
              </div>
            </div>
          </div>
        </div>
      </template>
    </div>

    <!-- 当天全部场次弹窗 -->
    <el-dialog v-model="dayDialog" :title="dayTitle" width="640px" top="8vh" class="day-dialog">
      <div v-if="dayCell" class="day-detail">
        <el-empty v-if="!dayCell.sessions.length" description="当天暂无直播数据" />
        <div v-for="s in dayCell.sessions" :key="s.id" class="dd-sess"
             :class="sessClass(s)">
          <div class="dd-top">
            <span v-if="isMega(s)" class="mega-badge" title="百万级PCU 顶流场次">🔥</span>
            <span v-else-if="isVip(s)" class="vip-star">★</span>
            <span class="dd-title">{{ s.title }}</span>
            <span v-if="isContFlow(s)" class="cont-flow-tag">持续流</span>
          </div>
          <div v-if="isContFlow(s)" class="cont-flow-hint">⚠️ 该直播间为长期持续流，此标题是开流时的初始标题，多天不变，不代表当天实际直播内容（如赛事每日对阵需另查赛程）。</div>
          <div class="dd-rows">
            <span v-if="s.anchor_name" class="dd-chip anchor">{{ s.anchor_name }}</span>
            <span v-if="s.pcu!=null" class="dd-chip time">峰值 {{ hhmm(s.session_time) }}</span>
            <span v-else class="dd-chip time">开播 {{ hhmm(s.session_time) }}</span>
            <span v-if="s.pcu!=null" class="dd-chip pcu">PCU {{ fmt(s.pcu) }}</span>
            <span v-if="s.reservation!=null" class="dd-chip rsv">预约 {{ fmt(s.reservation) }}</span>
            <a v-if="s.room_id && s.room_url" class="dd-chip room room-link" :href="s.room_url" target="_blank" rel="noopener" @click.stop>房间 {{ s.room_id }} ↗</a>
            <span v-else-if="s.room_id" class="dd-chip room">房间 {{ s.room_id }}</span>
          </div>
        </div>
      </div>
    </el-dialog>

    <!-- 单场详情抽屉 -->
    <el-drawer v-model="drawer" :title="detail?.title || '场次详情'" size="380px">
      <div v-if="detail" class="detail">
        <div v-if="isVip(detail)" class="vip-banner">★ 重点关注直播场次</div>
        <div class="d-row"><span class="d-lbl">{{ detail.pcu!=null ? 'PCU峰值时间' : '开播时间' }}</span><span>{{ detail.session_time }}</span></div>
        <div class="d-row"><span class="d-lbl">主播</span><span>{{ detail.anchor_name || '—' }}</span></div>
        <div class="d-row" v-if="detail.pcu!=null"><span class="d-lbl">PCU</span><span>{{ fmt(detail.pcu) }}</span></div>
        <div class="d-row" v-if="detail.reservation!=null"><span class="d-lbl">预约数</span><span>{{ fmt(detail.reservation) }}</span></div>
        <div class="d-row"><span class="d-lbl">直播间ID</span><span>{{ detail.room_id || '—' }}</span></div>
        <el-button type="primary" :disabled="!detail.room_url" @click="enterRoom" style="margin-top:16px;width:100%">
          进直播间
        </el-button>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import axios from 'axios'

const api = axios.create({ baseURL: '/api' })
const weekNames = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
const viewMode = ref('month')
const anchor = ref(new Date())   // 当前视图锚点日期
const sessions = ref([])
const loading = ref(false)
const drawer = ref(false)
const detail = ref(null)
const dayDialog = ref(false)
const dayCell = ref(null)
const dayTitle = computed(() => {
  if (!dayCell.value) return ''
  const n = dayCell.value.sessions.length
  return `${dayCell.value.date}  ·  共 ${n} 场`
})

const fmt = (n) => n == null ? '' : (n >= 10000 ? (n / 10000).toFixed(1) + 'w' : String(n))
const hhmm = (t) => (t || '').slice(11, 16)   // 'YYYY-MM-DD HH:MM:SS' → 'HH:MM'

// 重点关注官号/高优Up白名单(主播名精确匹配)→ 特殊标识
const VIP_ANCHORS = ['哔哩哔哩弹幕网', '哔哩哔哩直播', '影视飓风', '哔哩哔哩英雄联盟赛事', '哔哩哔哩晚会', '央视新闻']
const isVip = (s) => VIP_ANCHORS.includes((s.anchor_name || '').trim())
// 百万级 PCU 场次:最高优先级高亮(超过白名单官号)
const isMega = (s) => s.pcu != null && s.pcu >= 1000000
// 持续流识别:同一直播间(room_id)+同一标题 在当前视图内跨>=3天出现,判为长期持续流——
// 标题是开流时的初始值、不随每天实际内容(如S赛每日对阵)更新,提示用户勿把标题当当天内容。
// (阈值取3天:2天多为跨夜直播或同标题巧合,3天以上才明显是长期挂流)
const contFlowKeys = computed(() => {
  const dayset = {}  // key: room_id||title  → Set(日期)
  for (const s of sessions.value) {
    if (!s.room_id || !s.title || s.pcu == null) continue
    const k = s.room_id + '||' + s.title
    ;(dayset[k] || (dayset[k] = new Set())).add((s.session_time || '').slice(0, 10))
  }
  const keys = new Set()
  for (const k in dayset) if (dayset[k].size >= 3) keys.add(k)   // >=3天同room+title 才判持续流(2天多为跨夜/巧合,避免误标)
  return keys
})
const isContFlow = (s) => !!s.room_id && !!s.title && contFlowKeys.value.has(s.room_id + '||' + s.title)
// 场次视觉分级 class:mega(百万PCU,最亮) > vip(白名单官号,次亮) > 普通
const sessClass = (s) => [s.pcu != null ? 'past' : 'future', isMega(s) ? 'mega' : (isVip(s) ? 'vip' : '')]
const ymd = (d) => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`
const isSameDay = (a, b) => a.getFullYear()===b.getFullYear() && a.getMonth()===b.getMonth() && a.getDate()===b.getDate()

// 视图起止(周一为周首)
function viewRange() {
  const a = new Date(anchor.value)
  if (viewMode.value === 'week') {
    const dow = (a.getDay() + 6) % 7  // 周一=0
    const start = new Date(a); start.setDate(a.getDate() - dow)
    const end = new Date(start); end.setDate(start.getDate() + 6)
    return { start, end }
  }
  // month: 含首尾补全的整周
  const first = new Date(a.getFullYear(), a.getMonth(), 1)
  const last = new Date(a.getFullYear(), a.getMonth()+1, 0)
  const start = new Date(first); start.setDate(first.getDate() - ((first.getDay()+6)%7))
  const end = new Date(last); end.setDate(last.getDate() + (6-((last.getDay()+6)%7)))
  return { start, end }
}

const rangeLabel = computed(() => {
  const a = anchor.value
  if (viewMode.value === 'month') return `${a.getFullYear()}年${a.getMonth()+1}月`
  const { start, end } = viewRange()
  return `${ymd(start)} ~ ${ymd(end)}`
})

const cells = computed(() => {
  const { start, end } = viewRange()
  const today = new Date()
  const mAnchor = anchor.value.getMonth()
  const out = []
  const d = new Date(start)
  while (d <= end) {
    const key = ymd(d)
    const inMonth = viewMode.value === 'week' ? true : d.getMonth() === mAnchor
    // 月视图下非当月的格子：留空占位(不显示日期/场次)
    if (!inMonth) {
      out.push({ blank: true, date: key })
      d.setDate(d.getDate() + 1)
      continue
    }
    // 排序：有PCU的(过去/已开播)按PCU降序；纯预约的(未来)按预约量降序
    const metric = (s) => (s.pcu != null ? s.pcu : (s.reservation != null ? s.reservation : -1))
    const daySess = sessions.value.filter(s => (s.session_time || '').slice(0,10) === key)
                                  .sort((a,b)=> metric(b) - metric(a) || (a.session_time||'').localeCompare(b.session_time||''))
    out.push({
      day: d.getDate(),
      date: key,
      isToday: isSameDay(d, today),
      inRange: true,
      sessions: daySess,
    })
    d.setDate(d.getDate() + 1)
  }
  return out
})

async function load() {
  loading.value = true
  try {
    const { start, end } = viewRange()
    const { data } = await api.get('/live-calendar/sessions', { params: { start: ymd(start), end: ymd(end) } })
    sessions.value = data
  } catch (e) { sessions.value = [] } finally { loading.value = false }
}

function shift(n) {
  const a = new Date(anchor.value)
  if (viewMode.value === 'month') a.setMonth(a.getMonth() + n)
  else a.setDate(a.getDate() + n * 7)
  anchor.value = a
}
function goToday() { anchor.value = new Date() }
function openDetail(s) { detail.value = s; drawer.value = true }
function openDay(cell) { dayCell.value = cell; dayDialog.value = true }
function enterRoom() { if (detail.value?.room_url) window.open(detail.value.room_url, '_blank') }

watch([viewMode, anchor], load)
onMounted(load)
</script>

<style scoped>
.lc { max-width: 100%; margin: 0 auto; padding: 8px 8px 40px; box-sizing: border-box; overflow-x: hidden; }
.lc-head h2 { margin: 0 0 4px; font-size: 22px; color: #1a2b4a; }
.lc-head .sub { color: #6b7a90; font-size: 13px; margin: 0 0 14px; }
.lc-toolbar { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; margin-bottom: 14px; }
.nav { display: inline-flex; align-items: center; gap: 8px; }
.cur-label { font-weight: 600; color: #2f4368; min-width: 120px; text-align: center; }
.cal-grid { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 6px; width: 100%; box-sizing: border-box; }
.weekday { text-align: center; font-size: 14px; color: #8a96a8; padding: 6px 0; font-weight: 600; }
.day-cell { min-height: 150px; min-width: 0; box-sizing: border-box; overflow: visible; border: 1px solid #e3e8f0; border-radius: 8px; padding: 6px 8px; background: #fff; display: flex; flex-direction: column; cursor: pointer; transition: box-shadow .15s; }
.day-cell:not(.blank):hover { box-shadow: 0 2px 12px rgba(47,107,214,.18); border-color: #b9cdf0; }
.cal-grid.week .day-cell { min-height: 460px; }
.day-cell.blank { background: transparent; border: 1px dashed #eef1f6; cursor: default; }
.day-cell.is-today { border-color: #2f6bd6; box-shadow: 0 0 0 1px #2f6bd6 inset; }
.day-cell.has-sess { background: linear-gradient(180deg,#f5f9ff,#fff); }
.day-num { font-size: 14px; color: #909399; margin-bottom: 4px; font-weight: 600; flex: 0 0 auto; }
.day-cell.is-today .day-num { color: #2f6bd6; font-weight: 700; }
.sess-list { display: flex; flex-direction: column; gap: 4px; overflow: visible; min-width: 0; }
.sess { cursor: pointer; border-radius: 6px; padding: 5px 7px; font-size: 13px; border-left: 4px solid #c0c4cc; background: #f4f6fa; min-width: 0; overflow: hidden; }
.sess:hover { background: #e9f0fb; }
.sess.past { border-left-color: #e8a33d; }
.sess.future { border-left-color: #2f9e5e; }
.sess-title { color: #2c3a52; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-size: 13px; }
.sess-metric { display: flex; gap: 8px; margin-top: 3px; flex-wrap: wrap; font-size: 12px; min-width: 0; }
.sess-metric > span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
.sess-time { color:#2f6bd6; font-weight:600; margin-right:4px; }
.sess-metric .anchor { color:#7a6ad0; }

.sess-metric .peak-time { color:#2f6bd6; font-weight:600; }
.sess-metric .pcu { color: #b3701a; font-weight: 600; }
.sess-metric .rsv { color: #2f9e5e; font-weight: 600; }
/* 白名单官号(次级高优):淡化处理——明显高于普通、但低于百万级mega */
.sess.vip { border-left:4px solid #f0a852 !important; background:linear-gradient(135deg,#fff8ec,#fff1d8) !important; box-shadow:0 0 0 1px #f3c98a inset; }
.sess.vip .sess-title { font-size:13px; font-weight:600; color:#b5722a; }
.sess.vip .anchor { color:#c07a28 !important; font-weight:600; }
.sess.vip .pcu, .sess.vip .rsv { font-weight:600; }
.vip-star { color:#e8952f; font-weight:900; font-size:14px; margin-right:3px; }
/* 百万级PCU(最高优先):最强高亮——红金渐变+发光边框+加粗放大 */
.sess.mega { border-left:5px solid #e01f1f !important; background:linear-gradient(135deg,#ffe08a,#ff9d5c,#ff6b6b) !important; box-shadow:0 0 0 2px #ff3b3b inset, 0 0 12px rgba(255,60,60,.55); }
.sess.mega .sess-title { font-size:14px; font-weight:800; color:#7a1010; }
.sess.mega .anchor { color:#a01515 !important; font-weight:800; }
.sess.mega .pcu { color:#c0140a !important; font-weight:900; }
.mega-badge { font-size:15px; margin-right:3px; filter:drop-shadow(0 0 3px rgba(255,80,0,.6)); }
/* 持续流标注:格子内小灰标签 + 弹窗内提示行 */
.cont-flow-tag { display:inline-block; margin-left:5px; font-size:10px; font-weight:600; color:#8a7500; background:#fff5cc; border:1px solid #ecd98a; border-radius:6px; padding:0 5px; vertical-align:middle; white-space:nowrap; }
.cont-flow-hint { margin-top:6px; font-size:12px; color:#9a7b1a; background:#fffbe8; border-left:3px solid #ecc94b; border-radius:4px; padding:6px 10px; line-height:1.5; }
.vip-banner { background:linear-gradient(90deg,#ff7a18,#ffb020); color:#fff; border:none; border-radius:8px; padding:11px 14px; font-weight:800; margin-bottom:14px; font-size:15px; letter-spacing:1px; box-shadow:0 2px 10px rgba(232,82,15,.35); }
/* 当天全部场次弹窗 */
.day-detail { display: flex; flex-direction: column; gap: 10px; max-height: 70vh; overflow-y: auto; }
.dd-sess { border: 1px solid #e3e8f0; border-left: 4px solid #c0c4cc; border-radius: 8px; padding: 12px 14px; background: #fafbfd; }
.dd-sess.past { border-left-color: #e8a33d; }
.dd-sess.future { border-left-color: #2f9e5e; }
.dd-sess.vip { border-left-color: #f0a852; background: linear-gradient(135deg,#fff8ec,#fff); box-shadow: 0 0 0 1px #f3c98a inset; }
.dd-sess.mega { border-left-color: #e01f1f; border-left-width: 5px; background: linear-gradient(135deg,#fff0d0,#ffe0dc,#fff); box-shadow: 0 0 0 1px #ff5b5b inset, 0 0 10px rgba(255,60,60,.4); }
.dd-top { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; }
.dd-title { font-weight: 700; font-size: 15px; color: #1a2b4a; }
.dd-sess.vip .dd-title { color: #b5722a; }
.dd-sess.mega .dd-title { color: #7a1010; font-weight: 800; }
.dd-rows { display: flex; flex-wrap: wrap; gap: 8px; }
.dd-chip { font-size: 12.5px; padding: 2px 10px; border-radius: 12px; font-weight: 600; }
.dd-chip.anchor { background: #f0edfb; color: #7a6ad0; }
.dd-chip.time { background: #eaf1fc; color: #2f6bd6; }
.dd-chip.pcu { background: #fdf1e2; color: #b3701a; }
.dd-chip.rsv { background: #eaf7ef; color: #2f9e5e; }
.dd-chip.room { background: #f4f4f5; color: #707684; }
.dd-chip.room-link { cursor: pointer; text-decoration: none; transition: all .15s; }
.dd-chip.room-link:hover { background: #e0edff; color: #2f6bd6; }
.detail .d-row { display: flex; padding: 8px 0; border-bottom: 1px solid #f0f2f5; font-size: 14px; }
.detail .d-lbl { width: 80px; color: #909399; }
</style>
