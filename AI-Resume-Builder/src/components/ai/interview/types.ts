import type { InterviewTurnScore } from '@/services/interviewService'

export interface ChatMessage {
  id: string
  role: 'assistant' | 'user'
  content: string
  score: InterviewTurnScore | null
  incomplete?: boolean
}
