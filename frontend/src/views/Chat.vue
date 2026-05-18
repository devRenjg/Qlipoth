<template>
  <div class="chat-view">
    <div class="chat-messages" ref="messagesRef">
      <div v-if="messages.length === 0" class="empty-state">
        <h2>Qlipoth</h2>
        <p>输入问题，我会从知识库中搜索并回答</p>
      </div>
      <div v-for="(msg, i) in messages" :key="i" :class="['message', msg.role]">
        <div class="message-content">
          <div class="message-text" v-html="msg.html || msg.text"></div>
          <div v-if="msg.sources && msg.sources.length" class="message-sources">
            <el-collapse>
              <el-collapse-item title="引用来源">
                <div v-for="(s, j) in msg.sources" :key="j" class="source-item">
                  <code>{{ s.file }}:{{ s.line }}</code> {{ s.content }}
                </div>
              </el-collapse-item>
            </el-collapse>
          </div>
        </div>
      </div>
      <div v-if="loading" class="message assistant">
        <div class="message-content">
          <el-icon class="loading-icon"><Loading /></el-icon> 正在搜索知识库并生成回答...
        </div>
      </div>
    </div>
    <div class="chat-input">
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
</template>

<script setup>
import { ref, nextTick } from 'vue'
import { Loading } from '@element-plus/icons-vue'
import { queryKnowledgeBase } from '../api/index.js'

const messages = ref([])
const input = ref('')
const loading = ref(false)
const messagesRef = ref(null)

async function sendMessage() {
  const question = input.value.trim()
  if (!question || loading.value) return

  messages.value.push({ role: 'user', text: question })
  input.value = ''
  loading.value = true
  await scrollToBottom()

  try {
    const { data } = await queryKnowledgeBase(question)
    messages.value.push({
      role: 'assistant',
      text: data.answer,
      html: formatMarkdown(data.answer),
      sources: data.sources,
    })
  } catch (err) {
    const msg = err.response?.data?.detail || '请求失败，请检查后端服务和 LLM 配置'
    messages.value.push({ role: 'assistant', text: msg })
  } finally {
    loading.value = false
    await scrollToBottom()
  }
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
.chat-view { display: flex; flex-direction: column; height: calc(100vh - 100px); }
.chat-messages { flex: 1; overflow-y: auto; padding: 20px 0; }
.empty-state { text-align: center; margin-top: 120px; color: #909399; }
.empty-state h2 { font-size: 28px; margin-bottom: 12px; color: #303133; }
.message { margin-bottom: 16px; display: flex; }
.message.user { justify-content: flex-end; }
.message.assistant { justify-content: flex-start; }
.message-content {
  max-width: 75%;
  padding: 12px 16px;
  border-radius: 12px;
  line-height: 1.6;
}
.message.user .message-content { background: #409eff; color: white; }
.message.assistant .message-content { background: #f4f4f5; color: #303133; }
.message-sources { margin-top: 8px; font-size: 12px; }
.source-item { padding: 4px 0; border-bottom: 1px solid #ebeef5; }
.source-item code { color: #409eff; margin-right: 8px; }
.chat-input { padding-top: 16px; border-top: 1px solid #e4e7ed; }
.loading-icon { animation: spin 1s linear infinite; }
@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
</style>
