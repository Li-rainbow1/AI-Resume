import type { InterviewCommand, InterviewMode, InterviewRequestState, InterviewSessionSummary } from '@/services/interview/types'

// 面试面板的展示文案集中定义，组件只做引用，避免零散字符串散落在交互逻辑中。
export const INTERVIEW_PANEL_TEXT = {
  statusNotStarted: '未开始',
  statusFinished: '已结束',
  statusRunning: '进行中',
  statusPaused: '已暂停',
  unknownError: '未知错误',
  modeCandidate: '候选人模式（AI 面试官）',
  modeInterviewer: '面试官模式（AI 候选人）',
  hideResume: '收起简历',
  showResume: '查看简历',
  totalScore: '总分',
  pass: '通过',
  fail: '未通过',
  projectInterview: '项目面试',
  switchedToBrowserSpeech: '后端实时语音不可用，已切换为浏览器免费语音识别',
  speechUnavailable: '后端实时语音与浏览器免费语音均不可用',
  speechAutoDisabledNotice: '后端实时语音已因连续失败自动停用，可在语音配置中重新启用。',
  historyPlaceholder: '历史会话',
  historyRefresh: '刷新历史',
  historyLoading: '加载中...',
  sessionAlreadyFinished: '当前会话已结束，不可继续或发送消息。',
  composerDefaultHint: 'Enter 发送，Ctrl+Enter 换行，Ctrl+I 语音开关',
  composerListeningHint: '语音输入中，点击麦克风结束',
  composerConnectingHint: '语音连接中，请稍候',
  composerTranscribingHint: '语音转写中，请稍候',
  composerFailedHint: '本轮发送未完成，请根据提示调整后重试',
  composerFinishedHint: '当前会话已结束，如需继续请先重置',
  speechRealtimeLabel: '实时语音',
  speechBrowserLabel: '浏览器识别',
  speechPreferredLabel: '优先使用系统实时语音服务',
} as const

// 消息标识只需在会话内唯一，使用时间戳加随机串即可满足本地渲染的键值需求。
export function newMessageId(): string {
  return `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
}

// 请求标识优先使用浏览器原生 UUID，缺失时退化为本地生成，保证断线重试仍能复用同一标识。
export function newRequestId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `interview-${newMessageId()}`
}

// 两种模式下 AI 的角色不同，气泡与状态文案的称呼也随之切换。
export function resolveAssistantLabel(currentMode: InterviewMode): string {
  return currentMode === 'candidate' ? 'AI面试官' : 'AI候选人'
}

// 后端异常既可能是 Error，也可能是字符串或空值，统一转成可直接展示的文案。
export function formatErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.message
  return String(error ?? INTERVIEW_PANEL_TEXT.unknownError)
}

// 不同命令与阶段关注点不同，这里按 start、finish、continue 三条分支给出可读进度文案。
export function buildRequestStatusText(
  command: InterviewCommand,
  state: InterviewRequestState,
  mode: InterviewMode,
  assistantLabel: string
): string {
  if (command === 'start') {
    if (state === 'submitting') return '正在启动面试...'
    if (state === 'accepted') return '面试已启动，正在同步当前简历上下文'
    if (state === 'processing') {
      return mode === 'candidate' ? 'AI面试官正在生成第一轮问题...' : 'AI候选人正在生成开场回答...'
    }
    if (state === 'responding') return `${assistantLabel}正在回复...`
    if (state === 'failed') return '面试启动失败，请重试'
    return ''
  }

  if (command === 'finish') {
    if (state === 'submitting') return '正在结束面试...'
    if (state === 'accepted') return '已收到结束指令'
    if (state === 'processing') return '正在生成评分结果与总结...'
    if (state === 'responding') return `${assistantLabel}正在输出评分结果...`
    if (state === 'failed') return '结束并评分失败，请重试'
    return ''
  }

  if (state === 'submitting') return '消息已发送'
  if (state === 'accepted') return '已收到你的回答'
  if (state === 'processing') {
    return mode === 'candidate' ? 'AI面试官正在组织下一轮提问...' : 'AI候选人正在组织回答...'
  }
  if (state === 'responding') return `${assistantLabel}正在回复...`
  if (state === 'failed') return '本轮回复失败，请调整后重试'
  return ''
}

// 后端返回的状态说明优先展示，缺失时回退到本地推导的进度文案。
export function deriveRequestStatusText(
  command: InterviewCommand,
  state: InterviewRequestState,
  mode: InterviewMode,
  message: string | undefined
): string {
  const cleaned = String(message || '').trim()
  return cleaned || buildRequestStatusText(command, state, mode, resolveAssistantLabel(mode))
}

// 历史会话下拉项同时展示时间、模式、状态与分数，方便快速辨认要恢复的会话。
export function buildSessionOptionLabel(item: InterviewSessionSummary): string {
  const modeLabel = item.mode === 'candidate' ? '候选人' : '面试官'
  const statusLabel = item.status === 'finished' ? '结束' : '进行中'
  const scoreLabel = item.totalScore == null ? '' : ` · ${item.totalScore}分`
  const normalizedTime = item.updatedAt.replace('T', ' ')
  const timeLabel = `${normalizedTime.slice(5, 10)} ${normalizedTime.slice(11, 16)}`
  return `${timeLabel} · ${modeLabel} · ${statusLabel}${scoreLabel}`
}
