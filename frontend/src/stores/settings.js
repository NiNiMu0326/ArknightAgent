import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '../api'

export const useSettingsStore = defineStore('settings', () => {
  const theme = ref('dark')
  const currentModel = ref('')
  const availableModels = ref([])

  function loadSettings() {
    const saved = localStorage.getItem('arknights_rag_settings')
    if (saved) {
      try {
        const settings = JSON.parse(saved)
        theme.value = settings.theme || 'dark'
        currentModel.value = settings.currentModel || ''
      } catch (e) {
        console.warn('Failed to parse settings from localStorage:', e)
        theme.value = 'dark'
        currentModel.value = ''
      }
    }
  }

  function saveSettings() {
    localStorage.setItem('arknights_rag_settings', JSON.stringify({
      theme: theme.value,
      currentModel: currentModel.value
    }))
  }

  function toggleTheme() {
    theme.value = theme.value === 'dark' ? 'light' : 'dark'
    saveSettings()
  }

  function setModel(modelId) {
    currentModel.value = modelId
    saveSettings()
  }

  async function loadModels() {
    try {
      const res = await api.getModels()
      availableModels.value = res.models || []
      // 本地持久化的模型 id 可能已下线（如服务端改名），失效时清空以回落到默认模型
      const ids = availableModels.value.map(m => m.id)
      if (availableModels.value.length > 0 && currentModel.value && !ids.includes(currentModel.value)) {
        currentModel.value = ''
      }
      if (!currentModel.value) {
        currentModel.value = res.default || (res.models[0]?.id ?? '')
      }
    } catch (e) {
      console.warn('Failed to load models:', e)
    }
  }

  loadSettings()

  return {
    theme,
    currentModel,
    availableModels,
    toggleTheme,
    setModel,
    saveSettings,
    loadModels
  }
})
