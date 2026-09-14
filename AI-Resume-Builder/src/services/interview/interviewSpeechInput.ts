import { ref, type Ref } from 'vue'
import type { ChatMessage } from '@/components/ai/interview/types'
import {
  BrowserSpeechTranscriptionSession,
  type SpeechRuntimeState,
  type SpeechSession,
} from '@/services/browserSpeechService'
import { RealtimeTranscriptionSession } from '@/services/realtimeSpeechService'
import { useAiConfigStore } from '@/stores/aiConfig'
import { INTERVIEW_PANEL_TEXT, formatErrorMessage } from './interviewPanelText'

export type SpeechEngine = 'realtime' | 'browser'
export type SpeechUiState = Exclude<SpeechRuntimeState, 'closed'> | 'idle'

// 连续失败达到该次数后自动停用后端实时语音，避免每轮都等待一次必然失败的连接。
const BACKEND_SPEECH_AUTO_DISABLE_THRESHOLD = 2

// 语音输入独立成模块：内部持有会话句柄与转写状态，外部只传入输入框、错误提示和上下文来源。
export function createInterviewSpeechInput(options: {
  inputText: Ref<string>
  errorText: Ref<string>
  messages: Ref<ChatMessage[]>
  sessionStarted: Ref<boolean>
  isLoading: Ref<boolean>
}) {
  const aiConfigStore = useAiConfigStore()

  const isListening = ref(false)
  const speechUiState = ref<SpeechUiState>('idle')
  const activeSpeechEngine = ref<SpeechEngine | null>(null)

  let speechSession: SpeechSession | null = null
  let switchingSpeechEngine = false
  let speechInputPrefix = ''
  let speechTranscript = ''
  let backendSpeechFailureCount = 0

  function resetBackendSpeechFailureState() {
    backendSpeechFailureCount = 0
  }

  // 只有用户显式启用后端实时语音时才累计失败次数，浏览器识别失败不计入。
  function trackBackendSpeechFailure(): boolean {
    if (!aiConfigStore.useBackendSpeech) {
      return false
    }

    backendSpeechFailureCount += 1
    if (backendSpeechFailureCount < BACKEND_SPEECH_AUTO_DISABLE_THRESHOLD) {
      return false
    }

    aiConfigStore.markBackendSpeechUnavailable()
    return true
  }

  function resolveEngineLabel(engine: SpeechEngine | null): string {
    if (engine === 'realtime') return INTERVIEW_PANEL_TEXT.speechRealtimeLabel
    if (engine === 'browser') return INTERVIEW_PANEL_TEXT.speechBrowserLabel
    return aiConfigStore.shouldRequestBackendSpeech
      ? INTERVIEW_PANEL_TEXT.speechPreferredLabel
      : INTERVIEW_PANEL_TEXT.speechBrowserLabel
  }

  // 转写结果始终重写「原始输入前缀 + 转写正文」，避免中途切换引擎后正文重复叠加。
  function mergeSpeechToInput() {
    const transcript = speechTranscript.trim()
    if (!transcript) {
      options.inputText.value = speechInputPrefix
      return
    }
    options.inputText.value = speechInputPrefix ? `${speechInputPrefix}\n${transcript}` : transcript
  }

  function buildSpeechCallbacks(engine: SpeechEngine) {
    return {
      onPartialText(text: string) {
        speechTranscript = text
        mergeSpeechToInput()
      },
      onFinalText(_segment: string, mergedText: string) {
        speechTranscript = mergedText
        mergeSpeechToInput()
      },
      onError(message: string) {
        if (engine === 'realtime') {
          void handleRealtimeSpeechError(message)
          return
        }
        options.errorText.value = message
        stopSpeechSafely(false)
      },
      onStateChange(state: SpeechRuntimeState) {
        speechUiState.value = state === 'closed' ? 'idle' : state
        isListening.value = state === 'connected' || state === 'connecting'
      },
    }
  }

  // 实时语音把最近若干条消息作为上下文交给后端，便于服务端做断句与纠错。
  async function createSpeechSession(engine: SpeechEngine): Promise<SpeechSession> {
    if (engine === 'realtime') {
      return new RealtimeTranscriptionSession({
        context: options.messages.value
          .filter((item) => item.content.trim())
          // 多数面试记录以 AI 的开场问题开始。多取几条交给后端配对，
          // 才能稳定得到最近 5 轮完整的“用户回答 + AI 回复”。
          .slice(-20)
          .map((item) => ({
            role: item.role === 'assistant' ? 'assistant' as const : 'user' as const,
            content: item.content.slice(0, 400),
          })),
        callbacks: buildSpeechCallbacks('realtime'),
      })
    }

    return new BrowserSpeechTranscriptionSession({
      language: 'zh-CN',
      callbacks: buildSpeechCallbacks('browser'),
    })
  }

  async function activateSpeechEngine(engine: SpeechEngine) {
    const session = await createSpeechSession(engine)
    speechSession = session
    activeSpeechEngine.value = engine
    speechUiState.value = 'connecting'
    try {
      await session.start()
      if (engine === 'realtime') {
        resetBackendSpeechFailureState()
        aiConfigStore.clearBackendSpeechUnavailable()
      }
    } catch (error) {
      speechSession = null
      activeSpeechEngine.value = null
      speechUiState.value = 'idle'
      throw error
    }
  }

  async function stopSpeech(clearSpeechText: boolean) {
    const session = speechSession
    speechSession = null

    if (session) {
      await session.stop({ silent: clearSpeechText })
    }

    isListening.value = false
    speechUiState.value = 'idle'
    if (clearSpeechText) {
      speechTranscript = ''
      options.inputText.value = speechInputPrefix
    }
    speechInputPrefix = ''
    activeSpeechEngine.value = null
  }

  function stopSpeechSafely(clearSpeechText: boolean) {
    void stopSpeech(clearSpeechText).catch(() => {
      isListening.value = false
      speechUiState.value = 'idle'
      activeSpeechEngine.value = null
    })
  }

  // 后端实时语音不可用时自动降级到浏览器识别，并保留用户已经输入或转写的内容。
  async function trySwitchToBrowserSpeech(reason: string, trackBackendFailure = false): Promise<boolean> {
    if (switchingSpeechEngine) {
      return false
    }

    switchingSpeechEngine = true
    const backendSpeechAutoDisabled = trackBackendFailure ? trackBackendSpeechFailure() : false
    const autoDisabledNotice = backendSpeechAutoDisabled ? `\n${INTERVIEW_PANEL_TEXT.speechAutoDisabledNotice}` : ''
    try {
      const preservedInput = options.inputText.value.trim()
      await stopSpeech(false)
      speechInputPrefix = preservedInput
      speechTranscript = ''
      options.inputText.value = preservedInput
      await activateSpeechEngine('browser')
      options.errorText.value = `${INTERVIEW_PANEL_TEXT.switchedToBrowserSpeech}\n${reason}${autoDisabledNotice}`
      return true
    } catch (fallbackError) {
      const fallbackMessage = formatErrorMessage(fallbackError)
      options.errorText.value = `${INTERVIEW_PANEL_TEXT.speechUnavailable}\n${reason}${autoDisabledNotice}\n${fallbackMessage}`
      return false
    } finally {
      switchingSpeechEngine = false
    }
  }

  async function handleRealtimeSpeechError(message: string) {
    if (activeSpeechEngine.value === 'realtime') {
      const switched = await trySwitchToBrowserSpeech(message, true)
      if (switched) {
        return
      }
      if (options.errorText.value) {
        stopSpeechSafely(false)
        return
      }
    }

    options.errorText.value = message
    stopSpeechSafely(false)
  }

  // 启动前先清空上一轮转写，并把当前输入作为前缀固定下来。
  async function startSpeech() {
    if (!options.sessionStarted.value || options.isLoading.value || speechSession) return

    options.errorText.value = ''
    speechInputPrefix = options.inputText.value.trim()
    speechTranscript = ''
    speechUiState.value = 'idle'
    mergeSpeechToInput()

    if (!aiConfigStore.shouldRequestBackendSpeech) {
      try {
        await activateSpeechEngine('browser')
        return
      } catch (error) {
        const message = formatErrorMessage(error)
        options.errorText.value = message
        return
      }
    }

    try {
      await activateSpeechEngine('realtime')
    } catch (error) {
      const realtimeMessage = formatErrorMessage(error)
      const switched = await trySwitchToBrowserSpeech(realtimeMessage, true)
      if (switched) {
        return
      }

      isListening.value = false
      speechUiState.value = 'idle'
      speechTranscript = ''
      options.inputText.value = speechInputPrefix
      speechInputPrefix = ''
      activeSpeechEngine.value = null
      if (!options.errorText.value) {
        options.errorText.value = `${INTERVIEW_PANEL_TEXT.speechUnavailable}\n${realtimeMessage}`
      }
    }
  }

  return {
    isListening,
    speechUiState,
    activeSpeechEngine,
    resolveEngineLabel,
    resetBackendSpeechFailureState,
    startSpeech,
    stopSpeech,
    stopSpeechSafely,
  }
}
