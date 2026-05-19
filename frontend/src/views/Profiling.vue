<template>
  <div class="profiling-view">
    <h2>性能分析</h2>
    <p class="desc">展示每次问答的端到端链路耗时分布，帮助定位性能瓶颈。</p>

    <div v-if="profilingStore.records.length === 0" class="empty">
      <p>暂无数据，请先在「智能问答」中提问。</p>
    </div>

    <div v-for="(record, idx) in profilingStore.records" :key="idx" class="record-card">
      <div class="record-header">
        <span class="record-question">{{ record.question }}</span>
        <span class="record-time">{{ formatTime(record.timestamp) }}</span>
      </div>
      <div class="timing-bar">
        <div class="bar-segment strategy" :style="{ width: pct(record, 'strategy') }">
          <span class="bar-label" v-if="showLabel(record, 'strategy')">策略 {{ record.timing.strategy }}s</span>
        </div>
        <div class="bar-segment search" :style="{ width: pct(record, 'search') }">
          <span class="bar-label" v-if="showLabel(record, 'search')">搜索 {{ record.timing.search }}s</span>
        </div>
        <div class="bar-segment extract" :style="{ width: pct(record, 'extract') }">
          <span class="bar-label" v-if="showLabel(record, 'extract')">提取 {{ record.timing.extract }}s</span>
        </div>
        <div class="bar-segment answer" :style="{ width: pct(record, 'answer') }">
          <span class="bar-label" v-if="showLabel(record, 'answer')">生成 {{ record.timing.answer }}s</span>
        </div>
      </div>
      <div class="timing-details">
        <span>总耗时 <strong>{{ record.timing.total }}s</strong></span>
        <span>搜索命中 {{ record.timing.search_results_count }} 条</span>
        <span>上下文 {{ (record.timing.context_chars / 1024).toFixed(1) }}KB</span>
        <span>LLM(策略) {{ record.timing.strategy_llm }}s</span>
        <span>LLM(回答) {{ record.timing.answer_llm }}s</span>
      </div>
    </div>

    <div v-if="profilingStore.records.length > 0" class="summary">
      <h3>瓶颈分析</h3>
      <ul>
        <li>LLM 调用占比：<strong>{{ llmPct }}%</strong>（两次 LLM 调用合计）</li>
        <li>搜索 + 提取占比：<strong>{{ searchPct }}%</strong></li>
        <li v-if="avgTotal > 10" class="warn">平均耗时 {{ avgTotal.toFixed(1) }}s，建议优化 LLM 调用（合并为单次或使用更快模型）</li>
        <li v-else>平均耗时 {{ avgTotal.toFixed(1) }}s</li>
      </ul>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { profilingStore } from '../store/profiling.js'

function pct(record, key) {
  const total = record.timing.total || 1
  return ((record.timing[key] / total) * 100).toFixed(1) + '%'
}

function showLabel(record, key) {
  return (record.timing[key] / record.timing.total) > 0.08
}

function formatTime(ts) {
  return new Date(ts).toLocaleTimeString()
}

const avgTotal = computed(() => {
  const recs = profilingStore.records
  if (!recs.length) return 0
  return recs.reduce((sum, r) => sum + r.timing.total, 0) / recs.length
})

const llmPct = computed(() => {
  const recs = profilingStore.records
  if (!recs.length) return 0
  const llmSum = recs.reduce((s, r) => s + r.timing.strategy + r.timing.answer, 0)
  const totalSum = recs.reduce((s, r) => s + r.timing.total, 0)
  return ((llmSum / totalSum) * 100).toFixed(0)
})

const searchPct = computed(() => {
  const recs = profilingStore.records
  if (!recs.length) return 0
  const searchSum = recs.reduce((s, r) => s + r.timing.search + r.timing.extract, 0)
  const totalSum = recs.reduce((s, r) => s + r.timing.total, 0)
  return ((searchSum / totalSum) * 100).toFixed(0)
})
</script>

<style scoped>
.profiling-view { max-width: 900px; }
.desc { color: #909399; margin-bottom: 24px; }
.empty { text-align: center; color: #909399; margin-top: 60px; }
.record-card {
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 16px;
}
.record-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 10px;
}
.record-question { font-weight: 500; }
.record-time { color: #909399; font-size: 12px; }
.timing-bar {
  display: flex;
  height: 28px;
  border-radius: 4px;
  overflow: hidden;
  margin-bottom: 8px;
}
.bar-segment {
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 2px;
  transition: width 0.3s;
}
.bar-label { font-size: 11px; color: white; white-space: nowrap; }
.bar-segment.strategy { background: #409eff; }
.bar-segment.search { background: #67c23a; }
.bar-segment.extract { background: #e6a23c; }
.bar-segment.answer { background: #f56c6c; }
.timing-details {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  font-size: 12px;
  color: #606266;
}
.summary {
  margin-top: 24px;
  padding: 16px;
  background: #f5f7fa;
  border-radius: 8px;
}
.summary h3 { margin-bottom: 8px; }
.summary ul { padding-left: 20px; }
.summary li { margin-bottom: 4px; }
.summary .warn { color: #e6a23c; }
</style>
