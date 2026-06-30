<template>
  <div class="m712">
    <div class="m712-head">
      <h2>🎬 猫耳 712 直播活动 · 方案盲点提醒</h2>
      <p class="sub">基于 B 站大型直播活动保障经验（作战地图），对比猫耳 712 站庆方案，提示尚未覆盖或考虑不足的风险点</p>
    </div>

    <div v-loading="loading">
      <template v-if="data">
        <!-- 总体评价 -->
        <div class="card summary-card">
          <div class="card-h">📋 总体评价</div>
          <p class="summary-text">{{ data.summary }}</p>
        </div>

        <!-- 已覆盖 -->
        <div class="card covered-card" v-if="data.maoer_covered?.length">
          <div class="card-h">✅ 猫耳已较好覆盖</div>
          <ul class="covered-list">
            <li v-for="(c, i) in data.maoer_covered" :key="i">{{ c }}</li>
          </ul>
        </div>

        <!-- TODO 提醒 -->
        <div class="todos-head">
          <span class="todos-title">⚠️ 风险盲点 TODO（{{ data.todos?.length || 0 }} 条）</span>
          <span class="filter-chips">
            <span v-for="s in ['全部','高','中','低']" :key="s"
                  class="chip" :class="{ on: sevFilter === s }" @click="sevFilter = s">{{ s }}</span>
          </span>
        </div>

        <div v-for="(t, i) in filteredTodos" :key="i" class="card todo-card" :class="'sev-' + t.severity">
          <div class="todo-top">
            <span class="sev-badge" :class="'sev-' + t.severity">{{ t.severity }}</span>
            <span class="todo-dim">{{ t.dimension }}</span>
            <span class="todo-title">{{ t.title }}</span>
          </div>
          <div class="todo-row"><span class="lbl">风险</span><span class="val">{{ t.risk }}</span></div>
          <div class="todo-row"><span class="lbl exp">我们的经验</span><span class="val">{{ t.our_experience }}</span></div>
          <div class="todo-row"><span class="lbl sug">建议</span><span class="val">{{ t.suggestion }}</span></div>
        </div>
      </template>
      <el-empty v-else-if="!loading" description="尚未生成对比分析" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import axios from 'axios'

const api = axios.create({ baseURL: '/api' })
const data = ref(null)
const loading = ref(false)
const sevFilter = ref('全部')

const filteredTodos = computed(() => {
  if (!data.value?.todos) return []
  if (sevFilter.value === '全部') return data.value.todos
  return data.value.todos.filter(t => t.severity === sevFilter.value)
})

onMounted(async () => {
  loading.value = true
  try {
    const { data: d } = await api.get('/maoer712/todos')
    data.value = d
  } catch (e) { /* 静默 */ } finally { loading.value = false }
})
</script>

<style scoped>
.m712 { max-width: 1000px; margin: 0 auto; padding: 16px 20px 40px; }
.m712-head h2 { margin: 0 0 6px; font-size: 22px; color: #1a2b4a; }
.m712-head .sub { color: #6b7a90; font-size: 13px; margin: 0 0 18px; line-height: 1.6; }
.card { background: #fff; border: 1px solid #e3e8f0; border-radius: 10px; padding: 16px 18px; margin-bottom: 14px; }
.card-h { font-weight: 600; font-size: 15px; color: #2f4368; margin-bottom: 10px; }
.summary-card { background: linear-gradient(180deg,#f5f9ff,#fff); border-color: #cdddf6; }
.summary-text { margin: 0; line-height: 1.8; color: #3a4a63; font-size: 13.5px; }
.covered-card { background: linear-gradient(180deg,#f3fbf5,#fff); border-color: #cfead6; }
.covered-list { margin: 0; padding-left: 20px; }
.covered-list li { line-height: 1.8; color: #3a6347; font-size: 13px; }
.todos-head { display: flex; align-items: center; justify-content: space-between; margin: 22px 0 12px; }
.todos-title { font-size: 16px; font-weight: 600; color: #b3401a; }
.filter-chips { display: inline-flex; gap: 6px; }
.chip { cursor: pointer; font-size: 12px; padding: 3px 12px; border-radius: 12px; border: 1px solid #dce0e6; color: #6b7a90; user-select: none; }
.chip.on { background: #2f6bd6; color: #fff; border-color: #2f6bd6; }
.todo-card { border-left: 4px solid #d0d5dd; }
.todo-card.sev-高 { border-left-color: #e04b4b; }
.todo-card.sev-中 { border-left-color: #e8a33d; }
.todo-card.sev-低 { border-left-color: #6aa9e0; }
.todo-top { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; }
.sev-badge { font-size: 11px; font-weight: 700; padding: 1px 8px; border-radius: 4px; color: #fff; }
.sev-badge.sev-高 { background: #e04b4b; }
.sev-badge.sev-中 { background: #e8a33d; }
.sev-badge.sev-低 { background: #6aa9e0; }
.todo-dim { font-size: 11.5px; color: #5a7bb0; background: #eef4ff; padding: 1px 8px; border-radius: 4px; }
.todo-title { font-weight: 600; font-size: 14.5px; color: #1a2b4a; }
.todo-row { display: flex; gap: 10px; margin: 6px 0; font-size: 13px; line-height: 1.7; }
.todo-row .lbl { flex: 0 0 64px; color: #909399; font-size: 12px; padding-top: 1px; }
.todo-row .lbl.exp { color: #2f6bd6; }
.todo-row .lbl.sug { color: #2f9e5e; }
.todo-row .val { flex: 1; color: #3a4a63; }
</style>
