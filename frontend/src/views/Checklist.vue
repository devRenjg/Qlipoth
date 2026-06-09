<template>
  <div class="checklist-page">
    <!-- 顶部：生成新清单 -->
    <div class="gen-bar">
      <h2>保障清单 · 历史踩坑预警</h2>
      <p class="gen-sub">选活动类型，从历史复盘文档中提炼上届踩过的坑，按保障六维度生成备战清单。</p>
      <div class="gen-controls">
        <el-select v-model="genActivity" placeholder="选择大型活动" style="width: 180px">
          <el-option v-for="a in activities" :key="a" :label="a" :value="a" />
        </el-select>
        <el-button type="primary" :loading="generating" :disabled="!genActivity" @click="doGenerate">
          {{ generating ? `生成中 ${genProcessed}/${genTotal}` : '生成清单' }}
        </el-button>
        <span v-if="generating" class="gen-hint">扫描复盘文档中，约需几分钟，可离开页面稍后回来看</span>
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
          <span>源自 {{ c.source_doc_count }} 篇复盘</span>
          <span class="cl-time">{{ c.created_at }}</span>
          <el-button size="small" type="danger" text @click.stop="removeChecklist(c.id)">删除</el-button>
        </div>
      </div>
    </div>

    <!-- 清单详情：按六维度分组 -->
    <div class="cl-detail" v-if="activeChecklist">
      <div class="detail-head">
        <el-button text @click="closeChecklist">← 返回列表</el-button>
        <span class="cl-act" :style="actStyle(activeChecklist.checklist.activity)">{{ activeChecklist.checklist.activity }}</span>
        <span class="detail-title">{{ activeChecklist.checklist.title }}</span>
        <span class="detail-prog">已处理 {{ activeChecklist.handled_count }}/{{ activeChecklist.item_count }}</span>
        <el-radio-group v-model="filterMode" size="small" class="detail-filter">
          <el-radio-button value="all">全部</el-radio-button>
          <el-radio-button value="todo">未完成</el-radio-button>
          <el-radio-button value="done">已完成</el-radio-button>
        </el-radio-group>
        <el-radio-group v-model="sevFilter" size="small" class="detail-filter">
          <el-radio-button value="p0">只看P0</el-radio-button>
          <el-radio-button value="p01">P0+P1</el-radio-button>
          <el-radio-button value="all">全部</el-radio-button>
        </el-radio-group>
        <el-button size="small" text @click="toggleAll">{{ allCollapsed ? '全部展开' : '全部折叠' }}</el-button>
      </div>
      <el-collapse v-model="openDims">
        <el-collapse-item v-for="dim in activeChecklist.dimensions" :key="dim" :name="dim"
                          v-show="visibleItems(dim).length || (activeChecklist.grouped[dim] || []).length">
          <template #title>
            <span class="dim-title" :class="{ 'dim-incident': dim === '事故/故障' }">{{ dim === '事故/故障' ? '⚠ ' + dim : dim }}</span>
            <em class="dim-count">{{ visibleItems(dim).length }} 条</em>
            <el-button size="small" text @click.stop="startAdd(dim)">+ 加一条</el-button>
          </template>
          <div v-for="it in visibleItems(dim)" :key="it.id"
               class="item-card" :class="{ handled: it.handled }">
            <el-checkbox :model-value="!!it.handled" @change="toggleHandled(it)" class="item-check" />
            <div class="item-body">
              <div class="item-tags">
                <span class="sev-tag" :class="'sev-' + (it.severity || 'P2')">{{ it.severity || 'P2' }}</span>
                <span class="stage-tag" v-if="it.stage">{{ it.stage }}</span>
                <span class="cross-tag" v-if="it.cross_from">借鉴自 {{ it.cross_from }}</span>
                <span class="own-tag" v-if="it.team || it.owner">
                  {{ it.team }}<template v-if="it.team && it.owner"> · </template>{{ it.owner }}
                </span>
                <span class="own-tag own-empty" v-else @click="startEdit(it)">+ 标注归属</span>
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
                <el-button size="small" text @click="startEdit(it)">编辑</el-button>
                <el-button size="small" text type="danger" @click="removeItem(it)">删除</el-button>
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
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  generateChecklist, getChecklistProgress, listChecklists, getChecklist,
  updateChecklistItem, addChecklistItem, deleteChecklistItem, deleteChecklist,
} from '../api/index.js'
import { colorForTag } from '../utils/tagColor.js'

// 跨晚生成暂不开放，先聚焦 S赛/春晚（已有跨晚清单仍可查看）
const activities = ['S赛', '春晚']
const dimensions = ['事故/故障', '高可用保障', '直播播放体验', '安全', '业务需求', '成本']
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
    // 默认展开所有有内容的维度（记住后续手动折叠状态）
    openDims.value = data.dimensions.filter(d => (data.grouped[d] || []).length)
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

.detail-head { display: flex; align-items: center; gap: 12px; margin: 8px 0 20px; }
.detail-title { font-size: 18px; font-weight: 600; }
.detail-prog { margin-left: auto; color: #67c23a; font-size: 13px; }
.detail-filter { margin-left: 12px; }
.dim-title { font-size: 15px; font-weight: 600; }
.dim-count { font-style: normal; color: #999; font-size: 12px; margin: 0 10px; }
.item-tags { display: flex; gap: 8px; margin-bottom: 4px; align-items: center; }
.stage-tag { font-size: 11px; background: #fff3e0; color: #e6792b; padding: 1px 8px; border-radius: 10px; }
.sev-tag { font-size: 11px; font-weight: 700; padding: 1px 8px; border-radius: 10px; color: #fff; }
.sev-P0 { background: #e63946; }
.sev-P1 { background: #f59e0b; }
.sev-P2 { background: #9aa0a6; }
a.item-src { color: #409eff; text-decoration: none; }
a.item-src:hover { text-decoration: underline; }
.cross-tag { font-size: 11px; background: #ecf5ff; color: #409eff; padding: 1px 8px; border-radius: 10px; border: 1px solid #d0e6ff; }
.dim-incident { color: #e63946; font-weight: 700; }
.own-tag { font-size: 11px; background: #eef2ff; color: #4d6bfe; padding: 1px 8px; border-radius: 10px; }
.own-empty { background: transparent; color: #bbb; cursor: pointer; border: 1px dashed #ddd; }
.own-empty:hover { color: #4d6bfe; border-color: #4d6bfe; }
.item-handled { color: #67c23a; font-size: 12px; }
.dim-group { margin-bottom: 26px; }
.dim-title { font-size: 15px; border-left: 4px solid #4d6bfe; padding-left: 10px; display: flex; align-items: center; gap: 8px; }
.dim-title em { font-style: normal; color: #999; font-size: 12px; }
.item-card { display: flex; gap: 10px; border: 1px solid #f0f0f0; border-radius: 8px; padding: 12px 14px; margin-top: 10px; background: #fafbff; }
.item-card.handled { opacity: .55; background: #f5f5f5; }
.item-check { margin-top: 2px; }
.item-body { flex: 1; }
.item-row { font-size: 13px; line-height: 1.7; color: #444; }
.item-row b { display: inline-block; min-width: 56px; color: #888; font-weight: 500; margin-right: 6px; }
.item-row.sugg b { color: #4d6bfe; }
.item-row.timing b { color: #e6a23c; }
.item-foot { margin-top: 6px; display: flex; align-items: center; gap: 8px; }
.item-src { color: #aaa; font-size: 12px; margin-right: auto; }
</style>
