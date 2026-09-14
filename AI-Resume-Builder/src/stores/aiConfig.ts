import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'

export interface AiConfig {
  useBackendSpeech: boolean
  backendSpeechAutoDisabled: boolean
}

const STORAGE_KEY = 'resume-builder-ai-config'

export const useAiConfigStore = defineStore('aiConfig', () => {
  const useBackendSpeech = ref(true)
  const backendSpeechAutoDisabled = ref(false)

  const shouldRequestBackendSpeech = computed(
    () => useBackendSpeech.value && !backendSpeechAutoDisabled.value,
  )

  function saveToStorage() {
    // 只保存个人语音偏好；历史版本中的模型连接配置会在首次加载时被覆盖清除。
    const data: AiConfig = {
      useBackendSpeech: useBackendSpeech.value,
      backendSpeechAutoDisabled: backendSpeechAutoDisabled.value,
    }
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(data))
    } catch {
      // 存储受限时仅保持当前页面偏好，不阻断面试流程。
    }
  }

  function loadFromStorage() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY)
      if (raw) {
        const data = JSON.parse(raw) as Partial<AiConfig>
        if (typeof data.useBackendSpeech === 'boolean') useBackendSpeech.value = data.useBackendSpeech
        if (typeof data.backendSpeechAutoDisabled === 'boolean') {
          backendSpeechAutoDisabled.value = data.backendSpeechAutoDisabled
        }
      }
    } catch {
      // 历史值损坏时回退到安全默认值。
    } finally {
      // 无论是否存在旧版本数据，都只写入不含密钥的个人偏好。
      saveToStorage()
    }
  }

  function setUseBackendSpeech(enabled: boolean) {
    useBackendSpeech.value = enabled
    if (enabled) backendSpeechAutoDisabled.value = false
    saveToStorage()
  }

  function markBackendSpeechUnavailable() {
    if (!useBackendSpeech.value) return
    backendSpeechAutoDisabled.value = true
    saveToStorage()
  }

  function clearBackendSpeechUnavailable() {
    backendSpeechAutoDisabled.value = false
    saveToStorage()
  }

  function clearConfig() {
    useBackendSpeech.value = true
    backendSpeechAutoDisabled.value = false
    try {
      localStorage.removeItem(STORAGE_KEY)
    } catch {
      // 清理失败不影响当前内存态。
    }
  }

  loadFromStorage()

  watch([useBackendSpeech, backendSpeechAutoDisabled], saveToStorage)

  return {
    useBackendSpeech,
    backendSpeechAutoDisabled,
    shouldRequestBackendSpeech,
    setUseBackendSpeech,
    markBackendSpeechUnavailable,
    clearBackendSpeechUnavailable,
    clearConfig,
  }
})
