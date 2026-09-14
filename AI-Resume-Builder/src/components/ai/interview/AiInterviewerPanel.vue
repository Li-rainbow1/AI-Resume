<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  ChevronDown,
  FileText,
  History,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  Settings,
  Square,
  Timer,
} from 'lucide-vue-next'
import AiConfigDialog from '@/components/ai/AiConfigDialog.vue'
import InterviewSimulationPanel from '@/components/ai/interview/InterviewSimulationPanel.vue'
import ResumePreviewOverlay from '@/components/ai/interview/ResumePreviewOverlay.vue'
import type { ChatMessage } from '@/components/ai/interview/types'
import { createInterviewTimer } from '@/services/interview/interviewPanelTimer'
import { createInterviewSpeechInput } from '@/services/interview/interviewSpeechInput'
import {
  INTERVIEW_PANEL_TEXT as TEXT,
  buildRequestStatusText,
  buildSessionOptionLabel,
  deriveRequestStatusText,
  formatErrorMessage,
  newMessageId,
  newRequestId,
  resolveAssistantLabel,
} from '@/services/interview/interviewPanelText'
import { useResumeStore } from '@/stores/resume'
import {
  getInterviewSessionDetail,
  listInterviewSessions,
  requestInterviewTurn,
  type FinalEvaluation,
  type InterviewCommand,
  type InterviewHistoryItem,
  type InterviewMode,
  type InterviewRequestState,
  type InterviewSessionSummary,
  type InterviewTurnScore,
  type ResumeSnapshot,
} from '@/services/interviewService'

const resumeStore = useResumeStore()

const PENDING_FRESH_SESSION_STORAGE_KEY = 'resume-builder.ai-interview.pending-fresh-session-mode'

const mode = ref<InterviewMode>('candidate')
const isLoading = ref(false)
const showResumePreview = ref(false)
const showAiConfig = ref(false)
const historyFieldRef = ref<HTMLElement | null>(null)
const errorMsg = ref('')
const inputText = ref('')
const finalEvaluation = ref<FinalEvaluation | null>(null)
const messages = ref<ChatMessage[]>([])
const memorySummary = ref('')
const requestState = ref<InterviewRequestState>('idle')
const requestStatusText = ref('')
const streamingAssistantMessageId = ref<string | null>(null)
const currentSessionId = ref('')
const sessionHistory = ref<InterviewSessionSummary[]>([])
const selectedSessionId = ref('')
const loadingSessionHistory = ref(false)
const historyMenuOpen = ref(false)
const sessionFinished = ref(false)

const timer = createInterviewTimer({ sessionFinished })
const {
  durationMinutes,
  elapsedSeconds,
  sessionStarted,
  timerRunning,
  remainingSeconds,
  timerText,
  timerStatusText,
  interviewStatusText,
  pauseButtonLabel,
  adjustDuration,
  resetTimer,
} = timer

const speech = createInterviewSpeechInput({
  inputText,
  errorText: errorMsg,
  messages,
  sessionStarted,
  isLoading,
})
const { isListening, speechUiState, activeSpeechEngine, resolveEngineLabel, resetBackendSpeechFailureState, startSpeech, stopSpeech, stopSpeechSafely } = speech

// 会话状态版本号：异步恢复流程用它判断期间是否发生过重置或切换，避免旧响应覆盖新状态。
let sessionStateRevision = 0
let retryRequestId = ''
let retryCommand: InterviewCommand | null = null
let retryInput = ''
let retrySessionId = ''
let retryDraftMessageId = ''

