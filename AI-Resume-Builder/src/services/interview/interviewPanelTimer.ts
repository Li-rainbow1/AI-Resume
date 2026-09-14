import { computed, onMounted, onUnmounted, ref, type Ref } from 'vue'
import { INTERVIEW_PANEL_TEXT } from './interviewPanelText'

// 面试计时器同时承担「时长配置」与「倒计时展示」两件事，独立出来后组件只需消费状态。
// sessionFinished 由外部传入，因为结束状态还会被回合请求链路改写。
export function createInterviewTimer(options: { sessionFinished: Ref<boolean> }) {
  const durationMinutes = ref(60)
  const elapsedSeconds = ref(0)
  const sessionStarted = ref(false)
  const timerRunning = ref(false)

  let ticker: ReturnType<typeof setInterval> | null = null

  const totalSeconds = computed(() => Math.max(durationMinutes.value, 1) * 60)
  const remainingSeconds = computed(() => Math.max(totalSeconds.value - elapsedSeconds.value, 0))
  const timerText = computed(() => {
    const minutes = Math.floor(remainingSeconds.value / 60)
    const seconds = remainingSeconds.value % 60
    return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
  })
  const timerStatusText = computed(() => {
    if (!sessionStarted.value) return INTERVIEW_PANEL_TEXT.statusNotStarted
    if (options.sessionFinished.value) return INTERVIEW_PANEL_TEXT.statusFinished
    if (remainingSeconds.value === 0) return INTERVIEW_PANEL_TEXT.statusFinished
    return timerRunning.value ? INTERVIEW_PANEL_TEXT.statusRunning : INTERVIEW_PANEL_TEXT.statusPaused
  })
  const interviewStatusText = computed(() => {
    if (!sessionStarted.value) return INTERVIEW_PANEL_TEXT.statusNotStarted
    if (options.sessionFinished.value || remainingSeconds.value === 0) return INTERVIEW_PANEL_TEXT.statusFinished
    return INTERVIEW_PANEL_TEXT.statusRunning
  })
  const pauseButtonLabel = computed(() => (timerRunning.value ? '暂停' : '继续'))

  // 时长可调范围与后端入参保持一致，避免出现会话时长与真实倒计时不一致。
  function adjustDuration(delta: number) {
    const next = Math.max(15, Math.min(120, durationMinutes.value + delta))
    if (next === durationMinutes.value) return
    durationMinutes.value = next
    if (!sessionStarted.value) {
      elapsedSeconds.value = 0
    } else {
      elapsedSeconds.value = Math.max(0, Math.min(elapsedSeconds.value, totalSeconds.value - 1))
    }
  }

  // 秒级 ticker 只在「已开始且正在计时」时才累加，暂停与结束后自然停住。
  function startTicker() {
    if (ticker) return
    ticker = setInterval(() => {
      if (!sessionStarted.value || !timerRunning.value) return
      if (remainingSeconds.value <= 0) return
      elapsedSeconds.value += 1
    }, 1000)
  }

  function stopTicker() {
    if (!ticker) return
    clearInterval(ticker)
    ticker = null
  }

  // 重置只清计时相关状态，其他面板状态由组件自行复位。
  function resetTimer() {
    elapsedSeconds.value = 0
    sessionStarted.value = false
    timerRunning.value = false
  }

  onMounted(startTicker)
  onUnmounted(stopTicker)

  return {
    durationMinutes,
    elapsedSeconds,
    sessionStarted,
    timerRunning,
    totalSeconds,
    remainingSeconds,
    timerText,
    timerStatusText,
    interviewStatusText,
    pauseButtonLabel,
    adjustDuration,
    resetTimer,
  }
}
