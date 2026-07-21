<template>
  <div class="chat-wrapper">
    <div class="history-sidebar">
      <div class="sidebar-header">
        <span>对话历史</span>
        <el-button size="small" type="primary" text @click="newChat">新对话</el-button>
      </div>
      <div class="sidebar-list">
        <div
          v-for="item in conversations"
          :key="item.conversation_id"
          class="history-item"
          :class="{ active: conversationId === item.conversation_id }"
          @click="loadConversation(item)"
        >
          <div class="history-question">{{ item.last_question }}</div>
          <div v-if="item.has_tags" class="history-tags">
            <el-tag
              v-for="t in item.selected_tags"
              :key="t.id"
              size="small"
              type="warning"
              effect="plain"
              class="history-tag"
            >{{ t.name }}</el-tag>
            <span v-if="!item.selected_tags || !item.selected_tags.length" class="history-tag-flag">含标签筛选</span>
          </div>
          <div class="history-meta">
            <span class="history-time">{{ item.created_at }}</span>
            <span v-if="item.turn_count > 1" class="history-turns">{{ item.turn_count }} 轮</span>
            <span class="history-user">{{ item.user_name }}</span>
          </div>
        </div>
        <div v-if="!conversations.length" class="sidebar-empty">暂无记录</div>
      </div>
    </div>
    <div class="chat-view">
      <div class="chat-messages" ref="messagesRef">
        <div v-if="messages.length === 0" class="empty-state">
          <div class="shield-icon">&#9670;</div>
          <h2>直播大型活动保障！有问必答！使命必达！</h2>
          <p class="subtitle">克里珀持续以光年级屏障隔绝威胁，维系现存世界的完整。</p>
          <div v-if="tags.length" class="tag-filter">
            <div v-if="activityTags.length" class="tag-row">
              <span class="tag-filter-label">大型活动：</span>
              <div class="tag-chips">
                <span
                  v-for="t in activityTags"
                  :key="t.id"
                  class="tag-chip tag-chip-activity"
                  :style="tagStyle(t)"
                  @click="toggleTag(t.id)"
                >{{ t.name }} <em class="tag-chip-count">{{ t.doc_count }}</em></span>
              </div>
            </div>
            <div class="tag-row">
              <span class="tag-filter-label">主题：</span>
              <div class="tag-chips">
                <span
                  v-for="t in topicTags"
                  :key="t.id"
                  class="tag-chip"
                  :style="tagStyle(t)"
                  @click="toggleTag(t.id)"
                >{{ t.name }} <em class="tag-chip-count">{{ t.doc_count }}</em></span>
              </div>
            </div>
            <span v-if="selectedTagIds.length" class="tag-filter-hint">仅在选中标签的文档中检索</span>
          </div>
          <div class="center-input">
            <div class="input-wrapper">
              <el-input
                v-model="input"
                placeholder="输入你的问题..."
                @keyup.enter="sendMessage"
                :disabled="loading"
                size="large"
              >
                <template #append>
                  <el-button @click="sendMessage" :loading="loading" type="primary">发送</el-button>
                </template>
              </el-input>
            </div>
          </div>
          <div class="preset-questions">
            <span class="preset-label">快速问答：</span>
            <el-tag
              v-for="q in presetQuestions"
              :key="q"
              class="preset-tag"
              @click="askPreset(q)"
              effect="plain"
            >{{ q }}</el-tag>
          </div>
        </div>
        <div v-for="(msg, i) in messages" :key="i" :class="['message', msg.role]">
          <div class="message-content">
            <div class="message-text" :class="{ 'markdown-body': msg.html }" v-html="msg.html || msg.text"></div>
            <div v-if="msg.sourceUrls && msg.sourceUrls.length" class="message-refs">
              <div class="refs-label">引用文档：</div>
              <div v-for="(s, idx) in msg.sourceUrls" :key="idx" class="ref-item">
                <a v-if="s.url" :href="s.url" target="_blank" class="ref-link">{{ s.title }}</a>
                <a v-else href="#" class="ref-link ref-local" @click.prevent="viewLocalDoc(s.file)">{{ s.title }}</a>
              </div>
            </div>
            <div v-if="msg.images && msg.images.length" class="message-images">
              <div class="images-label">相关图片（{{ msg.images.length }}）：</div>
              <div class="images-grid">
                <a
                  v-for="(img, idx) in msg.images"
                  :key="idx"
                  :href="img.url"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="image-cell"
                  :title="img.title"
                >
                  <img :src="img.url" :alt="img.title" loading="lazy" referrerpolicy="no-referrer" />
                </a>
              </div>
            </div>
            <div v-if="msg.timing" class="message-timing">
              思考耗时 {{ msg.timing.total }}s（分析 {{ msg.timing.strategy }}s + 检索 {{ msg.timing.search }}s + 组织 {{ msg.timing.answer }}s）
            </div>
          </div>
        </div>
        <div v-if="loading && messages[messages.length-1]?.text === ''" class="message assistant">
          <div class="message-content">
            <el-icon class="loading-icon"><Loading /></el-icon> 正在整理思路...
          </div>
        </div>
      </div>
      <div v-if="messages.length > 0" class="chat-input">
        <div class="input-wrapper">
          <el-input
            v-model="input"
            placeholder="输入你的问题..."
            @keyup.enter="sendMessage"
            :disabled="loading"
            size="large"
          >
            <template #append>
              <el-button @click="sendMessage" :loading="loading" type="primary">发送</el-button>
            </template>
          </el-input>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
