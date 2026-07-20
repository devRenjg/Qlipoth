<template>
  <div class="checklist-page">
    <!-- 引导：优先看作战地图（尤其新负责人） -->
    <router-link to="/battlemap" class="bm-banner">
      <span class="bm-banner-icon">🗺️</span>
      <span class="bm-banner-text">
        <b>第一次参与保障？建议先看「作战地图」</b>
        <em>快速了解各方向涉及哪些系统、历史踩过哪些大坑、哪里水深——再回来看具体清单</em>
      </span>
      <span class="bm-banner-go">查看作战地图 →</span>
    </router-link>

    <!-- 顶部：生成新清单 -->
    <div class="gen-bar">
      <h2>保障清单 · 历史踩坑预警</h2>
      <p class="gen-sub">选活动类型，从历史复盘文档中提炼上届踩过的坑，按保障六维度生成备战清单。</p>
      <div class="gen-controls">
        <el-select v-model="genActivity" placeholder="选择大型活动" style="width: 180px">
          <el-option v-for="a in availableActivities" :key="a" :label="a" :value="a" />
        </el-select>
        <el-button type="primary" :loading="generating" :disabled="!genActivity" @click="doGenerate">
          {{ generating ? `生成中 ${genProcessed}/${genTotal}` : '生成清单' }}
        </el-button>
        <span v-if="generating" class="gen-hint">扫描复盘文档中，约需几分钟，可离开页面稍后回来看</span>
        <span v-else-if="!availableActivities.length" class="gen-hint">你已为所有活动各生成过一份清单，如需重新生成请先删除自己对应的清单</span>
      </div>
    </div>

    <!-- 已有清单列表 -->
    <div class="cl-list" v-if="!activeChecklist">
      <el-empty v-if="!checklists.length && !loading" description="还没有保障清单，选活动生成一份吧" />
      <div v-for="c in checklists" :key="c.id" class="cl-card" @click="openChecklist(c.id)">
        <div class="cl-card-head">
          <span class="cl-act" :style="actStyle(c.activity)">{{ c.activity }}</span>
          <span class="cl-title">{{ c.title }}</span>
          <span v-if="c.status === 'generating'" class="cl-gen">生成中…</span>
        </div>
        <div class="cl-card-meta">
          <span>{{ c.item_count }} 条踩坑</span>
          <span>已处理 {{ c.handled_count }}/{{ c.item_count }}</span>
          <span>源自保障知识库</span>
          <span v-if="c.created_by">生成者：{{ c.created_by }}</span>
          <span class="cl-time">{{ c.created_at }}</span>
          <el-button v-if="canEditList(c)" size="small" type="danger" text @click.stop="removeChecklist(c.id)">删除</el-button>
          <span v-else class="cl-readonly">仅查看</span>
        </div>
      </div>
    </div>

    <!-- 清单详情：按六维度分组 -->
    <div class="cl-detail" v-if="activeChecklist">
      <!-- 第一行：返回 + 标题 + 进度 -->
      <div class="detail-head">
        <el-button text @click="closeChecklist">← 返回列表</el-button>
        <span class="cl-act" :style="actStyle(activeChecklist.checklist.activity)">{{ activeChecklist.checklist.activity }}</span>
        <span class="detail-title">{{ activeChecklist.checklist.title }}</span>
        <span v-if="activeChecklist.checklist.created_by" class="detail-by">生成者：{{ activeChecklist.checklist.created_by }}</span>
        <span class="detail-prog">已处理 {{ activeChecklist.handled_count }}/{{ activeChecklist.item_count }}</span>
      </div>
      <!-- 第二行：筛选(左) + 操作(右) -->
      <div class="detail-toolbar">
        <div class="toolbar-left">
          <el-radio-group v-model="filterMode" size="small">
            <el-radio-button value="all">全部</el-radio-button>
            <el-radio-button value="todo">未完成</el-radio-button>
            <el-radio-button value="done">已完成</el-radio-button>
          </el-radio-group>
          <el-radio-group v-model="sevFilter" size="small">
            <el-radio-button value="p0">只看P0</el-radio-button>
            <el-radio-button value="p01">P0+P1</el-radio-button>
            <el-radio-button value="all">全部</el-radio-button>
          </el-radio-group>
        </div>
        <div class="toolbar-right">
          <el-button size="small" text @click="toggleAll">{{ allCollapsed ? '全部展开' : '全部折叠' }}</el-button>
          <el-button v-if="canEditActive" size="small" :type="selectMode ? 'warning' : 'success'" plain @click="toggleSelectMode">
            {{ selectMode ? '退出选择' : '选择导出' }}
          </el-button>
          <span v-else class="readonly-tag" title="该清单由他人生成，你只能查看">👁 仅查看（{{ activeChecklist.checklist.created_by }} 生成）</span>
        </div>
      </div>
      <!-- 选择模式工具条 -->
      <div class="export-bar" v-if="selectMode">
        <span>已选 {{ selectedIds.length }} 条</span>
        <el-button size="small" @click="selectAllVisible">全选(当前筛选)</el-button>
        <el-button size="small" @click="clearSelection">取消全选</el-button>
        <el-button size="small" type="primary" :loading="exporting" :disabled="!selectedIds.length" @click="doExport">
          {{ exporting ? '正在生成企微文档…' : `导出选中(${selectedIds.length})到企微文档` }}
        </el-button>
        <span class="export-hint">将生成一个企业微信在线文档（含可勾选 Checklist）</span>
      </div>
      <!-- 导出结果 -->
      <div class="export-result" v-if="exportResult">
        ✅ 已导出 {{ exportResult.count }} 条
        <template v-if="exportResult.doc_count > 1">（内容较多，自动拆成 {{ exportResult.doc_count }} 个文档）</template>
        <span v-for="(d, i) in (exportResult.docs || [{ url: exportResult.url, title: exportResult.title }])" :key="i">
          → <a :href="d.url" target="_blank" rel="noopener">{{ exportResult.doc_count > 1 ? `文档${i+1}` : '打开企业微信文档' }}</a>
        </span>
        <el-button size="small" text @click="copyExportUrl">复制{{ exportResult.doc_count > 1 ? '首个' : '' }}链接</el-button>
      </div>
      <el-collapse v-model="openDims" class="cl-collapse">
        <el-collapse-item v-for="dim in activeChecklist.dimensions" :key="dim" :name="dim"
                          :class="{ 'is-incident': dim === '事故/故障' }"
                          v-show="visibleItems(dim).length || (activeChecklist.grouped[dim] || []).length">
          <template #title>
            <span class="dim-title" :class="{ 'dim-incident': dim === '事故/故障' }">{{ dim === '事故/故障' ? '⚠ ' + dim : dim }}</span>
            <el-button v-if="canEditActive" size="small" text @click.stop="startAdd(dim)">+ 加一条</el-button>
          </template>
          <div v-for="it in visibleItems(dim)" :key="it.id"
               class="item-card" :class="{ handled: it.handled, 'item-selected': selectMode && selectedIds.includes(it.id) }">
            <el-checkbox v-if="selectMode" :model-value="selectedIds.includes(it.id)" @change="toggleSelect(it)" class="item-check" />
            <el-checkbox v-else :model-value="!!it.handled" :disabled="!canEditActive" @change="toggleHandled(it)" class="item-check" />
            <div class="item-body">
              <div class="item-tags">
                <span class="sev-tag" :class="'sev-' + (it.severity || 'P2')">{{ it.severity || 'P2' }}</span>
                <span class="daylate-tag" v-if="it.day_late" title="该问题在活动当天发生，且本可以在备战前期/压测阶段提前规避，请负责人务必重视、重点前置处理">⚠ 当天发生·务必重视</span>
                <span class="stage-tag" v-if="it.stage">{{ it.stage }}</span>
                <span class="cross-tag" v-if="it.cross_from">借鉴自 {{ it.cross_from }}</span>
                <span class="own-tag" v-if="it.team || it.owner">
                  {{ it.team }}<template v-if="it.team && it.owner"> · </template>{{ it.owner }}
                </span>
                <span class="own-tag own-empty" v-else-if="canEditActive" @click="startEdit(it)">+ 标注归属</span>
              </div>
              <div class="item-row"><b>现象</b>{{ it.phenomenon || '—' }}</div>
              <div class="item-row" v-if="it.cause"><b>原因</b>{{ it.cause }}</div>
              <div class="item-row" v-if="it.handling"><b>当时处置</b>{{ it.handling }}</div>
              <div class="item-row sugg" v-if="it.suggestion"><b>建议</b>{{ it.suggestion }}</div>
              <div class="item-row timing" v-if="it.timing"><b>时点</b>{{ it.timing }}</div>
              <div class="item-foot">
                <a class="item-src" v-if="it.source_url" :href="it.source_url" target="_blank" @click.stop>来源：{{ it.source_files }}</a>
                <span class="item-src" v-else-if="it.source_files">来源：{{ it.source_files }}</span>
                <span class="item-handled" v-if="it.handled && it.handled_by">✓ {{ it.handled_by }} {{ it.handled_at }}</span>
                <el-button v-if="canEditActive" size="small" text @click="startEdit(it)">编辑</el-button>
                <el-button v-if="canEditActive" size="small" text type="danger" @click="removeItem(it)">删除</el-button>
              </div>
            </div>
          </div>
        </el-collapse-item>
      </el-collapse>
    </div>

    <!-- 编辑/新增条目弹窗 -->
    <el-dialog v-model="editVisible" :title="editing.id ? '编辑条目' : '新增条目'" width="560px">
      <el-form label-width="80px">
        <el-form-item label="维度">
          <el-select v-model="editing.dimension" style="width: 200px">
            <el-option v-for="d in dimensions" :key="d" :label="d" :value="d" />
          </el-select>
        </el-form-item>
        <el-form-item label="严重度">
          <el-select v-model="editing.severity" style="width: 200px">
            <el-option v-for="s in ['P0','P1','P2']" :key="s" :label="s" :value="s" />
          </el-select>
        </el-form-item>
        <el-form-item label="保障阶段">
          <el-select v-model="editing.stage" style="width: 200px">
            <el-option v-for="s in stages" :key="s" :label="s" :value="s" />
          </el-select>
        </el-form-item>
        <el-form-item label="负责团队"><el-input v-model="editing.team" placeholder="不确定可留空" /></el-form-item>
        <el-form-item label="负责人"><el-input v-model="editing.owner" placeholder="不确定可留空" /></el-form-item>
        <el-form-item label="现象"><el-input v-model="editing.phenomenon" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="原因"><el-input v-model="editing.cause" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="当时处置"><el-input v-model="editing.handling" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="建议"><el-input v-model="editing.suggestion" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="时点"><el-input v-model="editing.timing" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" @click="saveEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>
