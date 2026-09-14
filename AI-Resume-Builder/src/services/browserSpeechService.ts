export interface SpeechTranscriptionCallbacks {
  onPartialText?: (text: string) => void
  onFinalText?: (segment: string, mergedText: string) => void
  onStateChange?: (state: SpeechRuntimeState) => void
  onError?: (message: string) => void
}

export type SpeechRuntimeState = 'connecting' | 'connected' | 'transcribing' | 'closed'

export interface BrowserSpeechTranscriptionOptions {
  language?: string
  callbacks?: SpeechTranscriptionCallbacks
}

export interface SpeechSession {
  start(): Promise<void>
  stop(options?: { silent?: boolean }): Promise<void>
}

type SpeechRecognitionResultItem = {
  transcript: string
}

type SpeechRecognitionResult = {
  isFinal: boolean
  0: SpeechRecognitionResultItem
}

type SpeechRecognitionEventLike = Event & {
  resultIndex: number
  results: SpeechRecognitionResult[]
}

type SpeechRecognitionLike = {
  continuous: boolean
  interimResults: boolean
  lang: string
  maxAlternatives: number
  onstart: ((event: Event) => void) | null
  onresult: ((event: SpeechRecognitionEventLike) => void) | null
  onerror: ((event: Event & { error?: string; message?: string }) => void) | null
  onend: ((event: Event) => void) | null
  start(): void
  stop(): void
  abort(): void
}

type SpeechRecognitionConstructor = new () => SpeechRecognitionLike

type BrowserWindow = Window & {
  SpeechRecognition?: SpeechRecognitionConstructor
  webkitSpeechRecognition?: SpeechRecognitionConstructor
}

const TEXT = {
  unsupported: '\u5f53\u524d\u6d4f\u89c8\u5668\u4e0d\u652f\u6301\u514d\u8d39\u8bed\u97f3\u8bc6\u522b\uff08Web Speech API\uff09',
  permissionDenied: '\u9ea6\u514b\u98ce\u6743\u9650\u88ab\u62d2\u7edd\uff0c\u8bf7\u5728\u6d4f\u89c8\u5668\u4e2d\u5141\u8bb8\u9ea6\u514b\u98ce\u8bbf\u95ee',
  noSpeech: '\u672a\u8bc6\u522b\u5230\u8bed\u97f3\uff0c\u8bf7\u9760\u8fd1\u9ea6\u514b\u98ce\u91cd\u8bd5',
  captureFailed: '\u65e0\u6cd5\u8bbf\u95ee\u9ea6\u514b\u98ce\uff0c\u8bf7\u68c0\u67e5\u8bbe\u5907\u6216\u6743\u9650',
  genericError: '\u6d4f\u89c8\u5668\u8bed\u97f3\u8bc6\u522b\u5931\u8d25',
} as const

function getSpeechRecognitionCtor(): SpeechRecognitionConstructor | null {
  const browserWindow = window as BrowserWindow
  return browserWindow.SpeechRecognition || browserWindow.webkitSpeechRecognition || null
}

function mapRecognitionError(errorCode: string): string {
  switch ((errorCode || '').toLowerCase()) {
    case 'not-allowed':
    case 'service-not-allowed':
      return TEXT.permissionDenied
    case 'no-speech':
      return TEXT.noSpeech
    case 'audio-capture':
      return TEXT.captureFailed
    default:
      return TEXT.genericError
  }
}

export class BrowserSpeechTranscriptionSession implements SpeechSession {
  private readonly language: string
  private readonly callbacks: SpeechTranscriptionCallbacks

  private recognition: SpeechRecognitionLike | null = null
  private stopped = false
  private finalSegments: string[] = []
  private interimSegment = ''

  constructor(options: BrowserSpeechTranscriptionOptions = {}) {
    this.language = (options.language || 'zh-CN').trim() || 'zh-CN'
    this.callbacks = options.callbacks || {}
  }

  async start(): Promise<void> {
    const Ctor = getSpeechRecognitionCtor()
    if (!Ctor) {
      throw new Error(TEXT.unsupported)
    }

    this.stopped = false
    this.callbacks.onStateChange?.('connecting')

    const recognition = new Ctor()
    this.recognition = recognition
    recognition.continuous = true
    recognition.interimResults = true
    recognition.lang = this.language
    recognition.maxAlternatives = 1

    recognition.onstart = () => {
      if (this.stopped) return
      this.callbacks.onStateChange?.('connected')
    }

    recognition.onresult = (event) => {
      if (this.stopped) return

      const nextFinalSegments: string[] = []
      const interimParts: string[] = []
      const finals: string[] = []

      for (let i = 0; i < event.results.length; i += 1) {
        const result = event.results[i]
        if (!result) continue

        const transcript = (result[0]?.transcript || '').trim()
        if (!transcript) continue

        if (result.isFinal) {
          nextFinalSegments.push(transcript)
          finals.push(transcript)
        } else {
          interimParts.push(transcript)
        }
      }

      this.finalSegments = nextFinalSegments
      this.interimSegment = interimParts.join('').trim()
      const merged = this.buildMergedText()
      this.callbacks.onPartialText?.(merged)

      if (finals.length > 0) {
        for (const segment of finals) {
          this.callbacks.onFinalText?.(segment, merged)
        }
      }
    }

    recognition.onerror = (event) => {
      if (this.stopped) return
      const message = mapRecognitionError(event.error || event.message || '')
      this.callbacks.onError?.(message)
    }

    recognition.onend = () => {
      if (this.stopped) return
      try {
        recognition.start()
      } catch {
        this.callbacks.onError?.(TEXT.genericError)
      }
    }

    recognition.start()
  }

  async stop(options: { silent?: boolean } = {}): Promise<void> {
    if (this.stopped) return
    this.stopped = true

    const recognition = this.recognition
    this.recognition = null

    if (recognition) {
      recognition.onstart = null
      recognition.onresult = null
      recognition.onerror = null
      recognition.onend = null

      try {
        recognition.stop()
      } catch {
        // Ignore stop errors.
      }

      try {
        recognition.abort()
      } catch {
        // Ignore abort errors.
      }
    }

    this.callbacks.onStateChange?.('closed')
    if (!options.silent) {
      this.callbacks.onPartialText?.(this.buildMergedText())
    }
  }

  private buildMergedText(): string {
    const parts = [...this.finalSegments]
    const interim = this.interimSegment.trim()
    if (interim) {
      parts.push(interim)
    }
    return parts.join('\n')
  }
}
