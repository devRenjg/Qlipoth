<template>
  <div class="lc">
    <div class="lc-head">
      <div class="lc-title-row">
        <h2>📅 直播日历</h2>
        <el-button
          class="caliber-btn"
          type="warning"
          plain
          @click="caliberDialog = true"
          aria-label="查看 PCU 与 OTT PCU 的数据口径说明"
        >
          <span class="caliber-icon" aria-hidden="true">📊</span>
          数据口径说明
        </el-button>
        <el-tooltip v-if="isLoggedIn" :content="reportRefreshTooltip" placement="bottom">
          <span class="refresh-report-trigger" @click="refreshReports">
            <el-button
              class="refresh-report-btn"
              type="primary"
              :loading="refreshingReports"
              :disabled="refreshingReports || reportRefreshCooldownActive"
              :aria-label="reportRefreshAriaLabel"
            >
              <span v-if="!refreshingReports" class="refresh-icon" aria-hidden="true">🔄</span>
              {{ reportRefreshButtonText }}
            </el-button>
          </span>
        </el-tooltip>
      </div>
      <p class="sub">回看过去重要直播的实际 PCU，前瞻未来场次的预约热度</p>
      <div class="lc-toolbar">
        <el-radio-group v-model="viewMode" size="small">
          <el-radio-button label="month">月视图</el-radio-button>
          <el-radio-button label="week">周视图</el-radio-button>
        </el-radio-group>
        <div class="nav">
          <div class="nav-row nav-row-bottom">
            <el-select v-model="selectedYear" size="small" class="year-select"
                       placeholder="跳转年份" aria-label="选择年份快速跳转">
              <el-option v-for="y in yearOptions" :key="y" :label="y + ' 年'" :value="y" />
            </el-select>
            <el-button size="small" :disabled="!canGoPrev" @click="shift(-1)">‹ 上{{ viewMode==='month'?'月':'周' }}</el-button>
            <span class="cur-label">{{ rangeLabel }}</span>
            <el-button size="small" :disabled="!canGoNext" @click="shift(1)">下{{ viewMode==='month'?'月':'周' }} ›</el-button>
          </div>
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
                <span v-if="isDirty(s)" class="dirty-badge" title="脏数据:PCU疑似口径异常,不代表真实观看">⚠️</span>
                <span v-else-if="s.is_report" class="report-badge" title="重要直播活动报备场次">🛡</span>
                <span v-else-if="isMega(s)" class="mega-badge" title="百万级PCU 顶流场次">🔥</span>
                <span v-else-if="isVip(s)" class="vip-star" title="重点关注官号/高优">★</span>
                <span v-if="s.pcu==null && showStartTime(s)" class="sess-time">{{ hhmm(s.session_time) }}</span>{{ s.title }}<span v-if="isContFlow(s) || isReportMulti(s)" class="cont-flow-tag" title="持续流/跨天报备：标题为初始值，不代表当天实际内容">持续流</span>
              </div>
              <div class="sess-metric">
                <span v-if="s.anchor_name" class="anchor">{{ s.anchor_name }}</span>
                <span v-if="s.report_info?.creator" class="anchor">报备人 {{ s.report_info.creator }}<template v-if="s.report_info?.creator_first_dept">（{{ s.report_info.creator_first_dept }}）</template></span>
                <span v-if="s.pcu!=null && hasPeakTime(s)" class="peak-time">峰值 {{ hhmm(s.session_time) }}</span>
                <span v-if="s.pcu!=null" class="pcu" :class="{ 'pcu-dirty': isDirty(s) }">PCU {{ fmt(s.pcu) }}</span>
                <span v-if="s.ott_pcu!=null" class="ott-pcu">OTT PCU {{ fmt(s.ott_pcu) }}</span>
                <span v-if="isDirty(s)" class="dirty-tag" title="PCU疑似口径异常">⚠️脏数据</span>
                <span v-if="s.reservation!=null" class="rsv">预约 {{ fmt(s.reservation) }}</span>
                <span v-if="s.is_report" class="report-tag" :class="{ 'report-tag-pending': s.report_info?.order_type==='审批中' }" :title="reportTagTitle(s)">{{ s.report_info?.order_type==='审批中' ? '审批中' : '已报备' }}{{ s.report_info?.pcu_display ? '·预估PCU '+s.report_info.pcu_display : '' }}</span>
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
            <span v-if="isDirty(s)" class="dirty-badge" title="脏数据:PCU疑似口径异常">⚠️</span>
            <span v-else-if="isMega(s)" class="mega-badge" title="百万级PCU 顶流场次">🔥</span>
            <span v-else-if="isVip(s)" class="vip-star">★</span>
            <span class="dd-title">{{ s.title }}</span>
            <span v-if="isContFlow(s)" class="cont-flow-tag">持续流</span>
          </div>
          <div v-if="isDirty(s)" class="dd-dirty-hint">⚠️ 本场 PCU 为脏数据，疑似口径异常，不代表真实观看。{{ s.dirty_note }}</div>
          <div v-if="isContFlow(s)" class="cont-flow-hint">⚠️ 该直播间为长期持续流，此标题是开流时的初始标题，多天不变，不代表当天实际直播内容（如赛事每日对阵需另查赛程）。</div>
          <div class="dd-rows">
            <span v-if="s.anchor_name" class="dd-chip anchor">{{ s.anchor_name }}</span>
            <span v-if="s.pcu!=null && hasPeakTime(s)" class="dd-chip time">峰值 {{ hhmm(s.session_time) }}</span>
            <span v-else-if="s.pcu==null" class="dd-chip time">开播 {{ hhmm(s.session_time) }}</span>
            <span v-if="s.pcu!=null" class="dd-chip pcu" :class="{ 'pcu-dirty': isDirty(s) }">PCU {{ fmt(s.pcu) }}</span>
            <span v-if="s.ott_pcu!=null" class="dd-chip ott-pcu">OTT PCU {{ fmt(s.ott_pcu) }}</span>
            <span v-if="s.reservation!=null" class="dd-chip rsv">预约 {{ fmt(s.reservation) }}</span>
            <a v-if="s.room_id && s.room_url" class="dd-chip room room-link" :href="s.room_url" target="_blank" rel="noopener" @click.stop>房间 {{ s.room_id }} ↗</a>
            <span v-else-if="s.room_id" class="dd-chip room">房间 {{ s.room_id }}</span>
          </div>
        </div>
      </div>
    </el-dialog>

    <!-- PCU / OTT PCU 数据口径说明 -->
    <el-dialog v-model="caliberDialog" title="📊 数据口径说明" width="680px" top="7vh" class="caliber-dialog">
      <div class="cal-body">
        <p class="cal-intro">
          日历里过去场次的「PCU」取自数仓，受<strong>数据表保留期</strong>限制，不同<strong>开播日期</strong>会落到不同的源表，口径并不完全一致。跨时间段的 PCU 数值<strong>不宜直接比大小</strong>，解读时请先对应下面的分段。
        </p>

        <section class="cal-sec">
          <h4 class="cal-h">① 开播日期 ≥ 2026-04-01 — 长链在线人数峰值</h4>
          <ul class="cal-ul">
            <li>取自技术近实时长连表 <code>rods_s_broadcast_live_room_online_l_hr</code> 的<strong>在线人数峰值</strong>，时间显示为<strong>峰值时刻</strong>。</li>
            <li>该口径为直播长连（TCP 长连接）上报的房间在线数，业务核心口径为<strong>登录用户按 mid 去重 + 风控过滤</strong>后的在线人数。</li>
          </ul>
        </section>

        <section class="cal-sec">
          <h4 class="cal-h">② 2024-05 至 2026-03（2024-05-01 ≤ 开播日期 &lt; 2026-04-01）— 历史长链表</h4>
          <ul class="cal-ul">
            <li>rods 表仅保留近 3 个月，更早日期改用历史落表 <code>dwd_prty_lvup_broadcast_live_room_online_i_hr</code> 的 <code>logic_count_real</code> 峰值。</li>
            <li>其口径与①<strong>完全一致</strong>（登录 + mid 去重 + 风控过滤），换表只为拉长历史保留期，不改变口径含义。</li>
          </ul>
        </section>

        <section class="cal-sec">
          <h4 class="cal-h">③ 2024-05 之前（开播日期 &lt; 2024-05-01）— 老版弹幕连接数</h4>
          <ul class="cal-ul">
            <li>历史长链表最早只到 2024-05-01，更早的场次改用场次日表 <code>dwd_live_room_broadcast_session_d</code> 的 <code>danmu_num</code>（弹幕连接数）作为 PCU，取当天值，<strong>无峰值时刻</strong>。</li>
            <li>此口径<strong>不做登录/mid 去重、不过风控</strong>，是连接层的“粗”口径，数值口径与①②<strong>不可比</strong>，仅作历史趋势参考。</li>
          </ul>
        </section>

        <section class="cal-sec cal-ott">
          <h4 class="cal-h">④ OTT PCU（大屏 / TV 端）</h4>
          <ul class="cal-ul">
            <li>来自 OTT 数仓 <code>dwd_live_tv_pcu_detail_l_d</code> 的 <code>online</code>，是电视大屏端<strong>独立</strong>的在线人数上报体系。</li>
            <li>与 App / Web 主端<strong>不是同一个直播间</strong>（房间号不通用），也<strong>不与主端 PCU 打通</strong>。</li>
            <li>OTT PCU 与前三种主端 PCU <strong>相互独立、不可相加</strong>，需单独查看。</li>
          </ul>
        </section>

        <p class="cal-note">
          ⚠️ 分段是因为数仓表保留期不同（并非业务定义变更）；①② 口径一致、可比，③ 为老版粗口径、④ 为大屏独立口径，均<strong>不宜与①②直接比大小</strong>。
        </p>
      </div>
      <template #footer>
        <el-button type="primary" @click="caliberDialog = false">我知道了</el-button>
      </template>
    </el-dialog>

    <!-- 单场详情抽屉 -->
    <el-drawer v-model="drawer" :title="detail?.title || '场次详情'" size="380px" class="lc-drawer">
      <div v-if="detail" class="detail">
        <div v-if="isDirty(detail)" class="dirty-banner">
          <div class="db-head">⚠️ 本场 PCU 为脏数据，不代表真实观看</div>
          <div class="db-note">{{ detail.dirty_note || 'PCU 疑似口径异常，数据保留原值但仅供参考，请勿据此判断真实热度。' }}</div>
        </div>
        <div v-else-if="isVip(detail)" class="vip-banner">★ 重点关注直播场次</div>
        <div class="d-row" v-if="(detail.pcu!=null && hasPeakTime(detail)) || (detail.pcu==null && showStartTime(detail))"><span class="d-lbl">{{ detail.pcu!=null ? 'PCU 峰值' : '开播时间' }}</span><span>{{ detail.session_time }}</span></div>
        <div class="d-row" v-if="detail.anchor_name"><span class="d-lbl">主播</span><span>{{ detail.anchor_name }}</span></div>
        <div class="d-row" v-if="detail.pcu!=null"><span class="d-lbl">PCU</span><span :class="{ 'v-dirty': isDirty(detail) }">{{ fmt(detail.pcu) }}<i v-if="isDirty(detail)" class="v-dirty-note">（脏数据，疑似口径异常）</i></span></div>
        <div class="d-row" v-if="detail.ott_pcu!=null"><span class="d-lbl">OTT PCU</span><span class="v-ott">{{ fmt(detail.ott_pcu) }}</span></div>
        <div class="d-row" v-if="detail.reservation!=null"><span class="d-lbl">预约数</span><span>{{ fmt(detail.reservation) }}</span></div>
        <div class="d-metric" v-if="hasDual(detail.watch_hours_fans, detail.watch_hours_all)">
          <div class="m-title">累计观看时长</div>
          <div class="m-sub"><span class="m-k">粉版 App</span><span class="m-v">{{ ksep(detail.watch_hours_fans) }}<i>小时</i></span></div>
          <div class="m-sub"><span class="m-k">全端</span><span class="m-v">{{ ksep(detail.watch_hours_all) }}<i>小时</i></span></div>
        </div>
        <div class="d-metric" v-if="hasDual(detail.danmu_fans, detail.danmu_all)">
          <div class="m-title">累计弹幕数</div>
          <div class="m-sub"><span class="m-k">粉版 App</span><span class="m-v">{{ ksep(detail.danmu_fans) }}</span></div>
          <div class="m-sub"><span class="m-k">全端</span><span class="m-v">{{ ksep(detail.danmu_all) }}</span></div>
        </div>
        <div class="d-metric" v-if="hasDual(detail.enter_dau_fans, detail.enter_dau_all)">
          <div class="m-title">进房 DAU</div>
          <div class="m-sub"><span class="m-k">粉版 App</span><span class="m-v">{{ ksep(detail.enter_dau_fans) }}</span></div>
          <div class="m-sub"><span class="m-k">全端</span><span class="m-v">{{ ksep(detail.enter_dau_all) }}</span></div>
        </div>
        <div class="d-metric" v-if="hasDual(detail.fans_growth_fans, detail.fans_growth_all)">
          <div class="m-title">涨粉数</div>
          <div class="m-sub"><span class="m-k">粉版 App</span><span class="m-v">{{ ksep(detail.fans_growth_fans) }}</span></div>
          <div class="m-sub"><span class="m-k">全端</span><span class="m-v">{{ ksep(detail.fans_growth_all) }}</span></div>
        </div>
        <div class="d-row"><span class="d-lbl">直播间ID</span><span>{{ detail.room_id || '—' }}</span></div>

        <!-- 报备信息区(重要直播活动报备):指标区之下 -->
        <div v-if="detail.report_info" class="report-block">
          <div class="rb-head">🛡 报备信息 <span class="rb-sub">重要直播活动报备</span><span v-if="detail.report_info.order_type==='审批中'" class="rb-pending">审批中</span></div>
          <div v-if="detail.report_info.order_type==='审批中'" class="rb-stuck">
            <template v-if="detail.report_info.pending_actor">⏳ 当前卡在 <b>{{ detail.report_info.pending_actor }}</b> 的「{{ detail.report_info.pending_node }}」节点<template v-if="detail.report_info.pending_since">，到达于 {{ detail.report_info.pending_since }}</template></template>
            <template v-else>⏳ 审批流进行中，卡点在我方审批节点之外（如部门负责人/成本确认等前置节点）</template>
          </div>
          <div class="d-row"><span class="d-lbl">活动名称</span><span>{{ detail.report_info.name || '—' }}</span></div>
          <div class="d-row"><span class="d-lbl">报备时段</span><span>{{ detail.report_info.time_start }} ~ {{ detail.report_info.time_end }}</span></div>
          <div class="d-row"><span class="d-lbl">预估 PCU</span><span>{{ detail.report_info.pcu_display || detail.report_info.pcu || '—' }}<i class="rb-note">（报备原值 {{ detail.report_info.pcu }}，单位「万」）</i></span></div>
          <div class="d-row" v-if="detail.report_info.pcu_reason"><span class="d-lbl">预估依据</span><span class="rb-reason">{{ detail.report_info.pcu_reason }}</span></div>
          <div class="d-row"><span class="d-lbl">直播间</span><span>{{ detail.report_info.room_id || '—' }}</span></div>
          <div class="d-row" v-if="detail.report_info.live_type"><span class="d-lbl">直播类型</span><span>{{ detail.report_info.live_type }}</span></div>
          <div class="d-row" v-if="detail.report_info.live_input_type"><span class="d-lbl">推拉流类型</span><span>{{ detail.report_info.live_input_type }}</span></div>
          <div class="d-row" v-if="detail.report_info.room_bw"><span class="d-lbl">码率/帧率</span><span>{{ detail.report_info.room_bw }}<template v-if="detail.report_info.room_fps"> / {{ detail.report_info.room_fps }}fps</template></span></div>
          <div class="d-row" v-if="detail.report_info.need_sungong"><span class="d-lbl">孙工团队</span><span>{{ detail.report_info.need_sungong }}</span></div>
          <div class="d-row" v-if="detail.report_info.need_4k"><span class="d-lbl">4K</span><span>{{ detail.report_info.need_4k }}</span></div>
          <div class="d-row" v-if="detail.report_info.need_hdr"><span class="d-lbl">HDR</span><span>{{ detail.report_info.need_hdr }}</span></div>
          <div class="d-row" v-if="detail.report_info.real_origin"><span class="d-lbl">真原画露出</span><span>{{ detail.report_info.real_origin }}</span></div>
          <div class="d-row" v-if="detail.report_info.default_origin"><span class="d-lbl">默认原画清晰度</span><span>{{ detail.report_info.default_origin }}</span></div>
          <div class="d-row"><span class="d-lbl">报备人</span><span>{{ detail.report_info.creator || '—' }}</span></div>
          <div class="d-row" v-if="detail.report_info.creator_dept"><span class="d-lbl">所属部门</span><span>{{ detail.report_info.creator_dept }}</span></div>
          <div class="d-row" v-if="isAdmin && detail.report_info.order_id && SHENPI_URL">
            <span class="d-lbl">审批单据</span>
            <a class="rb-link" :href="SHENPI_URL + detail.report_info.order_id" target="_blank" rel="noopener">打开原单据校准 ↗</a>
          </div>
        </div>

        <el-button type="primary" :disabled="!detail.room_url" @click="enterRoom" style="margin-top:16px;width:100%">
          进直播间
        </el-button>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount, inject } from 'vue'
