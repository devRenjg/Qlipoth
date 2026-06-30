<template>
  <div class="case-wrap">
    <div class="case-head">
      <h2>📊 案例分析</h2>
      <p class="sub">基于大型活动保障经验，对外部活动方案做盲点对比分析，沉淀可复用的风险检查案例</p>
    </div>

    <div class="case-grid">
      <!-- 猫耳712 案例卡片 -->
      <div class="case-card" @click="goCase('/maoer712')">
        <div class="case-card-top">
          <span class="case-icon">🎬</span>
          <span class="case-title">猫耳712 直播活动方案盲点提醒</span>
        </div>
        <p class="case-desc">对比 B 站大型直播活动保障经验，提示猫耳 712 站庆方案尚未覆盖或考虑不足的风险点</p>
        <div class="case-stats" v-if="m712">
          <span class="stat total">风险盲点 {{ m712.total }} 条</span>
          <span class="stat sev-高" v-if="m712.high">高 {{ m712.high }}</span>
          <span class="stat sev-中" v-if="m712.mid">中 {{ m712.mid }}</span>
          <span class="stat sev-低" v-if="m712.low">低 {{ m712.low }}</span>
        </div>
        <div class="case-stats" v-else>
          <span class="stat muted">加载中…</span>
        </div>
        <span class="case-go">查看详情 →</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import axios from 'axios'

const api = axios.create({ baseURL: '/api' })
const router = useRouter()
const m712 = ref(null)

const goCase = (path) => router.push(path)

onMounted(async () => {
  try {
    const { data } = await api.get('/maoer712/todos')
    const todos = data.todos || []
    m712.value = {
      total: todos.length,
      high: todos.filter(t => t.severity === '高').length,
      mid: todos.filter(t => t.severity === '中').length,
      low: todos.filter(t => t.severity === '低').length,
    }
  } catch (e) {
    m712.value = { total: 0, high: 0, mid: 0, low: 0 }
  }
})
</script>

<style scoped>
.case-wrap { max-width: 1100px; margin: 0 auto; padding: 16px 20px 40px; }
.case-head h2 { margin: 0 0 6px; font-size: 22px; color: #1a2b4a; }
.case-head .sub { color: #6b7a90; font-size: 13px; margin: 0 0 22px; line-height: 1.6; }
.case-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }
.case-card {
  background: #fff; border: 1px solid #e3e8f0; border-radius: 12px; padding: 18px 20px;
  cursor: pointer; transition: all .18s ease; position: relative; display: flex; flex-direction: column;
}
.case-card:hover { border-color: #2f6bd6; box-shadow: 0 6px 20px rgba(47,107,214,.12); transform: translateY(-2px); }
.case-card-top { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.case-icon { font-size: 22px; }
.case-title { font-weight: 600; font-size: 15.5px; color: #1a2b4a; line-height: 1.4; }
.case-desc { margin: 0 0 14px; color: #5a6a82; font-size: 13px; line-height: 1.7; flex: 1; }
.case-stats { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; }
.stat { font-size: 12px; padding: 2px 10px; border-radius: 12px; font-weight: 600; }
.stat.total { background: #eef4ff; color: #2f6bd6; }
.stat.muted { background: #f4f4f5; color: #909399; font-weight: 400; }
.stat.sev-高 { background: #fdeaea; color: #e04b4b; }
.stat.sev-中 { background: #fdf3e3; color: #c8842a; }
.stat.sev-低 { background: #eaf3fc; color: #3f86cc; }
.case-go { font-size: 12.5px; color: #2f6bd6; font-weight: 600; align-self: flex-end; }
</style>
