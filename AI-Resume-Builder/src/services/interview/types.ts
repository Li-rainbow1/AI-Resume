import type { BasicInfo, EducationEntry, ProjectEntry, WorkEntry } from '@/stores/resume'

export type InterviewMode = 'candidate' | 'interviewer'
export type InterviewCommand = 'start' | 'continue' | 'finish'
export type InterviewPhase = 'opening' | 'skills' | 'work' | 'projects' | 'scenario' | 'written' | 'summary'

export interface ResumeSnapshot {
  basicInfo: BasicInfo
  skillsText: string
  workList: WorkEntry[]
  projectList: ProjectEntry[]
  educationList: EducationEntry[]
  selfIntro?: string
}

export interface InterviewTurnScore {
  score: number
  comment: string
}

export interface InterviewHistoryItem {
  role: 'user' | 'assistant'
  content: string
  score?: InterviewTurnScore | null
}

export interface FinalEvaluation {
  projectScore: number
  skillScore: number
  workScore: number
  educationScore: number
  totalScore: number
  passed: boolean
  summary: string
  improvements: string[]
}

export interface InterviewTurnResponse {
  assistantReply: string
  phase: InterviewPhase
  nextAction: 'continue' | 'finish'
  turnScore: InterviewTurnScore | null
  finalEvaluation: FinalEvaluation | null
  memorySummary: string
  sessionId: string
}

export interface InterviewTurnRequest {
  mode: InterviewMode
  command: InterviewCommand
  sessionId?: string
  requestId?: string
  userInput?: string
  history: InterviewHistoryItem[]
  resumeSnapshot: ResumeSnapshot
  durationMinutes: number
  elapsedSeconds: number
  memorySummary?: string
}

export interface InterviewSessionSummary {
  sessionId: string
  mode: InterviewMode
  status: 'active' | 'finished'
  durationMinutes: number
  elapsedSeconds: number
  messageCount: number
  totalScore: number | null
  passed: boolean | null
  lastMessagePreview: string
  createdAt: string
  updatedAt: string
}

export interface InterviewSessionMessage {
  role: 'user' | 'assistant'
  content: string
  score: InterviewTurnScore | null
}

export interface InterviewSessionDetail {
  sessionId: string
  mode: InterviewMode
  status: 'active' | 'finished'
  durationMinutes: number
  elapsedSeconds: number
  memorySummary: string
  finalEvaluation: FinalEvaluation | null
  resumeSnapshot: ResumeSnapshot | null
  messages: InterviewSessionMessage[]
  createdAt: string
  updatedAt: string
}

export type InterviewRequestState = 'idle' | 'submitting' | 'accepted' | 'processing' | 'responding' | 'completed' | 'failed'

export interface InterviewTurnStreamCallbacks {
  onAccepted?: (message: string) => void
  onProcessing?: (message: string) => void
  onAssistantReplyChunk?: (text: string) => void
}

export type InterviewStreamEvent = {
  event: string
  data: string
}