import axios from 'axios'
import { ElMessage } from 'element-plus'

const currentUser = inject('currentUser', null)
const isAdmin = computed(() => currentUser?.value?.role === 'admin')
// 访客(未登录)不允许刷新报备场次,按钮仅登录用户可见
const isLoggedIn = computed(() => !!currentUser?.value?.id)
// 审批单据链接(仅管理员可见,用于人工校准报备数据)。
// 内部域名走环境变量脱敏,公开仓库不含真实地址;本地 .env.local 配 VITE_SHENPI_URL 即可。
const SHENPI_URL = import.meta.env.VITE_SHENPI_URL || ''

const api = axios.create({ baseURL: '/api' })
const weekNames = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
const viewMode = ref('month')
const anchor = ref(new Date())   // 当前视图锚点日期
const sessions = ref([])
const loading = ref(false)
const refreshingReports = ref(false)
const reportRefreshCooldown = ref(0)
const reportRefreshCooldownActive = computed(() => reportRefreshCooldown.value > 0)
const reportRefreshButtonText = computed(() => reportRefreshCooldownActive.value && !refreshingReports.value
  ? `仅刷新报备场次（${reportRefreshCooldown.value}s）`
  : '仅刷新报备场次')
const reportRefreshTooltip = computed(() => reportRefreshCooldownActive.value
  ? `冷却中，还需 ${reportRefreshCooldown.value} 秒。刷新今天及之后 10 天的报备场次`
  : '刷新今天及之后 10 天的报备场次，不会刷新其他场次')