const canSend = computed(() => sessionStarted.value && !sessionFinished.value && inputText.value.trim() !== '' && !isLoading.value)
const canStart = computed(() => !sessionStarted.value && !isLoading.value)
const canTogglePause = computed(() => sessionStarted.value && !sessionFinished.value && remainingSeconds.value > 0 && !isLoading.value)
const canFinish = computed(() => sessionStarted.value && !isLoading.value && !sessionFinished.value && messages.value.length > 0)
const canToggleVoice = computed(
  () => sessionStarted.value && !sessionFinished.value && !isLoading.value && speechUiState.value !== 'transcribing'
)
const selectedSessionLabel = computed(() => {
  const selected = sessionHistory.value.find((item) => item.sessionId === selectedSessionId.value)
  return selected ? buildSessionOptionLabel(selected) : TEXT.historyPlaceholder
})
const historyRefreshText = computed(() => (loadingSessionHistory.value ? TEXT.historyLoading : TEXT.historyRefresh))
const composerHintText = computed(() => {
  if (speechUiState.value === 'connecting') return TEXT.composerConnectingHint
  if (speechUiState.value === 'transcribing') return `${resolveEngineLabel(activeSpeechEngine.value)}处理中，请稍候`
  if (isListening.value) {
    return TEXT.composerListeningHint
  }
  if (['submitting', 'accepted', 'processing', 'responding'].includes(requestState.value)) {
    return requestStatusText.value || TEXT.composerDefaultHint
  }
  if (requestState.value === 'failed') return TEXT.composerFailedHint
  if (sessionFinished.value) return TEXT.composerFinishedHint
  return TEXT.composerDefaultHint
})

const resumeSnapshot = computed<ResumeSnapshot>(() => ({
  basicInfo: resumeStore.basicInfo,
  skillsText: resumeStore.skills,
  workList: resumeStore.workList,
  projectList: resumeStore.projectList,
  educationList: resumeStore.educationList,
  selfIntro: resumeStore.selfIntro,
}))

// 新建会话的意图需要跨刷新保留，因此借助 sessionStorage 记录待生效的模式。
function readPendingFreshSessionMode(): InterviewMode | null {
  try {
    const storedMode = window.sessionStorage.getItem(PENDING_FRESH_SESSION_STORAGE_KEY)
    return storedMode === 'candidate' || storedMode === 'interviewer' ? storedMode : null
  } catch {
    return null
  }
}

function markPendingFreshSession(modeValue: InterviewMode) {
  try {
    window.sessionStorage.setItem(PENDING_FRESH_SESSION_STORAGE_KEY, modeValue)
  } catch {
    // 浏览器禁用会话存储时，当前页面仍可正常使用新会话状态。
  }
}

function clearPendingFreshSession() {
  try {
    window.sessionStorage.removeItem(PENDING_FRESH_SESSION_STORAGE_KEY)
  } catch {
    // 浏览器禁用会话存储时无需阻断当前会话操作。
  }
}

function invalidatePendingSessionRestore() {
  sessionStateRevision += 1
}

// 请求状态既驱动气泡占位文案，也驱动输入区提示，统一在这里落状态与文案。
function setRequestState(nextState: InterviewRequestState, command: InterviewCommand, message?: string) {
  requestState.value = nextState

  if (nextState === 'idle' || nextState === 'completed') {
    requestStatusText.value = ''
    return
  }

  requestStatusText.value = deriveRequestStatusText(command, nextState, mode.value, message)
}

function appendMessage(role: 'assistant' | 'user', content: string, score: InterviewTurnScore | null = null) {
  messages.value.push({
    id: newMessageId(),
    role,
    content: content.trim(),
    score,
  })
}

// 流式回复先落一条占位消息，后续 chunk 与最终结果都复用这条消息的 id。
function createAssistantDraftMessage(content: string): string {
  const id = newMessageId()
  messages.value.push({
    id,
    role: 'assistant',
    content: content.trim(),
    score: null,
  })
  return id
}

function updateAssistantMessageById(id: string, content: string, score: InterviewTurnScore | null = null) {
  const target = messages.value.find((item) => item.id === id)
  if (!target) return
  target.content = content
  target.score = score
}

function removeMessageById(id: string) {
  const index = messages.value.findIndex((item) => item.id === id)
  if (index >= 0) messages.value.splice(index, 1)
}

