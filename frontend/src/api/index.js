import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

export function uploadFile(file) {
  const form = new FormData()
  form.append('file', file)
  return api.post('/upload', form)
}

export function queryKnowledgeBase(question) {
  return api.post('/query', { question })
}

export function queryKnowledgeBaseStream(question, { onMeta, onChunk, onDone, onError }, conversationId, tagIds) {
  const controller = new AbortController()
  fetch('/api/query/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      question,
      conversation_id: conversationId || null,
      tag_ids: tagIds || [],
    }),
    signal: controller.signal,
  }).then(async (resp) => {
    if (!resp.ok) {
      const text = await resp.text()
      onError(text)
      return
    }
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop()
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        try {
          const msg = JSON.parse(line.slice(6))
          if (msg.type === 'meta') onMeta(msg.data)
          else if (msg.type === 'chunk') onChunk(msg.data)
          else if (msg.type === 'done') onDone(msg.data)
          else if (msg.type === 'error') onError(msg.data)
        } catch {}
      }
    }
  }).catch((err) => {
    if (err.name !== 'AbortError') onError(err.message)
  })
  return controller
}

export function simpleSearch(q) {
  return api.get('/search', { params: { q } })
}

export function getDocuments() {
  return api.get('/documents')
}

export function getDocument(id) {
  return api.get(`/documents/${id}`)
}

export function deleteDocument(id) {
  return api.delete(`/documents/${id}`)
}

export function getTags() {
  return api.get('/tags')
}

export function createTag(name, description) {
  return api.post('/tags', { name, description })
}

export function renameTag(id, name, description) {
  return api.put(`/tags/${id}`, { name, description })
}

export function deleteTag(id) {
  return api.delete(`/tags/${id}`)
}

export function setDocumentTags(docId, tagIds) {
  return api.put(`/documents/${docId}/tags`, { tag_ids: tagIds })
}

export function getSettings() {
  return api.get('/settings')
}

export function updateSettings(data) {
  return api.put('/settings', data)
}

export function getCurrentUser() {
  return api.get('/user/me')
}

export function registerUser(username, password) {
  return api.post('/user/register', { username, password })
}

export function loginUser(username, password) {
  return api.post('/user/login', { username, password })
}

export function logoutUser() {
  return api.post('/user/logout')
}

export function getChatHistory(userId) {
  const params = userId ? { user_id: userId } : {}
  return api.get('/chat/history', { params })
}

export function saveChatHistory(question, answer, sourceUrls, userId, conversationId, selectedTags) {
  return api.post('/chat/history', {
    question, answer, source_urls: sourceUrls, user_id: userId,
    conversation_id: conversationId || null,
    selected_tags: selectedTags || [],
  })
}

export function getConversations(userId) {
  const params = userId ? { user_id: userId } : {}
  return api.get('/chat/conversations', { params })
}

export function getConversation(conversationId) {
  return api.get(`/chat/conversation/${conversationId}`)
}

export function deleteChatHistory(id) {
  return api.delete(`/chat/history/${id}`)
}

// 保障清单（历史踩坑预警）
export function generateChecklist(activity, title) {
  return api.post('/checklist/generate', { activity, title })
}
export function getChecklistProgress(id) {
  return api.get(`/checklist/generate/${id}/progress`)
}
export function listChecklists() {
  return api.get('/checklist/list')
}
export function getChecklist(id) {
  return api.get(`/checklist/${id}`)
}
export function updateChecklistItem(itemId, data) {
  return api.patch(`/checklist/item/${itemId}`, data)
}
export function addChecklistItem(checklistId, data) {
  return api.post(`/checklist/${checklistId}/item`, data)
}
export function deleteChecklistItem(itemId) {
  return api.delete(`/checklist/item/${itemId}`)
}
export function deleteChecklist(id) {
  return api.delete(`/checklist/${id}`)
}
export function exportChecklistToWecom(id, itemIds, title) {
  return api.post(`/checklist/${id}/export-wecom`, { item_ids: itemIds || [], title: title || null }, { timeout: 120000 })
}

// 埋点上报：action 命中后端白名单(访问页面/查看内容)；fire-and-forget，失败不打扰用户
export function trackActivity(action, detail) {
  try {
    api.post('/activity/track', { action, detail: detail || '' }).catch(() => {})
  } catch { /* 埋点绝不影响主流程 */ }
}
