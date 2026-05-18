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