<script setup>
import { ref, computed, onMounted, onUnmounted, inject } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  generateChecklist, getChecklistProgress, listChecklists, getChecklist,
  updateChecklistItem, addChecklistItem, deleteChecklistItem, deleteChecklist,
  exportChecklistToWecom,
} from '../api/index.js'
import { colorForTag } from '../utils/tagColor.js'
import { DIMENSION_LABELS } from '../dimensions.js'

const currentUser = inject('currentUser')
// 写权限：仅清单生成者本人可改（admin 也不例外）；无生成者(历史清单)放开给所有登录用户。
// 内网 SSO 访客(is_guest)非正式登录,写操作后端会 401,前端同步禁止编辑避免点击失败。
function canEditList(cl) {
  if (!cl) return false
  const me = currentUser?.value
  if (!me || me.is_guest) return false
  const owner = cl.created_by || ''
  return !owner || owner === me.username
}
const canEditActive = computed(() => canEditList(activeChecklist.value?.checklist))

// 跨晚生成暂不开放，先聚焦 S赛/春晚（已有跨晚清单仍可查看）
const activities = ['S赛', '春晚']
// 同一用户每个活动只能有一份清单：已生成过的活动从下拉中隐藏（需先删除自己的清单才能重新生成）
const availableActivities = computed(() => {
  const cu = currentUser?.value
  // 内网 SSO 访客非正式登录,不能生成清单(后端写操作会 401),不给生成选项
  if (cu?.is_guest) return []
  const me = cu?.username
  if (!me) return activities
  const mine = new Set(checklists.value.filter(c => c.created_by === me).map(c => c.activity))
  return activities.filter(a => !mine.has(a))
})
const dimensions = DIMENSION_LABELS
const stages = ['备战前期', '压测演练', '上线前', '活动当天', '活动后', '未分类']
const genActivity = ref('')
const generating = ref(false)
const genProcessed = ref(0)
const genTotal = ref(0)
const checklists = ref([])
const loading = ref(false)
const activeChecklist = ref(null)
const filterMode = ref('all')
const sevFilter = ref('p01')  // p0 / p01(默认,只看P0+P1) / all
const openDims = ref([])
let pollTimer = null