const reportRefreshAriaLabel = computed(() => {
  if (refreshingReports.value) return '正在刷新报备场次，请稍候'
  if (reportRefreshCooldownActive.value) return `刷新报备场次冷却中，还需 ${reportRefreshCooldown.value} 秒`
  return '刷新今天及之后 10 天的报备场次'
})
const REPORT_REFRESH_COOLDOWN_MS = 30_000
let reportRefreshUnmounted = false
let reportRefreshCooldownDeadline = 0
let reportRefreshCooldownTimer = null
const drawer = ref(false)
const detail = ref(null)
const dayDialog = ref(false)
const dayCell = ref(null)
const caliberDialog = ref(false)
const dayTitle = computed(() => {
  if (!dayCell.value) return ''
  const n = dayCell.value.sessions.length
  return `${dayCell.value.date}  ·  共 ${n} 场`
})

const fmt = (n) => n == null ? '' : (n >= 10000 ? (n / 10000).toFixed(1) + 'w' : String(n))
const hhmm = (t) => (t || '').slice(11, 16)   // 'YYYY-MM-DD HH:MM:SS' → 'HH:MM'
// 是否有真实的 PCU 峰值时刻:2024-05-01 前用场次日表 danmu_num 作 PCU,该表无峰值时刻,
// 时间统一兜底成 00:00:00(占位假值)。此类场次不展示峰值时间,避免"峰值 00:00"误导。
// 判据:session_time 存在且时分秒非 00:00:00。
const hasPeakTime = (s) => {
  const hms = (s.session_time || '').slice(11, 19)
  return !!hms && hms !== '00:00:00'
}
// 千分位;空值显示 —。unit 追加单位(如 ' 小时')
const ksep = (n, unit = '') => n == null ? '—' : Math.round(n).toLocaleString() + unit
// 某维度是否有任一口径值(粉版或全端),决定该行是否显示
const hasDual = (f, a) => f != null || a != null

