<template>
  <div class="settings-view">
    <h2>LLM 设置</h2>
    <p class="desc">配置大语言模型接口，支持 OpenAI 兼容格式及 Anthropic 格式</p>

    <el-form :model="form" label-width="120px" style="max-width: 600px" v-loading="loading">
      <el-form-item label="快速配置">
        <el-select v-model="preset" placeholder="选择预设..." @change="applyPreset" style="width: 100%">
          <el-option label="自定义" value="" />
          <el-option label="DeepSeek" value="deepseek" />
          <el-option label="通义千问 (Qwen)" value="qwen" />
          <el-option label="智谱 (GLM)" value="zhipu" />
          <el-option label="月之暗面 (Kimi)" value="moonshot" />
          <el-option label="OpenAI" value="openai" />
          <el-option label="Anthropic Claude" value="anthropic" />
          <el-option label="本地 Ollama" value="ollama" />
        </el-select>
      </el-form-item>
      <el-form-item label="API 格式">
        <el-radio-group v-model="form.llm_api_format">
          <el-radio value="openai">OpenAI 兼容</el-radio>
          <el-radio value="anthropic">Anthropic</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="API Base URL">
        <el-input v-model="form.llm_base_url" placeholder="https://api.deepseek.com" />
      </el-form-item>
      <el-form-item label="API Key">
        <el-input v-model="form.llm_api_key" type="password" show-password placeholder="sk-..." />
      </el-form-item>
      <el-form-item label="模型名称">
        <el-input v-model="form.llm_model" placeholder="deepseek-chat" />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" @click="save">保存设置</el-button>
      </el-form-item>
    </el-form>

    <el-divider />
    <h3>支持的模型服务</h3>
    <el-table :data="providers" size="small" style="max-width: 700px">
      <el-table-column prop="name" label="服务商" width="140" />
      <el-table-column prop="url" label="Base URL" />
      <el-table-column prop="models" label="推荐模型" />
    </el-table>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getSettings, updateSettings } from '../api/index.js'

const form = ref({ llm_base_url: '', llm_api_key: '', llm_model: '', llm_api_format: 'openai' })
const loading = ref(false)
const preset = ref('')

const presets = {
  deepseek: { llm_base_url: 'https://api.deepseek.com', llm_model: 'deepseek-chat', llm_api_format: 'openai' },
  qwen: { llm_base_url: 'https://dashscope.aliyuncs.com/compatible-mode', llm_model: 'qwen-plus', llm_api_format: 'openai' },
  zhipu: { llm_base_url: 'https://open.bigmodel.cn/api/paas', llm_model: 'glm-4-flash', llm_api_format: 'openai' },
  moonshot: { llm_base_url: 'https://api.moonshot.cn', llm_model: 'moonshot-v1-8k', llm_api_format: 'openai' },
  openai: { llm_base_url: 'https://api.openai.com', llm_model: 'gpt-4o', llm_api_format: 'openai' },
  anthropic: { llm_base_url: 'https://api.anthropic.com', llm_model: 'claude-sonnet-4-6', llm_api_format: 'anthropic' },
  ollama: { llm_base_url: 'http://localhost:11434', llm_model: 'qwen2.5', llm_api_format: 'openai' },
}

const providers = [
  { name: 'DeepSeek', url: 'https://api.deepseek.com', models: 'deepseek-chat, deepseek-reasoner' },
  { name: '通义千问', url: 'https://dashscope.aliyuncs.com/compatible-mode', models: 'qwen-plus, qwen-max, qwen-turbo' },
  { name: '智谱 GLM', url: 'https://open.bigmodel.cn/api/paas', models: 'glm-4-flash, glm-4-plus' },
  { name: '月之暗面', url: 'https://api.moonshot.cn', models: 'moonshot-v1-8k, moonshot-v1-32k' },
  { name: 'OpenAI', url: 'https://api.openai.com', models: 'gpt-4o, gpt-4o-mini' },
  { name: 'Anthropic', url: 'https://api.anthropic.com', models: 'claude-sonnet-4-6, claude-haiku-4-5' },
  { name: 'Ollama (本地)', url: 'http://localhost:11434', models: 'qwen2.5, llama3, deepseek-v2' },
]

function applyPreset(key) {
  if (key && presets[key]) {
    Object.assign(form.value, presets[key])
  }
}

onMounted(async () => {
  loading.value = true
  try {
    const { data } = await getSettings()
    form.value.llm_base_url = data.llm_base_url
    form.value.llm_model = data.llm_model
    form.value.llm_api_format = data.llm_api_format || 'openai'
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
</style>
