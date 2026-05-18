<template>
  <div class="settings-view">
    <h2>LLM 设置</h2>
    <p class="desc">配置大语言模型接口，支持 OpenAI 兼容格式的任意服务</p>

    <el-form :model="form" label-width="120px" style="max-width: 600px" v-loading="loading">
      <el-form-item label="API Base URL">
        <el-input v-model="form.llm_base_url" placeholder="https://api.openai.com/v1" />
      </el-form-item>
      <el-form-item label="API Key">
        <el-input v-model="form.llm_api_key" type="password" show-password placeholder="sk-..." />
      </el-form-item>
      <el-form-item label="模型名称">
        <el-input v-model="form.llm_model" placeholder="gpt-4o" />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" @click="save">保存设置</el-button>
      </el-form-item>
    </el-form>

    <el-divider />
    <h3>使用说明</h3>
    <ul class="tips">
      <li>支持任何 OpenAI 兼容接口（OpenAI、Claude via proxy、本地 Ollama 等）</li>
      <li>Ollama 本地部署：Base URL 填 <code>http://localhost:11434/v1</code>，API Key 填 <code>ollama</code></li>
      <li>API Key 会保存在服务器本地 config.json 中</li>
    </ul>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getSettings, updateSettings } from '../api/index.js'

const form = ref({ llm_base_url: '', llm_api_key: '', llm_model: '' })
const loading = ref(false)

onMounted(async () => {
  loading.value = true
  try {
    const { data } = await getSettings()
    form.value.llm_base_url = data.llm_base_url
    form.value.llm_model = data.llm_model
  } finally {
    loading.value = false
  }
})

async function save() {
  await updateSettings(form.value)
  ElMessage.success('设置已保存')
}
</script>

<style scoped>
.settings-view h2 { margin-bottom: 8px; }
.desc { color: #909399; margin-bottom: 24px; }
.tips { padding-left: 20px; line-height: 2; color: #606266; }
.tips code { background: #f4f4f5; padding: 2px 6px; border-radius: 3px; }
</style>
