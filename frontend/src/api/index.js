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

export function queryKnowledgeBaseStream(question, { onMeta, onChunk, onDone, onError }) {
  const controller = new AbortController()
  fetch('/api/query/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
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

export function saveChatHistory(question, answer, sourceUrls, userId) {
  return api.post('/chat/history', { question, answer, source_urls: sourceUrls, user_id: userId })
}

export function deleteChatHistory(id) {
  return api.delete(`/chat/history/${id}`)
}