const allCollapsed = computed(() => openDims.value.length === 0)

function visibleItems(dim) {
  let items = (activeChecklist.value?.grouped[dim]) || []
  if (filterMode.value === 'todo') items = items.filter(i => !i.handled)
  else if (filterMode.value === 'done') items = items.filter(i => i.handled)
  if (sevFilter.value === 'p0') items = items.filter(i => i.severity === 'P0')
  else if (sevFilter.value === 'p01') items = items.filter(i => i.severity === 'P0' || i.severity === 'P1')
  return items
}

// ── 选择导出到企微文档 ──
const selectMode = ref(false)
const selectedIds = ref([])         // 选中的条目 id
const exporting = ref(false)
const exportResult = ref(null)      // { url, count, title }

function allVisibleItems() {
  const dims = activeChecklist.value?.dimensions || []
  return dims.flatMap(d => visibleItems(d))
}
function toggleSelectMode() {
  selectMode.value = !selectMode.value
  if (!selectMode.value) selectedIds.value = []
}
function toggleSelect(it) {
  const i = selectedIds.value.indexOf(it.id)
  if (i >= 0) selectedIds.value.splice(i, 1)
  else selectedIds.value.push(it.id)
}
function selectAllVisible() {
  selectedIds.value = allVisibleItems().map(i => i.id)
}
function clearSelection() {
  selectedIds.value = []
}
async function doExport() {
  if (!selectedIds.value.length) { ElMessage.warning('请先选择要导出的条目'); return }
  exporting.value = true
  exportResult.value = null
  try {
    const { data } = await exportChecklistToWecom(activeChecklist.value.checklist.id, selectedIds.value)
    exportResult.value = data
    ElMessage.success(`已导出 ${data.count} 条到企业微信文档`)
  } catch (e) {
    const msg = e?.response?.data?.detail || e.message || '导出失败'
    ElMessage.error(msg)
  } finally {
    exporting.value = false
  }
}
function copyExportUrl() {
  const url = exportResult.value?.url
  if (!url) return
  try {
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(url)
    else { const ta = document.createElement('textarea'); ta.value = url; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); document.body.removeChild(ta) }
    ElMessage.success('链接已复制')
  } catch { ElMessage.warning('复制失败，请手动复制') }
}

