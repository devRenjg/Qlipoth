<template>
  <div class="chat-view">
    <div class="chat-messages" ref="messagesRef">
      <div v-if="messages.length === 0" class="empty-state">
        <div class="shield-icon">&#9670;</div>
        <h2>直播大型活动保障！有问必答！使命必达！</h2>
        <p class="subtitle">克里珀持续以光年级屏障隔绝威胁，维系现存世界的完整。</p>
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
          <span class="preset-label">快速查询：</span>
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
          <div class="message-text" v-html="msg.html || msg.text"></div>
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
</template>

<script setup>
import { ref, nextTick } from 'vue'
import { Loading } from '@element-plus/icons-vue'
import { queryKnowledgeBaseStream } from '../api/index.js'
import { addProfilingRecord } from '../store/profiling.js'

const messages = ref([])
const input = ref('')
const loading = ref(false)
const messagesRef = ref(null)

const presetQuestions = [
  '示例活动值班多少人？',
  '示例活动大概有多少需求，主要有哪些内容？',
  '26 年 CNY 版本覆盖率多少？',
]

function askPreset(q) {
  input.value = q
  sendMessage()
}

async function sendMessage() {
  const question = input.value.trim()
  if (!question || loading.value) return

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
    timing: null,
  })

  queryKnowledgeBaseStream(question, {
    onMeta(meta) {
      messages.value[msgIdx].sources = meta.sources
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
    },
    onError(err) {
      messages.value[msgIdx].text = err || '请求失败，请检查后端服务和 LLM 配置'
      messages.value[msgIdx].html = ''
      loading.value = false
    },
  })
}

function formatMarkdown(text) {
  return text
    .replace(/\n/g, '<br>')
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/`(.*?)`/g, '<code>$1</code>')
}

async function scrollToBottom() {
  await nextTick()
  if (messagesRef.value) {
    messagesRef.value.scrollTop = messagesRef.value.scrollHeight
  }
}
</script>

<style scoped>
.chat-view { display: flex; flex-direction: column; height: calc(100vh - 100px); font-size: 15px; }
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
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 10px;
  align-items: center;
}
.preset-label { font-size: 14px; color: #888888; }
.preset-tag {
  cursor: pointer;
  font-size: 14px;
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
.message-sources { margin-top: 8px; font-size: 13px; }
.source-item { padding: 4px 0; border-bottom: 1px solid #e8e8e8; color: #666666; }
.source-item code { color: #4d6bfe; margin-right: 8px; }
.message-timing { margin-top: 6px; font-size: 12px; color: #888888; }
.chat-input {
  padding: 16px 0;
  border-top: 1px solid #e8e8e8;
  display: flex;
  justify-content: center;
}
.input-wrapper {
  width: 65%;
  min-width: 400px;
  max-width: 660px;
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