function resetSession() {
  invalidatePendingSessionRestore()
  stopSpeechSafely(true)
  resetBackendSpeechFailureState()
  messages.value = []
  finalEvaluation.value = null
  memorySummary.value = ''
  errorMsg.value = ''
  requestState.value = 'idle'
  requestStatusText.value = ''
  resetTimer()
  streamingAssistantMessageId.value = null
  inputText.value = ''
  currentSessionId.value = ''
  selectedSessionId.value = ''
  sessionFinished.value = false
  retryRequestId = ''
  retryCommand = null
  retryInput = ''
  retrySessionId = ''
  retryDraftMessageId = ''
}

// 历史只提交已完成的轮次，未收到完整回复的占位消息与待发送草稿都不进入上下文。
function buildHistory(excludeLastMessageId?: string): InterviewHistoryItem[] {
  const source = excludeLastMessageId
    ? messages.value.filter((item) => item.id !== excludeLastMessageId)
    : messages.value
  return source.filter((item) => !item.incomplete).map((item) => ({
    role: item.role,
    content: item.content,
    score: item.score,
  }))
}

function removeLatestUserMessage(content: string) {
  const targetContent = content.trim()
  if (!targetContent) return
  for (let index = messages.value.length - 1; index >= 0; index -= 1) {
    const item = messages.value[index]
    if (item && item.role === 'user' && item.content.trim() === targetContent) {
      messages.value.splice(index, 1)
      return
    }
  }
}

// 会话详情是恢复入口，会把模式、时长、消息和评分整体覆盖为历史快照。
function applySessionDetail(detail: Awaited<ReturnType<typeof getInterviewSessionDetail>>) {
  retryRequestId = ''
  retryCommand = null
  retryInput = ''
  retrySessionId = ''
  retryDraftMessageId = ''
  mode.value = detail.mode
  durationMinutes.value = Math.max(15, Math.min(120, detail.durationMinutes || 60))
  elapsedSeconds.value = Math.max(0, Math.min(detail.elapsedSeconds || 0, durationMinutes.value * 60))
  messages.value = detail.messages.map((item) => ({
    id: newMessageId(),
    role: item.role,
    content: item.content,
    score: item.score,
  }))
  memorySummary.value = detail.memorySummary || ''
  finalEvaluation.value = detail.finalEvaluation
  requestState.value = 'idle'
  requestStatusText.value = ''
  currentSessionId.value = detail.sessionId
  selectedSessionId.value = detail.sessionId
  sessionStarted.value = detail.messages.length > 0
  timerRunning.value = false
  streamingAssistantMessageId.value = null
  inputText.value = ''
  errorMsg.value = ''
  sessionFinished.value = detail.status === 'finished' || Boolean(detail.finalEvaluation)
}

async function refreshSessionHistory(preferredSessionId?: string, selectLatestWhenEmpty = true) {
  loadingSessionHistory.value = true
  const requestRevision = sessionStateRevision
  try {
    const sessions = await listInterviewSessions(30)
    if (requestRevision !== sessionStateRevision) return
    sessionHistory.value = sessions

    const preferred = preferredSessionId || currentSessionId.value || selectedSessionId.value
    const targetSessionId = preferred || (selectLatestWhenEmpty ? sessions[0]?.sessionId || '' : '')

    selectedSessionId.value = sessions.some((item) => item.sessionId === targetSessionId)
      ? targetSessionId
      : selectLatestWhenEmpty
        ? sessions[0]?.sessionId || ''
        : ''
  } catch (error) {
    errorMsg.value = formatErrorMessage(error)
  } finally {
    loadingSessionHistory.value = false
  }
}

async function restoreSessionById(
  sessionId: string,
  refreshHistory = false,
  expectedRevision = sessionStateRevision
) {
  const targetSessionId = sessionId.trim()
  if (!targetSessionId) return

  if (isListening.value) {
    await stopSpeech(false)
  }

  const detail = await getInterviewSessionDetail(targetSessionId)
  if (expectedRevision !== undefined && expectedRevision !== sessionStateRevision) return
  applySessionDetail(detail)
  if (refreshHistory) {
    await refreshSessionHistory(targetSessionId)
  }
}