// 显式声明组件名，供 App.vue 的 <keep-alive :include="['Chat']"> 精确匹配（缓存问答页）
export default { name: 'Chat' }
</script>

<script setup>
import { ref, inject, nextTick, onMounted, onActivated, computed, watch } from 'vue'
import { Loading } from '@element-plus/icons-vue'
import { queryKnowledgeBaseStream, getConversations, getConversation, getChatTurn, getTags } from '../api/index.js'
import { addProfilingRecord } from '../store/profiling.js'
import { renderMarkdown } from '../utils/markdown.js'
import { tagChipStyle, isActivityTag } from '../utils/tagColor.js'
import 'github-markdown-css/github-markdown-light.css'

const currentUser = inject('currentUser')
const messages = ref([])
const input = ref('')
const loading = ref(false)
const messagesRef = ref(null)
const conversations = ref([])
const conversationId = ref(null)
const tags = ref([])
// 活动维度标签与主题标签分组展示（活动标签更醒目、置于前）
const activityTags = computed(() => tags.value.filter(t => isActivityTag(t.name)))
const topicTags = computed(() => tags.value.filter(t => !isActivityTag(t.name)))
const selectedTagIds = ref([])

// —— Route 2 后台生成：SSE 只是"订阅"，后端独立跑生成并落库 ——
// activeController：当前 SSE 的中止句柄。切会话时中止订阅（后端继续生成），不丢数据。
// pollCancel：重连轮询的取消函数。切回"生成中"的会话/刷新后，轮询 /chat/turn 续显。
let activeController = null
let pollCancel = null

function stopActiveStream() {
  if (activeController) { try { activeController.abort() } catch {} activeController = null }
}
function stopPoll() {
  if (pollCancel) { pollCancel(); pollCancel = null }
}

// 由 chat_history 轮次构建消息数组（DB 为准）。带 historyId + status，供重连识别"生成中"的轮次。
function buildMessagesFromTurns(turns) {
  const msgs = []
  for (const turn of turns) {
    msgs.push({ role: 'user', text: turn.question })
    const status = turn.status || 'done'
    let text = turn.answer || ''
    if (status === 'error' && !text) text = '（回答生成被中断，请重新提问）'
    msgs.push({
      role: 'assistant',
      text,
      html: formatMarkdown(text),
      sourceUrls: turn.source_urls || [],
      images: [],
      timing: null,
      historyId: turn.id,
      status,
    })
  }
  return msgs
}

// 重连"生成中"轮次：轮询只读接口 /chat/turn/{id} 续显，直到 done/error。绝不重新触发生成。
function reconnectTurn(msg) {
  if (!msg || !msg.historyId) return
  stopPoll()
  loading.value = true
  let cancelled = false
  pollCancel = () => { cancelled = true }
  const tick = async () => {
    if (cancelled) return
    let stop = false
    try {
      const { data } = await getChatTurn(msg.historyId)
      if (data.answer != null && data.answer !== msg.text) {
        msg.text = data.answer
        msg.html = formatMarkdown(data.answer)
        scrollToBottom()
      }
      if (data.status && data.status !== 'generating') {
        msg.status = data.status
        if (data.status === 'error' && !msg.text) {
          msg.text = '（回答生成被中断，请重新提问）'
          msg.html = formatMarkdown(msg.text)
        }
        if (data.timing) msg.timing = data.timing
        stop = true
      }
    } catch {
      stop = true  // 记录不存在/网络异常 → 停止轮询
    }
    if (cancelled) return
    if (stop) {
      pollCancel = null
      loading.value = false
      scrollToBottom()
      loadConversations()
      return
    }
    setTimeout(tick, 800)
  }
  tick()
}

