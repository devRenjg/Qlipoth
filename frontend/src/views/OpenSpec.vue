<template>
  <div class="openspec-view">
    <h2>OpenSpec</h2>
    <p class="subtitle">实时读取自仓库 openspec/ 目录</p>

    <el-tabs v-model="activeTab" v-loading="loading">
      <el-tab-pane :label="`已实现 (${specs.length})`" name="specs">
        <el-empty v-if="!loading && specs.length === 0" description="暂无已实现能力" />
        <el-collapse v-else accordion>
          <el-collapse-item v-for="cap in specs" :key="cap.name" :name="cap.name">
            <template #title>
              <span class="cap-name">{{ cap.name }}</span>
              <el-tag size="small" type="success" class="cap-count">{{ cap.requirement_count }} 需求</el-tag>
            </template>
            <p class="purpose">{{ cap.purpose }}</p>
            <div v-for="req in cap.requirements" :key="req.name" class="req-block">
              <div class="req-name">{{ req.name }}</div>
              <p class="req-desc">{{ req.description }}</p>
              <div v-for="sc in req.scenarios" :key="sc.name" class="scenario">
                <span class="scenario-name">{{ sc.name }}</span>
                <pre class="scenario-body">{{ sc.body }}</pre>
              </div>
            </div>
          </el-collapse-item>
        </el-collapse>
      </el-tab-pane>

      <el-tab-pane :label="`待实现 (${changes.length})`" name="changes">
        <el-empty v-if="!loading && changes.length === 0" description="暂无待实现提案" />
        <el-collapse v-else accordion>
          <el-collapse-item v-for="ch in changes" :key="ch.name" :name="ch.name">
            <template #title>
              <span class="cap-name">{{ ch.name }}</span>
              <el-tag size="small" :type="progressType(ch.tasks)" class="cap-count">
                {{ ch.tasks.done }}/{{ ch.tasks.total }} 任务
              </el-tag>
            </template>
            <el-progress
              v-if="ch.tasks.total"
              :percentage="pct(ch.tasks)"
              :status="ch.tasks.done === ch.tasks.total ? 'success' : ''"
              class="ch-progress"
            />
            <div class="req-block">
              <div class="req-name">Why</div>
              <p class="req-desc">{{ ch.why }}</p>
            </div>
            <div class="req-block">
              <div class="req-name">What Changes</div>
              <pre class="scenario-body">{{ ch.what_changes }}</pre>
            </div>
          </el-collapse-item>
        </el-collapse>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import axios from 'axios'

const api = axios.create({ baseURL: '/api' })
const activeTab = ref('specs')
const specs = ref([])
const changes = ref([])
const loading = ref(false)

onMounted(load)

async function load() {
  loading.value = true
  try {
    const [s, c] = await Promise.all([
      api.get('/openspec/specs'),
      api.get('/openspec/changes'),
    ])
    specs.value = s.data
    changes.value = c.data
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || '加载失败')
  } finally {
    loading.value = false
  }
}

function pct(t) {
  return t.total ? Math.round((t.done / t.total) * 100) : 0
}

function progressType(t) {
  if (!t.total) return 'info'
  if (t.done === t.total) return 'success'
  if (t.done === 0) return 'info'
  return 'warning'
}
</script>

<style scoped>
.openspec-view h2 { margin-bottom: 4px; }
.subtitle { color: #909399; font-size: 13px; margin-bottom: 20px; }
.cap-name { font-weight: 600; color: #1a1a2e; }
.cap-count { margin-left: 12px; }
.purpose { color: #555; line-height: 1.6; margin-bottom: 16px; }
.req-block { margin-bottom: 18px; padding-left: 12px; border-left: 3px solid #4d6bfe; }
.req-name { font-weight: 600; color: #1a1a2e; margin-bottom: 4px; }
.req-desc { color: #555; line-height: 1.6; margin-bottom: 8px; white-space: pre-wrap; }
.scenario { margin: 6px 0 6px 12px; }
.scenario-name { font-size: 13px; color: #4d6bfe; font-weight: 500; }
.scenario-body {
  font-family: inherit; white-space: pre-wrap; word-break: break-word;
  color: #666; font-size: 13px; line-height: 1.6; margin: 4px 0 0;
}
.ch-progress { margin-bottom: 16px; max-width: 400px; }
</style>