function toggleAll() {
  if (openDims.value.length) openDims.value = []
  else openDims.value = [...(activeChecklist.value?.dimensions || [])]
}

function actStyle(name) {
  const c = colorForTag(name)
  return { background: c, color: '#fff' }
}

async function loadList() {
  loading.value = true
  try {
    const { data } = await listChecklists()
    checklists.value = data
  } catch (e) { ElMessage.error('加载清单列表失败') }
  finally { loading.value = false }
}

async function doGenerate() {
  generating.value = true
  genProcessed.value = 0
  genTotal.value = 0
  try {
    const { data } = await generateChecklist(genActivity.value)
    pollProgress(data.id)
  } catch (e) {
    generating.value = false
    ElMessage.error(e.response?.data?.detail || '生成失败')
  }
}

function pollProgress(id) {
  pollTimer = setInterval(async () => {
    try {
      const { data } = await getChecklistProgress(id)
      genProcessed.value = data.processed || 0
      genTotal.value = data.total || 0
      if (data.status === 'done') {
        clearInterval(pollTimer)
        generating.value = false
        ElMessage.success(`生成完成，共 ${data.item_count} 条踩坑`)
        await loadList()
        openChecklist(id)
      } else if (data.status === 'error') {
        clearInterval(pollTimer)
        generating.value = false
        ElMessage.error('生成出错：' + (data.error || ''))
      }
    } catch (e) { /* 轮询容错 */ }
  }, 2000)
}