// 刷新/重开后以 DB 为准重载当前会话，并对未完成轮次重连。
async function reloadCurrentConversation() {
  const cid = conversationId.value
  if (!cid || cid.startsWith('legacy-')) return
  try {
    const { data } = await getConversation(cid)
    if (!data || !data.length) return
    messages.value = buildMessagesFromTurns(data)
    const last = messages.value[messages.value.length - 1]
    if (last && last.role === 'assistant' && last.status === 'generating') reconnectTurn(last)
    await scrollToBottom()
  } catch {}
}

const presetQuestions = [
  'S15 总决赛 PCU 多少啊？',
  '2026 春晚总共有多少需求，大概是哪些方向的内容？',
  '2026 春晚需求上线后的版本覆盖率多少，对比上次如何？',
]

// —— 当前对话本地持久化：切 Tab(keep-alive)已能保住内存态；此处再抗刷新/关页重开 ——
// 按当前用户隔离 key(currentUser 已在上方 inject)，避免同机不同账号/访客串对话。
function draftKey() {
  const uid = currentUser?.value?.id ?? 'guest'
  return `qlipoth_chat_draft_${uid}`
}

function saveDraft() {
  try {
    // 不持久化 loading：重开后不再假装"生成中"。html 可由 text 重建，故只存原始字段。
    const slim = messages.value.map(m => ({
      role: m.role,
      text: m.text,
      sourceUrls: m.sourceUrls || [],
      images: m.images || [],
      timing: m.timing || null,
    }))
    const payload = { messages: slim, conversationId: conversationId.value, selectedTagIds: selectedTagIds.value }
    localStorage.setItem(draftKey(), JSON.stringify(payload))
  } catch { /* 持久化失败不影响主流程 */ }
}

function restoreDraft() {
  try {
    const raw = localStorage.getItem(draftKey())
    if (!raw) return false
    const d = JSON.parse(raw)
    if (!d || !Array.isArray(d.messages) || !d.messages.length) return false
    const msgs = d.messages.map(m => ({
      role: m.role,
      text: m.text || '',
      html: m.role === 'assistant' ? formatMarkdown(m.text || '') : '',
      sourceUrls: m.sourceUrls || [],
      images: m.images || [],
      timing: m.timing || null,
    }))
    // 末条助手消息为空 = 上次生成中被刷新/关页打断、从未落库，明确提示而非留空白气泡。
    const last = msgs[msgs.length - 1]
    if (last && last.role === 'assistant' && !last.text) {
      last.text = '（上次回答生成被中断，请重新提问）'
      last.html = formatMarkdown(last.text)
    }
    messages.value = msgs
    conversationId.value = d.conversationId || null
    selectedTagIds.value = Array.isArray(d.selectedTagIds) ? d.selectedTagIds : []
    return true
  } catch { return false }
}

function clearDraft() {
  try { localStorage.removeItem(draftKey()) } catch {}
}

onMounted(async () => {
  // 内存空(首次进入/刷新重开)时：先恢复本地草稿拿回 conversationId，
  // 再以 DB 为准重载该会话——若上次是"生成中"被刷新打断，后端仍在跑，这里会重连续显。
  if (!messages.value.length) {
    restoreDraft()
    await reloadCurrentConversation()
  }
  loadConversations()
  loadTags()
})

// keep-alive 激活(切回 Tab)：内存态还在，无需重载。但若当前会话末轮仍是"生成中"
// 且没有活跃订阅/轮询(例如曾切走会话)，补一次重连续显，保证切回能看到最新进度。
onActivated(() => {
  const last = messages.value[messages.value.length - 1]
  if (last && last.role === 'assistant' && last.status === 'generating' && !activeController && !pollCancel) {
    reconnectTurn(last)
  }
  scrollToBottom()
})

// 对话内容/会话/标签变化 → 写本地草稿。流式生成时 chunk 高频触发，做 300ms 防抖降低写入频率。
let saveTimer = null
function scheduleSave() {
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(saveDraft, 300)
}
watch([messages, conversationId, selectedTagIds], scheduleSave, { deep: true })

async function loadConversations() {
  try {
    // 全员可见所有人的会话历史（不按当前用户过滤）
    const { data } = await getConversations()
    conversations.value = data
  } catch {}
}