async function initializeSessionHistory() {
  const initializationRevision = sessionStateRevision
  const pendingMode = readPendingFreshSessionMode()
  if (pendingMode) {
    mode.value = pendingMode
    await refreshSessionHistory(undefined, false)
    if (initializationRevision === sessionStateRevision) {
      selectedSessionId.value = ''
    }
    return
  }

  await refreshSessionHistory()
  if (initializationRevision !== sessionStateRevision) return
  const firstSessionId = selectedSessionId.value
  if (!firstSessionId) return

  try {
    await restoreSessionById(firstSessionId, false, initializationRevision)
  } catch (error) {
    errorMsg.value = formatErrorMessage(error)
  }
}

async function handleSessionSelectionChange() {
  const targetSessionId = selectedSessionId.value.trim()
  if (!targetSessionId || targetSessionId === currentSessionId.value) return

  try {
    await restoreSessionById(targetSessionId)
  } catch (error) {
    errorMsg.value = formatErrorMessage(error)
  }
}

function handleHistoryMenuToggle() {
  if (loadingSessionHistory.value || sessionHistory.value.length === 0) return
  historyMenuOpen.value = !historyMenuOpen.value
}

function handleHistoryOptionSelect(sessionId: string) {
  if (isLoading.value) return
  invalidatePendingSessionRestore()
  clearPendingFreshSession()
  selectedSessionId.value = sessionId
  historyMenuOpen.value = false
  void handleSessionSelectionChange()
}

function handleRefreshSessionHistory() {
  historyMenuOpen.value = false
  const preserveFreshSession = readPendingFreshSessionMode() !== null
  void refreshSessionHistory(
    preserveFreshSession ? undefined : selectedSessionId.value || currentSessionId.value,
    !preserveFreshSession
  )
}

// 单回合请求：失败时保留原始输入与 requestId，让用户重试时命中同一请求而不是产生重复回合。
async function runInterview(command: InterviewCommand, userInput?: string) {
  if (isLoading.value) return
  if (command === 'continue' && sessionFinished.value) {
    errorMsg.value = TEXT.sessionAlreadyFinished
    return
  }

  isLoading.value = true
  errorMsg.value = ''
  setRequestState('submitting', command)
  const normalizedUserInput = userInput?.trim() || ''
  const canReuseRetry = retryRequestId && retrySessionId === currentSessionId.value && retryCommand === command && retryInput === normalizedUserInput
  const requestId = canReuseRetry ? retryRequestId : newRequestId()
  // 原失败回复保留到同请求重试成功，再用完整回复替换，连续失败也只留一份。
  const previousDraftId = canReuseRetry ? retryDraftMessageId : ''
  const draftMessageId = createAssistantDraftMessage(
    requestStatusText.value || buildRequestStatusText(command, 'submitting', mode.value, resolveAssistantLabel(mode.value))
  )
  streamingAssistantMessageId.value = draftMessageId
  let hasStreamedAssistantReply = false

  try {
    const response = await requestInterviewTurn(
      {
        mode: mode.value,
        command,
        sessionId: currentSessionId.value || undefined,
        requestId,
        userInput,
        history: buildHistory(draftMessageId),
        resumeSnapshot: resumeSnapshot.value,
        durationMinutes: durationMinutes.value,
        elapsedSeconds: elapsedSeconds.value,
        memorySummary: memorySummary.value,
      },
      undefined,
      {
        onAccepted(message) {
          setRequestState('accepted', command, message)
          updateAssistantMessageById(draftMessageId, requestStatusText.value, null)
        },
        onProcessing(message) {
          setRequestState('processing', command, message)
          updateAssistantMessageById(draftMessageId, requestStatusText.value, null)
        },
        onAssistantReplyChunk(text) {
          hasStreamedAssistantReply = true
          setRequestState('responding', command)
          updateAssistantMessageById(draftMessageId, text)
        },
      }
    )

    updateAssistantMessageById(draftMessageId, response.assistantReply, response.turnScore)
    if (previousDraftId) removeMessageById(previousDraftId)
    setRequestState('completed', command)
    if (response.sessionId) {
      clearPendingFreshSession()
      currentSessionId.value = response.sessionId
      selectedSessionId.value = response.sessionId
    }
    if (response.memorySummary) memorySummary.value = response.memorySummary
    if (response.finalEvaluation) finalEvaluation.value = response.finalEvaluation
    if (response.nextAction === 'finish' || command === 'finish') {
      timerRunning.value = false
      sessionFinished.value = true
    }
    retryRequestId = ''
    retryCommand = null
    retryInput = ''
    retrySessionId = ''
    retryDraftMessageId = ''
    void refreshSessionHistory(currentSessionId.value)
  } catch (error: unknown) {
    if (hasStreamedAssistantReply) {
      const draft = messages.value.find((item) => item.id === draftMessageId)
      if (draft) draft.incomplete = true
      if (previousDraftId) removeMessageById(previousDraftId)
    } else {
      removeMessageById(draftMessageId)
    }
    removeLatestUserMessage(normalizedUserInput)
    if (normalizedUserInput) inputText.value = normalizedUserInput
    retryRequestId = requestId
    retryCommand = command
    retryInput = normalizedUserInput
    retrySessionId = currentSessionId.value
    retryDraftMessageId = hasStreamedAssistantReply ? draftMessageId : previousDraftId
    if (command === 'start') {
      sessionStarted.value = false
      timerRunning.value = false
    }
    setRequestState('failed', command)
    errorMsg.value = formatErrorMessage(error)
  } finally {
    if (streamingAssistantMessageId.value === draftMessageId) {
      streamingAssistantMessageId.value = null
    }
    isLoading.value = false
  }
}

