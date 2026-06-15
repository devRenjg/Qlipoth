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
              <span class="tag-filter-label">大型活动</span>
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
              <span class="tag-filter-label">主题</span>
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
          <div v-if="tags.length" class="tag-filter tag-filter-compact">
            <div v-if="activityTags.length" class="tag-row">
              <span
                v-for="t in activityTags"
                :key="t.id"
                class="tag-chip tag-chip-activity"
                :style="tagStyle(t)"
                @click="toggleTag(t.id)"
              >{{ t.name }} <em class="tag-chip-count">{{ t.doc_count }}</em></span>
            </div>
            <div class="tag-row">
              <span
                v-for="t in topicTags"
                :key="t.id"
                class="tag-chip"
                :style="tagStyle(t)"
                @click="toggleTag(t.id)"
              >{{ t.name }} <em class="tag-chip-count">{{ t.doc_count }}</em></span>
            </div>
          </div>
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

<script setup>
import { ref, inject, nextTick, onMounted, computed } from 'vue'
import { Loading } from '@element-plus/icons-vue'
import { queryKnowledgeBaseStream, getConversations, getConversation, saveChatHistory, getTags } from '../api/index.js'
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

const presetQuestions = [
  '示例活动值班多少人？',
  '示例活动大概有多少需求，主要有哪些内容？',
  '示例需求上线后的版本覆盖率多少，对比去年如何？',
]

onMounted(() => { loadConversations(); loadTags() })

async function loadConversations() {
  try {
    const role = currentUser?.value?.role
    const userId = (role === 'admin' || role === 'super') ? null : currentUser?.value?.id
    const { data } = await getConversations(userId)
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
  try {
    const { data } = await getConversation(item.conversation_id)
    const msgs = []
    for (const turn of data) {
      msgs.push({ role: 'user', text: turn.question })
      msgs.push({
        role: 'assistant',
        text: turn.answer,
        html: formatMarkdown(turn.answer),
        sourceUrls: turn.source_urls || [],
        timing: null,
      })
    }
    messages.value = msgs
    // legacy 单轮伪会话不可续聊（无真实 conversation_id），续聊另起新会话
    conversationId.value = item.conversation_id.startsWith('legacy-') ? null : item.conversation_id
    await scrollToBottom()
  } catch {}
}

function newChat() {
  messages.value = []
  conversationId.value = null
  input.value = ''
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

  const msgIdx = messages.value.length
  messages.value.push({
    role: 'assistant',
    text: '',
    html: '',
    sources: [],
    sourceUrls: [],
    images: [],
    timing: null,
  })

  let finalSourceUrls = []

  queryKnowledgeBaseStream(question, {
    onMeta(meta) {
      messages.value[msgIdx].sources = meta.sources
      messages.value[msgIdx].sourceUrls = meta.source_urls || []
      messages.value[msgIdx].images = meta.relevant_images || []
      finalSourceUrls = meta.source_urls || []
    },
    onChunk(chunk) {
      messages.value[msgIdx].text += chunk
      messages.value[msgIdx].html = formatMarkdown(messages.value[msgIdx].text)
      scrollToBottom()
    },
    onDone(timing) {
      messages.value[msgIdx].timing = timing
      addProfilingRecord(question, timing)
      loading.value = false
      scrollToBottom()
      // Save to history
      const answer = messages.value[msgIdx].text
      const userId = currentUser?.value?.id || null
      saveChatHistory(question, answer, finalSourceUrls, userId, convId, tagSnapshot)
        .then(() => loadConversations()).catch(() => {})
    },
    onError(err) {
      messages.value[msgIdx].text = err || '请求失败，请检查后端服务和 LLM 配置'
      messages.value[msgIdx].html = ''
      loading.value = false
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
  gap: 10px;
}
.tag-filter-label {
  flex: 0 0 64px;
  width: 64px;
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
.tag-filter-compact {
  margin-top: 0;
  margin-bottom: 10px;
  align-items: flex-start;
  max-width: none;
}
.tag-filter-compact .tag-row { flex-wrap: wrap; justify-content: flex-start; }
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
.message { margin-bottom: 16px; display: flex; }
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
.message.assistant .message-content {
  background: #f7f7f8;
  color: #1a1a2e;
  border: 1px solid #e8e8e8;
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
