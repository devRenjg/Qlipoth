import { reactive } from 'vue'

export const profilingStore = reactive({
  records: [],
})

export function addProfilingRecord(question, timing) {
  profilingStore.records.unshift({
    question,
    timing,
    timestamp: Date.now(),
  })
  if (profilingStore.records.length > 50) {
    profilingStore.records.pop()
  }
}