function handleModeSwitch(nextMode: InterviewMode) {
  if (mode.value === nextMode || isLoading.value) return
  mode.value = nextMode
  resetSession()
  markPendingFreshSession(nextMode)
}

function handleStart() {
  if (!canStart.value) return
  invalidatePendingSessionRestore()
  clearPendingFreshSession()
  if (!currentSessionId.value) currentSessionId.value = newRequestId()
  selectedSessionId.value = ''
  sessionStarted.value = true
  timerRunning.value = true
  sessionFinished.value = false
  void runInterview('start')
}

function handleTogglePause() {
  if (!sessionStarted.value || sessionFinished.value || remainingSeconds.value === 0 || isLoading.value) return
  timerRunning.value = !timerRunning.value
}

function handleFinish() {
  if (!canFinish.value) return
  timerRunning.value = false
  void runInterview('finish')
}

function handleReset() {
  if (isLoading.value) return
  resetSession()
  markPendingFreshSession(mode.value)
}

async function handleSend() {
  const text = inputText.value.trim()
  if (sessionFinished.value) {
    errorMsg.value = TEXT.sessionAlreadyFinished
    return
  }
  if (!canSend.value || !text) return

  if (isListening.value) {
    await stopSpeech(false)
  }

  const finalText = inputText.value.trim()
  if (!finalText) return

  appendMessage('user', finalText)
  inputText.value = ''
  void runInterview('continue', finalText)
}

async function handleToggleVoice() {
  if (!canToggleVoice.value) return

  if (isListening.value) {
    await stopSpeech(false)
    return
  }

  await startSpeech()
}

function handleGlobalKeydown(event: KeyboardEvent) {
  if (!event.ctrlKey || event.altKey || event.shiftKey || event.metaKey) return
  if (event.key.toLowerCase() !== 'i') return
  event.preventDefault()
  void handleToggleVoice()
}

function handleOpenAiConfig() {
  showAiConfig.value = true
}

function handleResumeToggle() {
  showResumePreview.value = !showResumePreview.value
}