// 脏数据:后端标记的口径异常场次(PCU值保留不洗,仅前端显著标记提示)。
// 优先级最高——脏数据一律走告警样式,压过 mega/vip 所有高亮,避免脏值被当顶流误导。
const isDirty = (s) => !!(s.is_dirty)
// 报备标签 hover 文案:审批中的单显示"当前卡在XX的XX节点"
const reportTagTitle = (s) => {
  const ri = s.report_info
  if (!ri) return ''
  if (ri.order_type !== '审批中') return '已报备'
  if (ri.pending_actor) {
    return `审批中 · 当前卡在 ${ri.pending_actor} 的「${ri.pending_node}」节点` + (ri.pending_since ? `（到达于 ${ri.pending_since}）` : '')
  }
  return '审批中 · 卡点在我方审批节点之外（如部门负责人/成本确认等前置节点）'
}
// 重点关注官号/高优Up白名单(主播名精确匹配)→ 特殊标识
const VIP_ANCHORS = ['哔哩哔哩弹幕网', '哔哩哔哩直播', '影视飓风', '哔哩哔哩英雄联盟赛事', '哔哩哔哩晚会', '央视新闻']
const isVip = (s) => !isDirty(s) && VIP_ANCHORS.includes((s.anchor_name || '').trim())
// 百万级 PCU 场次:最高优先级高亮(超过白名单官号)。脏数据不参与(否则脏的56万会被当顶流)
const isMega = (s) => !isDirty(s) && s.pcu != null && s.pcu >= 1000000
// 跨天报备:报备时段横跨多天,当天标"持续流"(与持续流逻辑一致)
const isReportMulti = (s) => {
  if (!s.is_report || !s.report_info) return false
  const st = (s.report_info.time_start || '').slice(0, 10)
  const et = (s.report_info.time_end || '').slice(0, 10)
  return st && et && st !== et
}
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
// 持续流/跨天场次的首日日期(用于"仅首日显示开播时间")
const contFlowFirstDay = computed(() => {
  const first = {}  // key → 最早日期
  for (const s of sessions.value) {
    if (!s.room_id || !s.title || s.pcu == null) continue
    const k = s.room_id + '||' + s.title
    const d = (s.session_time || '').slice(0, 10)
    if (!first[k] || d < first[k]) first[k] = d
  }
  return first
})
// 是否显示开播时间:跨天的持续流/报备仅首日显示,后续天不显示(不是每天开播)
function showStartTime(s) {
  const day = (s.session_time || '').slice(0, 10)
  // 跨天报备:仅首日(==report_info.time_start日)显示
  if (s.is_report && s.report_info) {
    const st = (s.report_info.time_start || '').slice(0, 10)
    const et = (s.report_info.time_end || '').slice(0, 10)
    if (st && et && st !== et) return day === st
    return true
  }
  // 持续流:仅视图内最早那天显示
  if (isContFlow(s)) {
    return day === contFlowFirstDay.value[s.room_id + '||' + s.title]
  }
  return true
}
// 场次视觉分级 class:dirty(脏数据,告警灰底,最高优先) > mega(百万PCU) > vip(白名单官号) > 普通
const sessClass = (s) => [
  s.pcu != null ? 'past' : 'future',
  isDirty(s) ? 'dirty' : ((s.is_report || s.report_info) ? 'report' : (isMega(s) ? 'mega' : (isVip(s) ? 'vip' : ''))),
]
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