async function loadTags() {
  try {
    const { data } = await getTags()
    // 按文档数降序，空标签沉底
    tags.value = [...data].sort((a, b) => (b.doc_count || 0) - (a.doc_count || 0))
  } catch {}
}

function toggleTag(id) {
  const i = selectedTagIds.value.indexOf(id)
  if (i === -1) selectedTagIds.value.push(id)
  else selectedTagIds.value.splice(i, 1)
}

function tagStyle(t) {
  return tagChipStyle(t.name, selectedTagIds.value.includes(t.id))
}

async function loadConversation(item) {
  // 切会话：中止当前 SSE 订阅与轮询（后端后台任务继续跑、不受影响），再载入目标会话。
  stopActiveStream()
  stopPoll()
  loading.value = false
  try {
    const { data } = await getConversation(item.conversation_id)
    messages.value = buildMessagesFromTurns(data)
    // legacy 单轮伪会话不可续聊（无真实 conversation_id），续聊另起新会话
    conversationId.value = item.conversation_id.startsWith('legacy-') ? null : item.conversation_id
    // 目标会话末轮若仍在生成 → 重连续显（后端在跑，DB 持续增量落库）
    const last = messages.value[messages.value.length - 1]
    if (last && last.role === 'assistant' && last.status === 'generating') reconnectTurn(last)
    await scrollToBottom()
  } catch {}
}

function newChat() {
  stopActiveStream()
  stopPoll()
  loading.value = false
  messages.value = []
  conversationId.value = null
  input.value = ''
  clearDraft()  // 清掉本地草稿，新对话不残留上一段
}

function askPreset(q) {
  input.value = q
  sendMessage()
}

function viewLocalDoc(file) {
  const url = `/api/documents/view/${encodeURIComponent(file)}`
  window.open(url, '_blank')
}

async function sendMessage() {
  const question = input.value.trim()
  if (!question || loading.value) return

  if (!conversationId.value) conversationId.value = genId()
  const convId = conversationId.value

  // 本轮锁定所选标签：快照 id 与名称，用于检索过滤 + 历史记录
  const tagIds = [...selectedTagIds.value]
  const tagSnapshot = tags.value
    .filter(t => tagIds.includes(t.id))
    .map(t => ({ id: t.id, name: t.name }))

  messages.value.push({ role: 'user', text: question })
  input.value = ''
  loading.value = true
  await scrollToBottom()

  // 绑定回调到消息对象引用(而非下标)：即便数组后续被重建，本次流仍写入正确对象。
  const botMsg = {
    role: 'assistant',
    text: '',
    html: '',
    sources: [],
    sourceUrls: [],
    images: [],
    timing: null,
    historyId: null,
    status: 'generating',
  }
  messages.value.push(botMsg)

  // Route 2：后端已在后台落库，前端不再于 onDone 调 saveChatHistory(否则重复插入)。
  // 切会话/刷新会中止本 SSE 订阅，但后端后台任务继续，重连轮询 /chat/turn 续显。
  activeController = queryKnowledgeBaseStream(question, {
    onMeta(meta) {
      botMsg.sources = meta.sources
      botMsg.sourceUrls = meta.source_urls || []
      botMsg.images = meta.relevant_images || []
      if (meta.history_id) botMsg.historyId = meta.history_id  // 拿到落库行 id，供重连
    },
    onChunk(chunk) {
      botMsg.text += chunk
      botMsg.html = formatMarkdown(botMsg.text)
      scrollToBottom()
    },
    onDone(timing) {
      botMsg.timing = timing
      botMsg.status = 'done'
      addProfilingRecord(question, timing)
      loading.value = false
      activeController = null
      scrollToBottom()
      loadConversations()  // 刷新侧栏(后端已落库)
    },
    onError(err) {
      botMsg.text = err || '请求失败，请检查后端服务和 LLM 配置'
      botMsg.html = formatMarkdown(botMsg.text)
      botMsg.status = 'error'
      loading.value = false
      activeController = null
    },
  }, convId, tagIds)
}

function formatMarkdown(text) {
  return renderMarkdown(text)
}

// 生成会话ID。crypto.randomUUID() 仅在 HTTPS/localhost 等安全上下文可用，
// 通过 http://<内网IP> 访问时它是 undefined，直接调用会抛错导致发送无响应。
// 这里做安全降级，保证内网 IP 访问也能正常发起问答。
function genId() {
  try {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID()
  } catch {}
  return 'c-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10)
}