function handleDocumentPointerDown(event: MouseEvent) {
  const target = event.target as Node | null
  if (!target) return
  if (historyFieldRef.value && !historyFieldRef.value.contains(target)) {
    historyMenuOpen.value = false
  }
}

// 倒计时归零即自动收卷，避免用户忘记点「结束并评分」导致会话一直挂着。
watch(remainingSeconds, (value) => {
  if (!sessionStarted.value) return
  if (value !== 0) return
  timerRunning.value = false
  if (!finalEvaluation.value && !isLoading.value) {
    void runInterview('finish')
  }
})

onMounted(() => {
  void initializeSessionHistory()
  window.addEventListener('keydown', handleGlobalKeydown)
  document.addEventListener('mousedown', handleDocumentPointerDown)
})

onUnmounted(() => {
  window.removeEventListener('keydown', handleGlobalKeydown)
  document.removeEventListener('mousedown', handleDocumentPointerDown)
  stopSpeechSafely(true)
})
</script>

<template>
  <section class="ai-interviewer-panel">
    <header class="interview-hero">
      <div class="interview-identity">
        <span class="interview-identity-mark" aria-hidden="true">AI</span>
        <div class="interview-identity-copy">
          <strong>简历 AI 面试</strong>
          <span v-if="finalEvaluation">
            {{ TEXT.projectInterview }} · {{ finalEvaluation.totalScore }}分 ·
            {{ finalEvaluation.passed ? TEXT.pass : TEXT.fail }}
          </span>
          <span v-else>
            {{ mode === 'candidate' ? '我是候选人' : '我是面试官' }} · {{ interviewStatusText }}
          </span>
        </div>
      </div>

      <div class="interview-command-bar" aria-label="面试操作">
        <div class="topbar-mode-switch" aria-label="面试模式">
          <button
            type="button"
            :disabled="isLoading"
            :class="{ active: mode === 'candidate' }"
            :aria-pressed="mode === 'candidate'"
            @click="handleModeSwitch('candidate')"
          >
            我是候选人
          </button>
          <button
            type="button"
            :disabled="isLoading"
            :class="{ active: mode === 'interviewer' }"
            :aria-pressed="mode === 'interviewer'"
            @click="handleModeSwitch('interviewer')"
          >
            我是面试官
          </button>
        </div>

        <button
          type="button"
          class="topbar-action topbar-resume-action"
          :class="{ active: showResumePreview }"
          @click="handleResumeToggle"
        >
          <FileText :size="15" :stroke-width="1.9" aria-hidden="true" />
          {{ showResumePreview ? TEXT.hideResume : TEXT.showResume }}
        </button>

        <button
          type="button"
          class="topbar-action topbar-config-action"
          @click="handleOpenAiConfig"
        >
          <Settings :size="15" :stroke-width="1.9" aria-hidden="true" />
          语音配置
        </button>

        <div class="topbar-duration" aria-label="面试时长">
          <button type="button" aria-label="减少五分钟" @click="adjustDuration(-5)">-5</button>
          <span>
            <Timer :size="15" :stroke-width="1.9" aria-hidden="true" />
            {{ durationMinutes }} 分钟
          </span>
          <button type="button" aria-label="增加五分钟" @click="adjustDuration(5)">+5</button>
        </div>

        <button
          v-if="sessionStarted"
          type="button"
          class="topbar-action topbar-pause-action"
          :disabled="!canTogglePause"
          @click="handleTogglePause"
        >
          <Pause v-if="timerRunning" :size="15" :stroke-width="1.9" aria-hidden="true" />
          <Play v-else :size="15" :stroke-width="1.9" aria-hidden="true" />
          {{ pauseButtonLabel }}
        </button>

        <button
          type="button"
          class="topbar-action topbar-reset-action"
          :disabled="isLoading"
          @click="handleReset"
        >
          <RotateCcw :size="15" :stroke-width="1.9" aria-hidden="true" />
          重置
        </button>

        <button
          v-if="sessionStarted"
          type="button"
          class="topbar-action topbar-finish-action danger"
          :disabled="!canFinish"
          @click="handleFinish"
        >
          <Square :size="14" :stroke-width="1.9" aria-hidden="true" />
          结束并评分
        </button>
      </div>

      <div class="interview-hero-tools">
        <div ref="historyFieldRef" class="history-field" aria-label="历史会话">
          <History :size="16" :stroke-width="1.8" aria-hidden="true" />
          <button
            class="history-select"
            type="button"
            :disabled="loadingSessionHistory"
            :aria-expanded="historyMenuOpen"
            aria-haspopup="listbox"
            @click="handleHistoryMenuToggle"
          >
            <span class="history-select-text">{{ selectedSessionLabel }}</span>
            <ChevronDown class="history-select-arrow" :size="17" :stroke-width="2.1" aria-hidden="true" />
          </button>
          <div v-if="historyMenuOpen" class="history-options" role="listbox">
            <button
              v-for="item in sessionHistory"
              :key="item.sessionId"
              class="history-option"
              :class="{ active: item.sessionId === selectedSessionId }"
              type="button"
              role="option"
              :aria-selected="item.sessionId === selectedSessionId"
              @click="handleHistoryOptionSelect(item.sessionId)"
            >
              {{ buildSessionOptionLabel(item) }}
            </button>
          </div>
        </div>
        <button
          class="history-refresh-btn"
          :class="{ loading: loadingSessionHistory }"
          type="button"
          :disabled="loadingSessionHistory"
          :title="historyRefreshText"
          aria-label="刷新历史"
          :aria-busy="loadingSessionHistory"
          @click="handleRefreshSessionHistory"
        >
          <RefreshCw :size="16" :stroke-width="1.9" aria-hidden="true" />
        </button>
        <span
          class="interview-status-pill"
          :class="{
            active: sessionStarted && !sessionFinished && remainingSeconds > 0,
            finished: sessionFinished || remainingSeconds === 0,
          }"
        >
          <span class="interview-status-dot" aria-hidden="true" />
          {{ interviewStatusText }}
        </span>
      </div>
    </header>

    <div class="interview-context-status">
      <div
        v-if="sessionStarted"
        class="session-timer-fab"
        :class="{ running: timerRunning, finished: sessionFinished || remainingSeconds === 0 }"
        role="timer"
        aria-live="polite"
      >
        <span class="session-timer-dot" aria-hidden="true" />
        <Timer :size="16" :stroke-width="1.9" aria-hidden="true" />
        <strong>{{ timerText }}</strong>
        <span>{{ timerStatusText }}</span>
      </div>
    </div>

    <div class="interview-layout">
      <div class="workspace">
        <InterviewSimulationPanel
          :mode="mode"
          :messages="messages"
          :is-loading="isLoading"
          :error-msg="errorMsg"
          :input-text="inputText"
          :can-send="canSend"
          :is-listening="isListening"
          :request-state="requestState"
          :request-status-text="requestStatusText"
          :composer-hint-text="composerHintText"
          :streaming-assistant-message-id="streamingAssistantMessageId"
          :session-started="sessionStarted"
          :can-toggle-voice="canToggleVoice"
          :session-finished="sessionFinished"
          :speech-state="speechUiState"
          @update:input-text="inputText = $event"
          @send="handleSend"
          @toggle-voice="handleToggleVoice"
        />

        <ResumePreviewOverlay v-if="showResumePreview" @close="showResumePreview = false" />
      </div>
    </div>

    <button
      v-if="!sessionStarted"
      class="session-start-fab"
      type="button"
      :disabled="isLoading"
      @click="handleStart"
    >
      <Play :size="20" :stroke-width="2" aria-hidden="true" />
      {{ isLoading ? '正在启动...' : '开始面试' }}
    </button>

    <AiConfigDialog v-if="showAiConfig" @close="showAiConfig = false" />
  </section>
</template>

<style scoped src="./AiInterviewerPanel.base.css"></style>
<style scoped src="./AiInterviewerPanel.chatgpt.css"></style>