// 数据下界:最早可信数据 2020-09(2020-08及更早无有效数据、不会再补),再往前不允许翻页/跳转
const DATA_FLOOR = new Date(2020, 8, 1)   // 2020-09-01(月份从0计,8=9月)
// 数据上界:当前日期的下个月(未来仅约7天预约,翻到下月足够;再往后无内容)。取下月1号所在月。
const _now = new Date()
const DATA_CEIL = new Date(_now.getFullYear(), _now.getMonth() + 1, 1)   // 下个月1号
// 年份快速跳转:范围覆盖已有数据(2020 起)至当前年+1(容纳未来预约场次)
const DATA_START_YEAR = 2020
const yearOptions = computed(() => {
  const end = new Date().getFullYear() + 1
  const out = []
  for (let y = end; y >= DATA_START_YEAR; y--) out.push(y)
  return out
})
// 可写计算属性:读取=当前锚点年份;写入=保持当前月/日、仅切换年份跳转
const selectedYear = computed({
  get: () => anchor.value.getFullYear(),
  set: (y) => {
    if (y == null || y === anchor.value.getFullYear()) return
    const a = new Date(anchor.value)
    a.setFullYear(y)   // 保留当前所选月份与日期,仅改年份
    // 越界钳制:早于下界→2020-09;晚于上界(下个月)→钳到下月
    if (a < DATA_FLOOR) { anchor.value = new Date(DATA_FLOOR); return }
    if (a > DATA_CEIL) { anchor.value = new Date(DATA_CEIL); return }
    anchor.value = a
  },
})

const MIN_SHOW = 10000  // 展示门槛:过去PCU<1w、未来预约<1w 默认不展示(数据照常存,仅前端精简)

// 组织某天要展示的场次:过滤低量 + 报备优先 + 同直播间合并(报备挂到指标/预约场次上)
function buildDaySessions(key) {
  const all = sessions.value.filter(s => (s.session_time || '').slice(0, 10) === key)
  const reports = all.filter(s => s.is_report)
  const normal = all.filter(s => !s.is_report)
  // 合并:同 room_id 的报备信息挂到普通场次上;未被合并的报备单独成条
  const usedReport = new Set()
  const merged = normal.map(s => {
    if (!s.report_info) {
      const rep = reports.find(r => r.room_id && r.room_id === s.room_id && !usedReport.has(r.id))
      if (rep) { usedReport.add(rep.id); return { ...s, report_info: rep.report_info, is_report: rep.is_report } }
    }
    return s
  })
  for (const r of reports) if (!usedReport.has(r.id)) merged.push(r)
  // 过滤:报备场次始终展示;其余按规则过滤
  const kept = merged.filter(s => {
    if (s.is_report || s.report_info) return true
    // 过去场次(有PCU):PCU<=0(数据源无有效流水) 或 空标题(源表无标题) 不展示——脏/残缺数据不上日历
    if (s.pcu != null) {
      if (s.pcu <= 0) return false
      if (!s.title || !s.title.trim()) return false
      return s.pcu >= MIN_SHOW          // 低量门槛:过去PCU<1w 不展示
    }
    if (s.reservation != null) return s.reservation >= MIN_SHOW   // 未来纯预约<1w 不展示
    return true
  })
  // 排序:报备优先 → PCU降序 → 预约降序
  const rank = (s) => (s.is_report || s.report_info) ? 3 : (s.pcu != null ? 2 : 1)
  const metric = (s) => (s.pcu != null ? s.pcu : (s.reservation != null ? s.reservation : -1))
  return kept.sort((a, b) => rank(b) - rank(a) || metric(b) - metric(a) || (a.session_time || '').localeCompare(b.session_time || ''))
}

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
    const daySess = buildDaySessions(key)
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

async function load({ preserveOnError = false } = {}) {
  loading.value = true
  try {
    const { start, end } = viewRange()
    const { data } = await api.get('/live-calendar/sessions', { params: { start: ymd(start), end: ymd(end) } })
    sessions.value = data
    return true
  } catch (e) {
    if (!preserveOnError) sessions.value = []
    return false
  } finally { loading.value = false }
}

function refreshErrorMessage(error) {
  const status = error?.response?.status
  const rawDetail = error?.response?.data?.detail || error?.response?.data?.message
  const detail = typeof rawDetail === 'string' ? rawDetail.trim() : ''
  if (status === 409) return detail || '报备刷新正在进行中，请稍候再试'
  if (status === 503) return detail ? `当前环境不支持报备刷新：${detail}` : '当前环境不支持报备刷新'
  if (status === 502) return detail || '外部审批中台拉取异常，请稍后重试'
  if (detail) return detail
  return error?.message || '请求失败，请稍后重试'
}

function showReportRefreshError(error) {
  const status = error?.response?.status
  const message = refreshErrorMessage(error)
  if (status === 409) {
    ElMessage.warning({ message, duration: 4000 })
  } else if (status === 503) {
    ElMessage.error(message)
  } else {
    ElMessage.error(`刷新报备场次失败：${message}`)
  }
}

function clearReportRefreshCooldownTimer() {
  if (reportRefreshCooldownTimer !== null) {
    window.clearTimeout(reportRefreshCooldownTimer)
    reportRefreshCooldownTimer = null
  }
}

function updateReportRefreshCooldown() {
  reportRefreshCooldownTimer = null
  if (reportRefreshUnmounted) return

  const remainingMs = reportRefreshCooldownDeadline - Date.now()
  if (remainingMs <= 0) {
    reportRefreshCooldown.value = 0
    reportRefreshCooldownDeadline = 0
    return
  }

  reportRefreshCooldown.value = Math.ceil(remainingMs / 1000)
  reportRefreshCooldownTimer = window.setTimeout(updateReportRefreshCooldown, Math.min(1000, remainingMs))
}