async function openChecklist(id) {
  try {
    const { data } = await getChecklist(id)
    activeChecklist.value = data
    // 默认全部折叠收拢，用户点哪个展开哪个（与作战地图一致）
    openDims.value = []
  } catch (e) { ElMessage.error('打开清单失败') }
}

function closeChecklist() {
  activeChecklist.value = null
  loadList()
}

async function removeChecklist(id) {
  try {
    await ElMessageBox.confirm('确认删除这份清单？', '提示', { type: 'warning' })
    await deleteChecklist(id)
    ElMessage.success('已删除')
    loadList()
  } catch (e) { if (e !== 'cancel') ElMessage.error('删除失败') }
}

async function toggleHandled(it) {
  const next = it.handled ? 0 : 1
  try {
    await updateChecklistItem(it.id, { handled: !!next })
    it.handled = next
    if (!next) { it.handled_by = ''; it.handled_at = '' }
    activeChecklist.value.handled_count += next ? 1 : -1
    // 拉取最新勾选追溯(谁/何时)
    if (next) { const { data } = await getChecklist(activeChecklist.value.checklist.id);
      const fresh = Object.values(data.grouped).flat().find(x => x.id === it.id)
      if (fresh) { it.handled_by = fresh.handled_by; it.handled_at = fresh.handled_at } }
  } catch (e) { ElMessage.error('更新失败') }
}

const editVisible = ref(false)
const editing = ref({})

function startEdit(it) {
  editing.value = { ...it }
  editVisible.value = true
}
function startAdd(dim) {
  editing.value = { dimension: dim, severity: 'P2', stage: '未分类', team: '', owner: '', phenomenon: '', cause: '', handling: '', suggestion: '', timing: '' }
  editVisible.value = true
}

async function saveEdit() {
  const e = editing.value
  try {
    if (e.id) {
      await updateChecklistItem(e.id, {
        dimension: e.dimension, severity: e.severity, stage: e.stage, team: e.team, owner: e.owner,
        phenomenon: e.phenomenon, cause: e.cause,
        handling: e.handling, suggestion: e.suggestion, timing: e.timing,
      })
    } else {
      await addChecklistItem(activeChecklist.value.checklist.id, e)
    }
    editVisible.value = false
    await openChecklist(activeChecklist.value.checklist.id)
    ElMessage.success('已保存')
  } catch (err) { ElMessage.error('保存失败') }
}

async function removeItem(it) {
  try {
    await deleteChecklistItem(it.id)
    await openChecklist(activeChecklist.value.checklist.id)
  } catch (e) { ElMessage.error('删除失败') }
}