async function scrollToBottom() {
  await nextTick()
  if (messagesRef.value) {
    messagesRef.value.scrollTop = messagesRef.value.scrollHeight
  }
}
</script>

<style scoped>
.chat-wrapper { display: flex; height: calc(100vh - 100px); gap: 0; }
.history-sidebar {
  width: 260px;
  border-right: 1px solid #e8e8e8;
  display: flex;
  flex-direction: column;
  background: #f9fafb;
  flex-shrink: 0;
}
.sidebar-header {
  padding: 14px 16px;
  font-size: 14px;
  font-weight: 500;
  color: #333;
  border-bottom: 1px solid #e8e8e8;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.sidebar-list { flex: 1; overflow-y: auto; padding: 8px 0; }
.history-item {
  padding: 10px 16px;
  cursor: pointer;
  border-bottom: 1px solid #f0f0f0;
  transition: background 0.15s;
}
.history-item:hover { background: #eef1f6; }
.history-item.active { background: #e6f0ff; border-left: 3px solid #4d6bfe; }
.history-question {
  font-size: 13px;
  color: #333;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.history-meta { display: flex; gap: 8px; margin-top: 4px; font-size: 11px; }
.history-tags { margin-top: 5px; display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
.history-tag { font-size: 11px; }
.history-tag-flag { font-size: 11px; color: #e6a23c; }
.history-user { color: #4d6bfe; }
.history-turns { color: #67c23a; }
.history-time { color: #999; }
.sidebar-empty { padding: 20px 16px; color: #999; font-size: 13px; text-align: center; }
.chat-view { flex: 1; display: flex; flex-direction: column; font-size: 15px; min-width: 0; }
.chat-messages { flex: 1; overflow-y: auto; padding: 20px 0; display: flex; flex-direction: column; justify-content: flex-start; padding-top: 12vh; }
.empty-state { text-align: center; color: #666666; }
.shield-icon {
  font-size: 48px;
  color: #4d6bfe;
  margin-bottom: 16px;
}
.empty-state h2 {
  font-size: 18px;
  margin-bottom: 12px;
  color: #1a1a2e;
  font-weight: 400;
  white-space: nowrap;
  line-height: 1.6;
}
.subtitle {
  font-size: 15px;
  color: #888888;
  margin-bottom: 24px;
}
.center-input {
  display: flex;
  justify-content: center;
  margin-bottom: 20px;
}
.preset-questions {
  margin-top: 28px;
  max-width: 100%;
  display: flex;
  flex-wrap: nowrap;
  justify-content: center;
  gap: 8px;
  align-items: center;
}
.preset-label {
  flex: 0 0 auto;
  font-size: 13px;
  color: #909399;
  white-space: nowrap;
}
.tag-filter {
  margin-top: 14px;
  margin-bottom: 20px;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 10px;
  width: 660px;
  max-width: 100%;
  margin-left: auto;
  margin-right: auto;
}
.tag-row {
  display: flex;
  flex-wrap: nowrap;
  align-items: flex-start;
  gap: 8px;
}
.tag-filter-label {
  flex: 0 0 76px;
  width: 76px;
  font-size: 13px;
  color: #909399;
  text-align: right;
  white-space: nowrap;
  line-height: 26px;
  height: 26px;
}
.tag-chips {
  flex: 1;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.tag-filter-label { font-size: 13px; color: #888888; }
.tag-filter-hint { font-size: 12px; color: #e6a23c; margin-left: 4px; width: 100%; text-align: center; }
.tag-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 12px;
  font-size: 13px;
  line-height: 1.4;
  border: 1px solid transparent;
  border-radius: 14px;
  cursor: pointer;
  user-select: none;
  transition: all 0.15s ease;
}
.tag-chip:hover { filter: brightness(0.96); transform: translateY(-1px); }
.tag-chip-activity { font-weight: 600; }
.tag-group-sep { color: #c0c4cc; margin: 0 2px; user-select: none; }
.tag-chip-count {
  font-style: normal;
  font-size: 11px;
  opacity: 0.7;
}
.preset-tag {
  cursor: pointer;
  font-size: 13px;
  white-space: nowrap;
  background: rgba(77, 107, 254, 0.08) !important;
  border-color: rgba(77, 107, 254, 0.3) !important;
  color: #7b93fe !important;
  transition: all 0.2s;
}
.preset-tag:hover {
  background: rgba(77, 107, 254, 0.15) !important;
  border-color: #4d6bfe !important;
  color: #4d6bfe !important;
}
/* 对话内容居中成 660px 列,与底部输入框对齐,整体感更强(ChatGPT/DeepSeek 风格) */
.message { margin-bottom: 16px; display: flex; width: 660px; max-width: 100%; align-self: center; }
.message.user { justify-content: flex-end; }
.message.assistant { justify-content: flex-start; }
.message-content {
  max-width: 75%;
  padding: 14px 18px;
  border-radius: 12px;
  line-height: 1.7;
  font-size: 15px;
}
.message.user .message-content {
  background: #4d6bfe;
  color: #ffffff;
}
/* 助手回答走 DeepSeek/ChatGPT 风格:无边框无底色,与页面融为一体,占满宽度 */
.message.assistant .message-content {
  max-width: 100%;
  width: 100%;
  background: transparent;
  color: #1a1a2e;
  border: none;
  padding: 4px 0;
}
.message-text.markdown-body {
  background: transparent;
  font-size: 15px;
  color: #1a1a2e;
}
.message-text.markdown-body :deep(table) { margin: 8px 0; }
.message-text.markdown-body :deep(pre) { background: #eef0f3; }
.message-text.markdown-body :deep(h1),
.message-text.markdown-body :deep(h2) { border-bottom: 1px solid #e0e0e0; }
.message-sources { margin-top: 8px; font-size: 13px; }
.source-item { padding: 4px 0; border-bottom: 1px solid #e8e8e8; color: #666666; }
.source-item code { color: #4d6bfe; margin-right: 8px; }
.message-refs { margin-top: 10px; font-size: 13px; color: #909399; }
.refs-label { margin-bottom: 4px; }
.ref-item { padding: 2px 0; }
.ref-link { color: #409eff; text-decoration: none; }
.ref-link:hover { text-decoration: underline; }
.ref-local { color: #67c23a; }
.message-images { margin-top: 10px; }
.images-label { font-size: 13px; color: #909399; margin-bottom: 6px; }
.images-grid { display: flex; flex-wrap: wrap; gap: 8px; }
.image-cell {
  display: block;
  width: 120px;
  height: 90px;
  border: 1px solid #e8e8e8;
  border-radius: 6px;
  overflow: hidden;
  background: #f0f0f0;
}
.image-cell img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  transition: transform 0.15s ease;
}
.image-cell:hover img { transform: scale(1.05); }
.message-timing { margin-top: 6px; font-size: 12px; color: #888888; }
.chat-input {
  padding: 16px 0;
  border-top: 1px solid #e8e8e8;
  display: flex;
  justify-content: center;
}
.input-wrapper {
  width: 660px;
  max-width: 100%;
}
.chat-input :deep(.el-input__wrapper) {
  background: #ffffff;
  border: 1px solid #d9d9d9;
  box-shadow: none;
}
.chat-input :deep(.el-input__wrapper:hover) {
  border-color: #4d6bfe;
}
.chat-input :deep(.el-input__wrapper.is-focus) {
  border-color: #4d6bfe;
  box-shadow: 0 0 0 1px rgba(77, 107, 254, 0.15);
}
.chat-input :deep(.el-input__inner) {
  color: #1a1a2e;
}
.chat-input :deep(.el-input__inner::placeholder) {
  color: #999999;
}
.chat-input :deep(.el-input-group__append) {
  background: transparent;
  border: none;
  padding: 0;
}
.chat-input :deep(.el-button--primary) {
  background: #4d6bfe;
  border: none;
  color: #fff;
}
.chat-input :deep(.el-button--primary:hover) {
  background: #5f7afe;
}
.center-input :deep(.el-input__wrapper) {
  background: #ffffff;
  border: 1px solid #d9d9d9;
  box-shadow: none;
}
.center-input :deep(.el-input__wrapper:hover) {
  border-color: #4d6bfe;
}
.center-input :deep(.el-input__wrapper.is-focus) {
  border-color: #4d6bfe;
  box-shadow: 0 0 0 1px rgba(77, 107, 254, 0.15);
}
.center-input :deep(.el-input__inner) {
  color: #1a1a2e;
}
.center-input :deep(.el-input__inner::placeholder) {
  color: #999999;
}
.center-input :deep(.el-input-group__append) {
  background: transparent;
  border: none;
  padding: 0;
}
.center-input :deep(.el-button--primary) {
  background: #4d6bfe;
  border: none;
  color: #fff;
}
.center-input :deep(.el-button--primary:hover) {
  background: #5f7afe;
}
.loading-icon { animation: spin 1s linear infinite; color: #4d6bfe; }
@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
</style>