function startReportRefreshCooldown() {
  clearReportRefreshCooldownTimer()
  reportRefreshCooldownDeadline = Date.now() + REPORT_REFRESH_COOLDOWN_MS
  reportRefreshCooldown.value = REPORT_REFRESH_COOLDOWN_MS / 1000
  reportRefreshCooldownTimer = window.setTimeout(updateReportRefreshCooldown, 1000)
}

async function refreshReports() {
  if (reportRefreshCooldownActive.value) {
    ElMessage.warning('您刷新太快了！')
    return
  }
  if (refreshingReports.value) return
  startReportRefreshCooldown()
  refreshingReports.value = true
  try {
    const { data = {} } = await api.post('/live-calendar/refresh-reports')
    if (reportRefreshUnmounted) return
    const hasValidStats = ['total', 'updated', 'failed'].every(key => Number.isFinite(data[key]))
    if (data.ok !== true || !hasValidStats) {
      throw new Error('刷新接口返回异常，请稍后重试')
    }

    const reloaded = await load({ preserveOnError: true })
    if (reportRefreshUnmounted) return

    // 基于真实变化(新增/变更/删除)反馈,而非"回写条数"(每次全量重写,回写数≈窗口总数,无法感知变化)
    const c = data.changes || {}
    const hasChangeStats = ['added', 'changed', 'removed'].every(key => Number.isFinite(c[key]))
    let resultMessage
    let noChange = false
    if (hasChangeStats) {
      const parts = []
      if (c.added > 0) parts.push(`新增 ${c.added} 条`)
      if (c.changed > 0) parts.push(`更新 ${c.changed} 条`)
      if (c.removed > 0) parts.push(`移除 ${c.removed} 条`)
      if (parts.length) {
        resultMessage = `报备场次已刷新：${parts.join('，')}（命中 ${data.total} 单据${data.failed > 0 ? `，${data.failed} 单据详情拉取失败` : ''}）`
      } else {
        noChange = true
        resultMessage = `报备数据已是最新，无新增更新（命中 ${data.total} 单据${data.failed > 0 ? `，${data.failed} 单据详情拉取失败` : ''}）`
      }
    } else {
      // 兜底:后端未返回 changes 时回退旧文案
      resultMessage = `已刷新 ${data.updated} 条报备场次（命中 ${data.total} 单据${data.failed > 0 ? `，${data.failed} 单据详情拉取失败` : ''}）`
    }
    if (!reloaded) {
      ElMessage.warning({ message: `${resultMessage}，但日历视图重新加载失败，请稍后重试`, duration: 5000 })
    } else if (data.failed > 0) {
      ElMessage.warning({ message: resultMessage, duration: 5000 })
    } else if (noChange) {
      ElMessage.info({ message: resultMessage, duration: 4000 })
    } else {
      ElMessage.success({ message: resultMessage, duration: 4000 })
    }
  } catch (error) {
    if (!reportRefreshUnmounted) showReportRefreshError(error)
  } finally {
    if (!reportRefreshUnmounted) refreshingReports.value = false
  }
}

function shift(n) {
  const a = new Date(anchor.value)
  if (viewMode.value === 'month') a.setMonth(a.getMonth() + n)
  else a.setDate(a.getDate() + n * 7)
  // 下界保护:向前(n<0)不早于数据下界;上界保护:向后(n>0)不晚于当前月的下个月。越界则忽略
  if (n < 0 && !canGoPrevTo(a)) return
  if (n > 0 && !canGoNextTo(a)) return
  anchor.value = a
}
// 目标锚点对应的视图是否仍触及 >=DATA_FLOOR 的数据(月视图看当月末、周视图看周末)
function canGoPrevTo(a) {
  if (viewMode.value === 'month') {
    const lastDay = new Date(a.getFullYear(), a.getMonth() + 1, 0)
    return lastDay >= DATA_FLOOR
  }
  const d = new Date(a); const dow = (d.getDay() + 6) % 7
  const weekEnd = new Date(d); weekEnd.setDate(d.getDate() - dow + 6)
  return weekEnd >= DATA_FLOOR
}
// 目标锚点是否未超出上界 DATA_CEIL(月视图看当月1号<=下月、周视图看周一<=下月)
function canGoNextTo(a) {
  if (viewMode.value === 'month') {
    const firstDay = new Date(a.getFullYear(), a.getMonth(), 1)
    return firstDay <= DATA_CEIL
  }
  const d = new Date(a); const dow = (d.getDay() + 6) % 7
  const weekStart = new Date(d); weekStart.setDate(d.getDate() - dow)
  return weekStart <= DATA_CEIL
}
// 上月/上周按钮是否可点(当前锚点再往前一档是否越下界)
const canGoPrev = computed(() => {
  const a = new Date(anchor.value)
  if (viewMode.value === 'month') a.setMonth(a.getMonth() - 1)
  else a.setDate(a.getDate() - 7)
  return canGoPrevTo(a)
})
// 下月/下周按钮是否可点(当前锚点再往后一档是否越上界)
const canGoNext = computed(() => {
  const a = new Date(anchor.value)
  if (viewMode.value === 'month') a.setMonth(a.getMonth() + 1)
  else a.setDate(a.getDate() + 7)
  return canGoNextTo(a)
})
function openDetail(s) { detail.value = s; drawer.value = true }
function openDay(cell) { dayCell.value = cell; dayDialog.value = true }
function enterRoom() { if (detail.value?.room_url) window.open(detail.value.room_url, '_blank') }

watch([viewMode, anchor], () => load())
onMounted(() => {
  reportRefreshUnmounted = false
  load()
})
onBeforeUnmount(() => {
  reportRefreshUnmounted = true
  clearReportRefreshCooldownTimer()
  reportRefreshCooldownDeadline = 0
  reportRefreshCooldown.value = 0
  refreshingReports.value = false
})
</script>

<!-- 非 scoped:el-drawer 被 teleport 到 body,scoped :deep 匹配不到,必须用全局样式压缩标题↔正文间距 -->
<style>
.lc-drawer .el-drawer__header { margin-bottom: 4px !important; padding-bottom: 0 !important; }
.lc-drawer .el-drawer__body { padding-top: 4px !important; }
</style>