onMounted(loadList)
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer) })
</script>
<style scoped>
.checklist-page { max-width: 960px; margin: 0 auto; padding: 24px 20px 60px; }
.bm-banner {
  display: flex; align-items: center; gap: 12px; text-decoration: none;
  background: linear-gradient(90deg, #ecf5ff 0%, #f5f9ff 100%);
  border: 1px solid #b3d8ff; border-left: 4px solid #409eff;
  border-radius: 8px; padding: 12px 16px; margin-bottom: 18px;
  transition: box-shadow .2s, transform .1s;
}
.bm-banner:hover { box-shadow: 0 2px 12px rgba(64,158,255,.25); transform: translateY(-1px); }
.bm-banner-icon { font-size: 22px; }
.bm-banner-text { display: flex; flex-direction: column; flex: 1; line-height: 1.5; }
.bm-banner-text b { color: #2c6cb0; font-size: 14px; }
.bm-banner-text em { color: #7a9bc0; font-size: 12px; font-style: normal; }
.bm-banner-go { color: #409eff; font-weight: 600; font-size: 13px; white-space: nowrap; }
.gen-bar h2 { margin: 0 0 4px; font-size: 20px; }
.gen-sub { color: #888; font-size: 13px; margin: 0 0 14px; }
.gen-controls { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.gen-hint { color: #e6a23c; font-size: 12px; }

.cl-list { margin-top: 28px; display: flex; flex-direction: column; gap: 12px; }
.cl-card { border: 1px solid #ebeef5; border-radius: 10px; padding: 14px 16px; cursor: pointer; transition: all .15s; }
.cl-card:hover { border-color: #c6d4ff; box-shadow: 0 2px 12px rgba(0,0,0,.06); }
.cl-card-head { display: flex; align-items: center; gap: 10px; }
.cl-act { font-size: 12px; font-weight: 600; padding: 2px 10px; border-radius: 12px; }
.cl-title { font-weight: 600; }
.cl-gen { color: #e6a23c; font-size: 12px; }
.cl-card-meta { margin-top: 8px; display: flex; gap: 16px; color: #999; font-size: 12px; align-items: center; }
.cl-time { margin-left: auto; }

.detail-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin: 8px 0 14px; }
.detail-title { font-size: 18px; font-weight: 600; }
.detail-prog { margin-left: auto; color: #67c23a; font-size: 13px; }
.detail-by { color: #909399; font-size: 13px; }
.readonly-tag { color: #e6a23c; font-size: 13px; font-weight: 600; }
.cl-readonly { color: #c0c4cc; font-size: 12px; }
.detail-toolbar { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; margin: 0 0 18px; padding: 10px 14px; background: #f7f9fc; border-radius: 8px; }
.toolbar-left { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.toolbar-right { display: flex; align-items: center; gap: 10px; }
.dim-title { font-size: 15px; font-weight: 600; }
.dim-count { font-style: normal; color: #999; font-size: 12px; margin: 0 10px; }
.item-tags { display: flex; gap: 8px; margin-bottom: 4px; align-items: center; }
.stage-tag { font-size: 11px; background: #fff3e0; color: #e6792b; padding: 1px 8px; border-radius: 10px; }
.sev-tag { font-size: 11px; font-weight: 700; padding: 1px 8px; border-radius: 10px; color: #fff; }
.sev-P0 { background: #e63946; }
.sev-P1 { background: #f59e0b; }
.sev-P2 { background: #9aa0a6; }
.daylate-tag { font-size: 11px; font-weight: 700; color: #fff; background: #c1121f; padding: 1px 8px; border-radius: 10px; box-shadow: 0 0 0 2px rgba(193,18,31,0.18); cursor: help; }
a.item-src { color: #409eff; text-decoration: none; }
a.item-src:hover { text-decoration: underline; }
.cross-tag { font-size: 11px; background: #ecf5ff; color: #409eff; padding: 1px 8px; border-radius: 10px; border: 1px solid #d0e6ff; }
.dim-incident { color: #e63946; font-weight: 700; }
.own-tag { font-size: 11px; background: #eef2ff; color: #4d6bfe; padding: 1px 8px; border-radius: 10px; }
.own-empty { background: transparent; color: #bbb; cursor: pointer; border: 1px dashed #ddd; }
.own-empty:hover { color: #4d6bfe; border-color: #4d6bfe; }
.item-handled { color: #67c23a; font-size: 12px; }
.dim-group { margin-bottom: 26px; }
.dim-title { font-size: 15px; border-left: 4px solid; border-image: linear-gradient(180deg, #2f80ff, #36d1c4) 1; padding-left: 10px; display: inline-flex; align-items: center; gap: 8px; color: #1c2f5e; font-weight: 600; line-height: 1.2; white-space: nowrap; }
.dim-title em { font-style: normal; color: #999; font-size: 12px; }
/* 事故/故障维度保持红色告警语义 */
.dim-title.dim-incident { color: #e63946; border-image: none; border-left-color: #e63946; }
.item-card { display: flex; gap: 10px; border: 1px solid #dbe5f2; border-radius: 10px; padding: 12px 14px; margin-top: 10px; background: linear-gradient(180deg, #fbfdff 0%, #ffffff 60%); box-shadow: 0 1px 6px rgba(31,58,110,.05); transition: box-shadow .2s ease, border-color .2s ease, transform .1s ease; }
.item-card:hover { border-color: rgba(47,128,255,.4); box-shadow: 0 4px 16px rgba(47,128,255,.14); transform: translateY(-1px); }
.item-card.handled { opacity: .55; background: #f5f5f5; }
.item-card.item-selected { border-color: #4d6bfe; background: #eef2ff; }
.export-bar { display: flex; align-items: center; gap: 10px; margin: 0 0 14px; padding: 10px 14px; background: #fff7e6; border: 1px solid #ffe0a3; border-radius: 8px; font-size: 13px; }
.export-hint { color: #999; font-size: 12px; }
.export-result { margin: 0 0 14px; padding: 10px 14px; background: #f0f9eb; border: 1px solid #c2e7b0; border-radius: 8px; font-size: 13px; }
.export-result a { color: #4d6bfe; margin: 0 8px; font-weight: 600; }
.item-check { margin-top: 2px; }
.item-body { flex: 1; }
.item-row { font-size: 13px; line-height: 1.7; color: #444; }
.item-row b { display: inline-block; min-width: 56px; color: #888; font-weight: 500; margin-right: 6px; }
.item-row.sugg b { color: #4d6bfe; }
.item-row.timing b { color: #e6a23c; }
.item-foot { margin-top: 6px; display: flex; align-items: center; gap: 8px; }
.item-src { color: #aaa; font-size: 12px; margin-right: auto; }

/* 维度折叠：套用作战地图卡片质感(蓝调圆角卡片+顺滑动效)，红色告警语义不动 */
.cl-collapse { border: none; }
.cl-collapse :deep(.el-collapse-item) {
  margin-bottom: 12px;
  border: 1px solid #dbe5f2;
  border-radius: 12px;
  background: linear-gradient(180deg, #fbfdff 0%, #ffffff 60%);
  box-shadow: 0 2px 10px rgba(31,58,110,.06);
  overflow: hidden;
  transition: box-shadow .25s ease, border-color .25s ease;
}
.cl-collapse :deep(.el-collapse-item:hover) { border-color: rgba(47,128,255,.4); box-shadow: 0 6px 20px rgba(47,128,255,.14); }
.cl-collapse :deep(.el-collapse-item.is-active) { border-color: rgba(47,128,255,.5); box-shadow: 0 6px 22px rgba(47,128,255,.16); }
.cl-collapse :deep(.el-collapse-item__header) {
  border: none; background: transparent; padding: 0 16px; height: 52px; line-height: 52px;
  display: flex; align-items: center; flex-wrap: nowrap;
}
.cl-collapse :deep(.el-collapse-item__header .dim-title) { flex: 0 0 auto; }
.cl-collapse :deep(.el-collapse-item__wrap) { border: none; background: transparent; }
.cl-collapse :deep(.el-collapse-item__content) { padding: 0 16px 14px; }
.cl-collapse :deep(.el-collapse-item__arrow) { color: #8aa6d0; }
.cl-collapse :deep(.el-collapse-item.is-active .el-collapse-item__arrow) { color: #2f80ff; }
/* 事故/故障维度卡片：左侧红条提示告警 */
.cl-collapse :deep(.el-collapse-item.is-incident) { border-left: 4px solid #e63946; }
</style>