<style scoped>
.lc { max-width: 100%; margin: 0 auto; padding: 8px 8px 40px; box-sizing: border-box; overflow-x: hidden; }
.lc-title-row { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin-bottom: 4px; }
.lc-head h2 { margin: 0; font-size: 22px; color: #1a2b4a; }
.refresh-report-trigger { display: inline-flex; flex-shrink: 0; margin-left: auto; }
.refresh-report-trigger :deep(.el-button.is-disabled) { pointer-events: none; }
.refresh-report-btn { font-weight: 700; box-shadow: 0 3px 10px rgba(47, 107, 214, .28); }
.refresh-icon { margin-right: 6px; }
.caliber-btn { font-weight: 700; box-shadow: 0 3px 10px rgba(230, 162, 60, .22); }
.caliber-icon { margin-right: 6px; }
/* 数据口径说明弹窗 */
.caliber-dialog .cal-body { font-size: 14px; color: #2f3f5c; line-height: 1.7; }
.caliber-dialog .cal-intro { margin: 0 0 16px; padding: 10px 12px; background: #fff8ec; border-left: 3px solid #e6a23c; border-radius: 4px; color: #7a5a12; }
.caliber-dialog .cal-sec { margin-bottom: 16px; }
.caliber-dialog .cal-h { margin: 0 0 8px; font-size: 15px; color: #1a2b4a; }
.caliber-dialog .cal-ott .cal-h { color: #8250c4; }
.caliber-dialog .cal-ul { margin: 0; padding-left: 20px; }
.caliber-dialog .cal-ul li { margin-bottom: 5px; }
.caliber-dialog .cal-ul strong { color: #c0140a; font-weight: 700; margin: 0 3px; }
.caliber-dialog .cal-ott .cal-ul strong { color: #8250c4; }
.caliber-dialog .cal-todo { color: #909399; font-style: italic; }
.caliber-dialog .cal-todo strong { color: #909399; }
.caliber-dialog .cal-note { margin: 4px 0 0; padding: 10px 12px; background: #f4f5f7; border-radius: 4px; font-size: 13px; color: #606266; }
.caliber-dialog code { padding: 1px 5px; background: #eef1f6; border: 1px solid #dfe4ec; border-radius: 3px; font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace; font-size: 12px; color: #476582; word-break: break-all; }
.lc-head .sub { color: #6b7a90; font-size: 13px; margin: 0 0 14px; }
.lc-toolbar { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; margin-bottom: 14px; }
.nav { display: inline-flex; flex-direction: column; align-items: stretch; gap: 8px; }
.nav-row { display: flex; align-items: center; gap: 8px; }
.cur-label { font-weight: 600; color: #2f4368; min-width: 120px; text-align: center; }
/* 年份筛选:放在"上月"左侧同一行,大小与字体和上月/下月按钮完全一致(白底灰边、常规字重#606266) */
.year-select { width: 88px; }
.year-select :deep(.el-select__wrapper) {
  background: #fff;
  box-shadow: 0 0 0 1px #dcdfe6 inset;
}
.year-select :deep(.el-select__wrapper:hover) {
  box-shadow: 0 0 0 1px #c0c4cc inset;
}
.year-select :deep(.el-select__wrapper.is-focused) {
  box-shadow: 0 0 0 1px #2f6bd6 inset;
}
.year-select :deep(.el-select__placeholder) { color: #606266; }
/* 选中的年份数字:加粗加深(700+深蓝),明显突出 */
.year-select :deep(.el-select__selected-item) { color: #2f4368; font-weight: 700; }
.year-select :deep(.el-select__caret) { color: #909399; }
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
.sess-metric .ott-pcu { color: #b3701a; font-weight: 600; }
.sess-metric .rsv { color: #2f9e5e; font-weight: 700; font-size: 14px; }
/* 白名单官号(次级高优):淡化处理——明显高于普通、但低于百万级mega */
.sess.vip { border-left:4px solid #f0a852 !important; background:linear-gradient(135deg,#fff8ec,#fff1d8) !important; box-shadow:0 0 0 1px #f3c98a inset; }
.sess.vip .sess-title { font-size:13px; font-weight:600; color:#b5722a; }
.sess.vip .anchor { color:#c07a28 !important; font-weight:600; }
.sess.vip .pcu, .sess.vip .ott-pcu, .sess.vip .rsv { font-weight:600; }
/* 报备场次:蓝底突出(优先级最高的视觉强调) */
.sess.report { border-left:4px solid #2f6bd6 !important; background:linear-gradient(135deg,#eaf2ff,#d6e6ff) !important; box-shadow:0 0 0 1px #a8c6f0 inset; }
.sess.report .sess-title { font-weight:700; color:#1e4fa3; }
.sess.report .anchor { color:#2f6bd6 !important; font-weight:600; }
.sess.report .report-badge { font-size:14px; }
.sess-metric .report-tag { color:#fff; background:#2f6bd6; border:none; border-radius:3px; padding:1px 6px; font-size:11px; font-weight:600; }
.sess-metric .report-tag-pending { background:#e8952f; }
.rb-pending { display:inline-block; margin-left:8px; color:#fff; background:#e8952f; border-radius:3px; padding:1px 7px; font-size:12px; font-weight:600; }
.vip-star { color:#e8952f; font-weight:900; font-size:14px; margin-right:3px; }
/* 百万级PCU(最高优先):最强高亮——红金渐变+发光边框+加粗放大 */
.sess.mega { border-left:5px solid #e01f1f !important; background:linear-gradient(135deg,#ffe08a,#ff9d5c,#ff6b6b) !important; box-shadow:0 0 0 2px #ff3b3b inset, 0 0 12px rgba(255,60,60,.55); }
.sess.mega .sess-title { font-size:14px; font-weight:800; color:#7a1010; }
.sess.mega .anchor { color:#a01515 !important; font-weight:800; }
.sess.mega .pcu, .sess.mega .ott-pcu { color:#c0140a !important; font-weight:900; }
.mega-badge { font-size:15px; margin-right:3px; filter:drop-shadow(0 0 3px rgba(255,80,0,.6)); }
/* 持续流标注:格子内小灰标签 + 弹窗内提示行 */
.cont-flow-tag { display:inline-block; margin-left:5px; font-size:10px; font-weight:600; color:#8a7500; background:#fff5cc; border:1px solid #ecd98a; border-radius:6px; padding:0 5px; vertical-align:middle; white-space:nowrap; }
.cont-flow-hint { margin-top:6px; font-size:12px; color:#9a7b1a; background:#fffbe8; border-left:3px solid #ecc94b; border-radius:4px; padding:6px 10px; line-height:1.5; }
.vip-banner { background:linear-gradient(90deg,#ff7a18,#ffb020); color:#fff; border:none; border-radius:8px; padding:11px 14px; font-weight:800; margin-bottom:14px; font-size:15px; letter-spacing:1px; box-shadow:0 2px 10px rgba(232,82,15,.35); }
/* 脏数据(方案A):告警灰底+红边,压过一切高亮;PCU删除线;红色⚠️脏数据标签 */
.sess.dirty { border-left:4px solid #c0392b !important; background:repeating-linear-gradient(45deg,#f7f2f2,#f7f2f2 8px,#f0e6e6 8px,#f0e6e6 16px) !important; box-shadow:0 0 0 1px #e0b4b0 inset; }
.sess.dirty .sess-title { color:#7a5a58; font-weight:600; }
.sess.dirty .anchor { color:#9a7876 !important; }
.sess-metric .pcu.pcu-dirty { color:#a0392b !important; text-decoration:line-through; text-decoration-color:#c0392b; opacity:.75; font-weight:600; }
.dirty-badge { margin-right:3px; font-size:13px; }
.dirty-tag { color:#fff; background:#c0392b; border-radius:3px; padding:1px 6px; font-size:11px; font-weight:700; }
/* 详情抽屉:脏数据红色警示条 */
.detail .dirty-banner { background:linear-gradient(90deg,#e74c3c,#c0392b); color:#fff; border-radius:8px; padding:11px 14px; margin-bottom:14px; box-shadow:0 2px 10px rgba(192,57,43,.35); }
.detail .dirty-banner .db-head { font-weight:800; font-size:15px; letter-spacing:.5px; }
.detail .dirty-banner .db-note { font-size:12.5px; font-weight:400; line-height:1.55; margin-top:6px; opacity:.95; }
.detail .v-dirty { color:#c0392b; text-decoration:line-through; text-decoration-color:#c0392b; }
.detail .v-dirty-note { font-style:normal; color:#c0392b; font-size:12px; margin-left:6px; text-decoration:none; font-weight:600; }
.detail .d-lbl-wide { width:auto; min-width:80px; white-space:nowrap; margin-right:8px; }
.detail .v-ott { color:inherit; font-weight:inherit; }
.detail .v-ott-note { font-style:normal; color:#909399; font-size:11px; margin-left:6px; font-weight:400; }
/* 当天弹窗:脏数据提示行 + PCU删除线 + 灰底 */
.dd-sess.dirty { border-left-color:#c0392b; background:#faf5f5; box-shadow:0 0 0 1px #e0b4b0 inset; }
.dd-dirty-hint { margin-top:6px; margin-bottom:2px; font-size:12px; color:#a03228; background:#fdeceb; border-left:3px solid #c0392b; border-radius:4px; padding:6px 10px; line-height:1.5; }
.dd-chip.pcu.pcu-dirty { text-decoration:line-through; text-decoration-color:#c0392b; color:#a0392b; opacity:.8; }
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
.dd-chip.ott-pcu { background: #fdf1e2; color: #b3701a; }
.dd-chip.rsv { background: #eaf7ef; color: #2f9e5e; }
.dd-chip.room { background: #f4f4f5; color: #707684; }
.dd-chip.room-link { cursor: pointer; text-decoration: none; transition: all .15s; }
.dd-chip.room-link:hover { background: #e0edff; color: #2f6bd6; }
.detail .d-row { display: flex; padding: 8px 0; border-bottom: 1px solid #f0f2f5; font-size: 14px; }
.detail .d-lbl { width: 80px; color: #909399; flex-shrink: 0; }
.detail .d-dual { flex: 1; text-align: right; color: #303133; }
/* 双口径指标:标题行 + 粉版/全端两条明细,数值紧跟标签 */
.detail .d-metric { padding: 8px 0; border-bottom: 1px solid #f0f2f5; }
.detail .m-title { font-size: 14px; color: #909399; margin-bottom: 4px; }
.detail .m-sub { display: flex; align-items: baseline; padding: 2px 0; font-size: 14px; }
.detail .m-k { color: #909399; width: 80px; flex-shrink: 0; }
.detail .m-v { color: #303133; font-weight: 600; font-variant-numeric: tabular-nums; }
.detail .m-v i { font-style: normal; font-weight: 400; color: #c0c4cc; font-size: 12px; margin-left: 3px; }
/* 报备标记(格子内) */
.report-badge { margin-right: 2px; }
/* 报备信息区(详情弹窗) */
.detail .report-block { margin-top: 14px; padding: 10px 12px; background: #fffbf0; border: 1px solid #ffe7ba; border-radius: 6px; }
.detail .rb-head { font-size: 14px; font-weight: 600; color: #d48806; margin-bottom: 8px; }
.detail .rb-head .rb-sub { font-size: 12px; font-weight: 400; color: #b8935a; margin-left: 4px; }
.detail .rb-stuck { background:#fff7e6; border:1px solid #ffd591; border-radius:4px; padding:6px 10px; margin-bottom:8px; font-size:12px; color:#ad6800; line-height:1.5; }
.detail .rb-stuck b { color:#d46b08; }
.detail .rb-note { font-style: normal; color: #c0c4cc; font-size: 12px; margin-left: 4px; }
.detail .rb-reason { text-align: left; color: #606266; font-size: 13px; line-height: 1.5; }
.detail .rb-link { color: #2f6bd6; text-decoration: none; font-weight: 600; }
.detail .rb-link:hover { text-decoration: underline; }
.detail .report-block .d-row { border-bottom: 1px solid #f7ecd6; }
</style>
