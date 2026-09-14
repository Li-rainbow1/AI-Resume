import {
  type RealtimeClientSecretRequest,
  type RealtimeClientSecretResponse,
  postRealtimeCallSdp,
  postRealtimeClientSecret,
} from '@/api/realtimeSpeechApi'
import type { SpeechRuntimeState } from '@/services/browserSpeechService'

const TEXT = {
  getSecretFailed: '获取实时语音会话失败',
  missingSecret: '后端未返回可用的实时会话密钥',
  missingRealtimeBaseUrl: '后端未返回实时服务地址',
  unsupportedMediaDevice: '当前浏览器不支持麦克风访问',
  unsupportedWebrtc: '当前浏览器不支持实时语音识别',
  dataChannelError: '实时语音通道异常，请重试',
  connectionDisconnected: '实时语音连接断开，请重试',
  connectFailed: '建立实时语音连接失败',
  recognitionFailed: '实时语音识别失败',
  connectionTimeout: '实时语音连接超时，请重试',
  unsupportedWebSocket: '当前浏览器不支持实时语音 WebSocket',
  missingSessionTicket: '后端未返回可用的实时语音会话票据',
} as const

export interface RealtimeTranscriptionCallbacks {
  onPartialText?: (text: string) => void
  onFinalText?: (segment: string, mergedText: string) => void
  onStateChange?: (state: SpeechRuntimeState) => void
  onError?: (message: string) => void
}

export interface RealtimeTranscriptionOptions {
  model?: string
  language?: string
  context?: Array<{ role: 'user' | 'assistant'; content: string }>
  callbacks?: RealtimeTranscriptionCallbacks
}

function extractErrorText(text: string): string {
  const cleaned = text.trim()
  if (!cleaned) return ''
  try {
    const parsed = JSON.parse(cleaned) as { error?: string; message?: string }
    return parsed.message?.trim() || parsed.error?.trim() || cleaned
  } catch {
    return cleaned
  }
}

function normalizeBaseUrl(baseUrl: string): string {
  return (baseUrl || '').trim().replace(/\/+$/, '')
}

function normalizePath(path: string, fallback: string): string {
  const value = (path || '').trim() || fallback
  return value.startsWith('/') ? value : `/${value}`
}

function buildRealtimeCallsUrl(baseUrl: string, callsPath?: string): string {
  return `${normalizeBaseUrl(baseUrl)}${normalizePath(callsPath || '', '/v1/realtime/calls')}`
}

function buildBackendWebSocketUrl(path: string): string {
  const target = path.trim()
  if (target.startsWith('ws://') || target.startsWith('wss://')) {
    return target
  }
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}${normalizePath(target, '/ws/ai/realtime-asr')}`
}

function asText(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function normalizeProvider(provider: unknown): 'openai' | 'bailian' | 'volcengine' {
  const normalized = asText(provider).trim().toLowerCase()
  if (normalized === 'dashscope' || normalized === 'aliyun' || normalized === 'qwen' || normalized === 'bailian') return 'bailian'
  if (normalized === 'volcengine' || normalized === 'doubao') return 'volcengine'
  return 'openai'
}

async function requestRealtimeClientSecret(
  payload: RealtimeClientSecretRequest = {},
  signal?: AbortSignal
): Promise<RealtimeClientSecretResponse> {
  const response = await postRealtimeClientSecret(payload, signal)

  if (!response.ok) {
    const errorText = extractErrorText(await response.text().catch(() => ''))
    throw new Error(`${TEXT.getSecretFailed} (${response.status}): ${errorText || response.statusText}`)
  }

  const data = (await response.json().catch(() => ({}))) as RealtimeClientSecretResponse
  const provider = normalizeProvider(data.provider)

  if (provider !== 'openai') {
    const sessionTicket = asText(data.sessionTicket).trim()
    if (!sessionTicket) throw new Error(TEXT.missingSessionTicket)
    return {
      ...data,
      provider,
      clientSecret: '',
      realtimeApiBaseUrl: '',
      realtimeCallsPath: '',
      sessionTicket,
      websocketPath: data.websocketPath || '/ws/ai/realtime-asr',
      sampleRate: Number(data.sampleRate) || 16000,
    }
  }

  const clientSecret = asText(data.clientSecret).trim()
  if (!clientSecret) {
    throw new Error(TEXT.missingSecret)
  }
  const realtimeApiBaseUrl = normalizeBaseUrl(data.realtimeApiBaseUrl || '')
  if (!realtimeApiBaseUrl) {
    throw new Error(TEXT.missingRealtimeBaseUrl)
  }

  return {
    ...data,
    provider,
    clientSecret,
    realtimeApiBaseUrl,
    realtimeCallsPath: normalizePath(data.realtimeCallsPath || '', '/v1/realtime/calls'),
  }
}

function waitForIceGatheringComplete(peerConnection: RTCPeerConnection): Promise<void> {
  if (peerConnection.iceGatheringState === 'complete') {
    return Promise.resolve()
  }

  return new Promise((resolve) => {
    const handleStateChange = () => {
      if (peerConnection.iceGatheringState !== 'complete') {
        return
      }
      peerConnection.removeEventListener('icegatheringstatechange', handleStateChange)
      resolve()
    }

    peerConnection.addEventListener('icegatheringstatechange', handleStateChange)
    setTimeout(() => {
      peerConnection.removeEventListener('icegatheringstatechange', handleStateChange)
      resolve()
    }, 2000)
  })
}

function extractTranscriptFromEvent(payload: Record<string, unknown>, fallback: string): string {
  const direct = asText(payload.transcript).trim()
  if (direct) {
    return direct
  }

  const segment = asText(payload.segment).trim()
  if (segment) {
    return segment
  }

  const text = asText(payload.text).trim()
  if (text) {
    return text
  }

  const item = payload.item as Record<string, unknown> | undefined
  const content = Array.isArray(item?.content) ? item.content : []
  for (const part of content) {
    if (!part || typeof part !== 'object') continue
    const transcript = asText((part as Record<string, unknown>).transcript).trim()
    if (transcript) {
      return transcript
    }
    const innerText = asText((part as Record<string, unknown>).text).trim()
    if (innerText) {
      return innerText
    }
  }

  return fallback
}

function waitForWebSocketOpen(webSocket: WebSocket, timeoutMs: number): Promise<void> {
  if (webSocket.readyState === WebSocket.OPEN) {
    return Promise.resolve()
  }

  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      cleanup()
      reject(new Error(TEXT.connectionTimeout))
    }, timeoutMs)

    const cleanup = () => {
      clearTimeout(timer)
      webSocket.removeEventListener('open', handleOpen)
      webSocket.removeEventListener('error', handleError)
    }

    const handleOpen = () => {
      cleanup()
      resolve()
    }

    const handleError = () => {
      cleanup()
      reject(new Error(TEXT.connectFailed))
    }

    webSocket.addEventListener('open', handleOpen)
    webSocket.addEventListener('error', handleError)
  })
}

function parseRealtimeEvent(raw: unknown): Record<string, unknown> | null {
  const text = asText(raw)
  if (!text.trim()) return null
  try {
    return JSON.parse(text) as Record<string, unknown>
  } catch {
    return null
  }
}

function resampleAudio(input: Float32Array, sourceRate: number, targetRate: number): Float32Array {
  if (sourceRate === targetRate) {
    return input
  }
  const ratio = sourceRate / targetRate
  const length = Math.max(1, Math.round(input.length / ratio))
  const output = new Float32Array(length)
  for (let i = 0; i < length; i += 1) {
    output[i] = input[Math.min(input.length - 1, Math.round(i * ratio))] || 0
  }
  return output
}

function floatToPcm16(input: Float32Array): Uint8Array {
  const buffer = new ArrayBuffer(input.length * 2)
  const view = new DataView(buffer)
  for (let i = 0; i < input.length; i += 1) {
    const sample = Math.max(-1, Math.min(1, input[i] || 0))
    view.setInt16(i * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true)
  }
  return new Uint8Array(buffer)
}

export class RealtimeTranscriptionSession {
  private readonly model?: string
  private readonly language?: string
  private readonly context: Array<{ role: 'user' | 'assistant'; content: string }>
  private readonly callbacks: RealtimeTranscriptionCallbacks

  private peerConnection: RTCPeerConnection | null = null
  private dataChannel: RTCDataChannel | null = null
  private backendWebSocket: WebSocket | null = null
  private audioContext: AudioContext | null = null
  private audioSource: MediaStreamAudioSourceNode | null = null
  private audioProcessor: ScriptProcessorNode | null = null
  private microphoneStream: MediaStream | null = null
  private finalSegments: string[] = []
  private partialSegment = ''
  private stopped = false

  constructor(options: RealtimeTranscriptionOptions = {}) {
    this.model = options.model?.trim()
    this.language = options.language?.trim()
    this.context = options.context || []
    this.callbacks = options.callbacks || {}
  }

  async start(): Promise<void> {
    if (this.peerConnection || this.backendWebSocket) return
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error(TEXT.unsupportedMediaDevice)
    }

    this.stopped = false
    this.callbacks.onStateChange?.('connecting')

    try {
      const clientSecret = await requestRealtimeClientSecret({
        model: this.model,
        language: this.language,
        context: this.context,
      })

      if (clientSecret.provider !== 'openai') {
        await this.startProxySession(clientSecret)
        return
      }

      if (typeof RTCPeerConnection === 'undefined') {
        throw new Error(TEXT.unsupportedWebrtc)
      }

      const microphoneStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      })
      const peerConnection = new RTCPeerConnection()

      this.microphoneStream = microphoneStream
      this.peerConnection = peerConnection

      for (const track of microphoneStream.getTracks()) {
        peerConnection.addTrack(track, microphoneStream)
      }

      const dataChannel = peerConnection.createDataChannel('oai-events')
      this.attachDataChannel(dataChannel)

      peerConnection.ondatachannel = (event) => {
        if (this.dataChannel) return
        this.attachDataChannel(event.channel)
      }

      peerConnection.onconnectionstatechange = () => {
        if (peerConnection.connectionState === 'connected') {
          this.callbacks.onStateChange?.('connected')
          return
        }

        if (['failed', 'disconnected', 'closed'].includes(peerConnection.connectionState)) {
          this.handleError(TEXT.connectionDisconnected)
        }
      }

      const offer = await peerConnection.createOffer()
      await peerConnection.setLocalDescription(offer)
      await waitForIceGatheringComplete(peerConnection)

      const localSdp = peerConnection.localDescription?.sdp || offer.sdp || ''
      const endpoint = buildRealtimeCallsUrl(clientSecret.realtimeApiBaseUrl || '', clientSecret.realtimeCallsPath)
      const response = await postRealtimeCallSdp(endpoint, clientSecret.clientSecret || '', localSdp)

      if (!response.ok) {
        const errorText = extractErrorText(await response.text().catch(() => ''))
        throw new Error(`${TEXT.connectFailed} (${response.status}): ${errorText || response.statusText}`)
      }

      const answerSdp = await response.text()
      await peerConnection.setRemoteDescription({
        type: 'answer',
        sdp: answerSdp,
      })

      await this.waitForConnected(peerConnection, 12000)
    } catch (error) {
      await this.stop({ silent: true })
      throw error
    }
  }

  async stop(options: { silent?: boolean } = {}): Promise<void> {
    if (this.stopped) return
    this.stopped = true

    const dataChannel = this.dataChannel
    const peerConnection = this.peerConnection
    const backendWebSocket = this.backendWebSocket
    const audioProcessor = this.audioProcessor
    const audioSource = this.audioSource
    const audioContext = this.audioContext
    const microphoneStream = this.microphoneStream

    this.dataChannel = null
    this.peerConnection = null
    this.audioProcessor = null
    this.audioSource = null
    this.audioContext = null
    this.microphoneStream = null
    this.partialSegment = ''

    if (dataChannel && dataChannel.readyState !== 'closed') {
      try {
        dataChannel.close()
      } catch {
        // Ignore close errors.
      }
    }

    if (peerConnection && peerConnection.connectionState !== 'closed') {
      try {
        peerConnection.close()
      } catch {
        // Ignore close errors.
      }
    }

    if (backendWebSocket && backendWebSocket.readyState === WebSocket.OPEN) {
      try {
        // 先挂上完成事件监听，再发送结束指令，避免上游响应很快时错过
        // session_finished，导致每次停止都无谓等待超时。
        const finished = this.waitForProxyFinished(backendWebSocket, 6000)
        backendWebSocket.send(JSON.stringify({ type: 'session.finish' }))
        await finished
        backendWebSocket.close()
      } catch {
        // Ignore close errors.
      }
    } else if (backendWebSocket && backendWebSocket.readyState === WebSocket.CONNECTING) {
      try {
        backendWebSocket.close()
      } catch {
        // Ignore close errors.
      }
    }

    this.backendWebSocket = null

    try {
      audioProcessor?.disconnect()
      audioSource?.disconnect()
    } catch {
      // Ignore close errors.
    }

    if (audioContext && audioContext.state !== 'closed') {
      void audioContext.close().catch(() => undefined)
    }

    if (microphoneStream) {
      for (const track of microphoneStream.getTracks()) {
        track.stop()
      }
    }

    this.callbacks.onStateChange?.('closed')
    if (!options.silent) {
      this.emitMergedText()
    }
  }

  private async startProxySession(clientSecret: RealtimeClientSecretResponse): Promise<void> {
    if (typeof WebSocket === 'undefined') {
      throw new Error(TEXT.unsupportedWebSocket)
    }

    const webSocket = new WebSocket(buildBackendWebSocketUrl(clientSecret.websocketPath || '/ws/ai/realtime-asr'))
    this.backendWebSocket = webSocket
    // 连接建立后立即接收上游事件，避免 session_started 后切换监听器的窗口丢失首个 partial。
    webSocket.onmessage = (event) => {
      this.handleRealtimeEvent(event.data)
    }
    webSocket.onerror = () => {
      this.handleError(TEXT.dataChannelError)
    }
    webSocket.onclose = () => {
      if (!this.stopped) {
        this.handleError(TEXT.connectionDisconnected)
      }
    }

    await waitForWebSocketOpen(webSocket, 12000)

    const sampleRate = Number(clientSecret.sampleRate) || 16000
    const waitForStarted = this.waitForProxySessionStarted(webSocket, 12000)
    webSocket.send(JSON.stringify({
      type: 'session.start',
      ticket: clientSecret.sessionTicket,
      sampleRate,
    }))
    await waitForStarted

    const microphoneStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    })
    this.microphoneStream = microphoneStream
    await this.startPcmAudioStreaming(microphoneStream, sampleRate)
    this.callbacks.onStateChange?.('connected')
  }

  private waitForProxySessionStarted(webSocket: WebSocket, timeoutMs: number): Promise<void> {
    return new Promise((resolve, reject) => {
      let settled = false

      const cleanup = () => {
        clearTimeout(timer)
        webSocket.removeEventListener('message', handleMessage)
        webSocket.removeEventListener('error', handleError)
        webSocket.removeEventListener('close', handleClose)
      }

      const settleResolve = () => {
        if (settled) return
        settled = true
        cleanup()
        resolve()
      }

      const settleReject = (message: string) => {
        if (settled) return
        settled = true
        cleanup()
        reject(new Error(message))
      }

      const handleMessage = (event: MessageEvent) => {
        const payload = parseRealtimeEvent(event.data)
        const eventType = asText(payload?.type)
        if (eventType === 'session_started') {
          settleResolve()
          return
        }
        if (eventType === 'error' || eventType.endsWith('.failed')) {
          const message = payload ? this.extractRealtimeErrorMessage(payload) : ''
          settleReject(message || TEXT.recognitionFailed)
        }
      }

      const handleError = () => {
        settleReject(TEXT.connectFailed)
      }

      const handleClose = () => {
        settleReject(TEXT.connectionDisconnected)
      }

      const timer = setTimeout(() => {
        settleReject(TEXT.connectionTimeout)
      }, timeoutMs)

      webSocket.addEventListener('message', handleMessage)
      webSocket.addEventListener('error', handleError)
      webSocket.addEventListener('close', handleClose)
    })
  }

  private waitForProxyFinished(webSocket: WebSocket, timeoutMs: number): Promise<void> {
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        cleanup()
        resolve()
      }, timeoutMs)
      const cleanup = () => {
        clearTimeout(timer)
        webSocket.removeEventListener('message', handleMessage)
        webSocket.removeEventListener('close', handleClose)
      }
      const settle = () => {
        cleanup()
        resolve()
      }
      const handleMessage = (event: MessageEvent) => {
        const payload = parseRealtimeEvent(event.data)
        if (asText(payload?.type) === 'session_finished') settle()
      }
      const handleClose = () => settle()
      webSocket.addEventListener('message', handleMessage)
      webSocket.addEventListener('close', handleClose)
    })
  }

  private async startPcmAudioStreaming(stream: MediaStream, targetSampleRate: number): Promise<void> {
    const AudioContextCtor = window.AudioContext ||
      (window as Window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    if (!AudioContextCtor) {
      throw new Error(TEXT.unsupportedMediaDevice)
    }
    const audioContext = new AudioContextCtor()
    if (audioContext.state === 'suspended') {
      await audioContext.resume().catch(() => undefined)
    }
    const source = audioContext.createMediaStreamSource(stream)
    const processor = audioContext.createScriptProcessor(4096, 1, 1)

    this.audioContext = audioContext
    this.audioSource = source
    this.audioProcessor = processor

    processor.onaudioprocess = (event) => {
      const output = event.outputBuffer.getChannelData(0)
      output.fill(0)
      const webSocket = this.backendWebSocket
      if (!webSocket || webSocket.readyState !== WebSocket.OPEN || this.stopped) {
        return
      }
      const input = event.inputBuffer.getChannelData(0)
      const resampled = resampleAudio(input, audioContext.sampleRate, targetSampleRate)
      const audio = floatToPcm16(resampled)
      if (!audio.byteLength) return
      webSocket.send(audio)
    }

    source.connect(processor)
    processor.connect(audioContext.destination)
  }

  private attachDataChannel(channel: RTCDataChannel) {
    this.dataChannel = channel
    channel.onmessage = (event) => {
      this.handleRealtimeEvent(event.data)
    }
    channel.onerror = () => {
      this.handleError(TEXT.dataChannelError)
    }
  }

  private waitForConnected(peerConnection: RTCPeerConnection, timeoutMs: number): Promise<void> {
    if (peerConnection.connectionState === 'connected') {
      this.callbacks.onStateChange?.('connected')
      return Promise.resolve()
    }

    return new Promise((resolve, reject) => {
      let settled = false

      const onStateChange = () => {
        if (settled) return
        if (peerConnection.connectionState === 'connected') {
          settled = true
          cleanup()
          this.callbacks.onStateChange?.('connected')
          resolve()
          return
        }
        if (['failed', 'disconnected', 'closed'].includes(peerConnection.connectionState)) {
          settled = true
          cleanup()
          reject(new Error(TEXT.connectionDisconnected))
        }
      }

      const timer = setTimeout(() => {
        if (settled) return
        settled = true
        cleanup()
        reject(new Error(TEXT.connectionTimeout))
      }, timeoutMs)

      const cleanup = () => {
        clearTimeout(timer)
        peerConnection.removeEventListener('connectionstatechange', onStateChange)
      }

      peerConnection.addEventListener('connectionstatechange', onStateChange)
      onStateChange()
    })
  }

  private handleRealtimeEvent(raw: unknown) {
    const payload = parseRealtimeEvent(raw)
    if (!payload) return

    const eventType = asText(payload.type)
    if (!eventType) return

    if (eventType === 'session_started') {
      this.callbacks.onStateChange?.('connected')
      return
    }

    if (eventType === 'session_closed') {
      return
    }

    if (eventType === 'partial') {
      const text = asText(payload.text).trim()
      if (!text) return
      this.partialSegment = text
      this.emitMergedText()
      return
    }

    if (eventType === 'completed') {
      const transcript = asText(payload.text).trim()
      this.partialSegment = ''
      if (transcript) {
        this.finalSegments.push(transcript)
        const merged = this.buildMergedText()
        this.callbacks.onFinalText?.(transcript, merged)
        this.callbacks.onPartialText?.(merged)
      }
      return
    }

    if (eventType === 'session_finished') {
      return
    }

    if (eventType === 'conversation.item.input_audio_transcription.text') {
      const preview = asText(payload.mergedText).trim() || asText(payload.text).trim()
      if (!preview) return
      this.partialSegment = preview
      this.emitMergedText()
      return
    }

    if (
      eventType === 'conversation.item.input_audio_transcription.delta' ||
      eventType === 'conversation.item.input_audio_transcription.partial'
    ) {
      const delta = asText(payload.delta) || asText(payload.text)
      if (!delta) return
      this.partialSegment += delta
      this.emitMergedText()
      return
    }

    if (
      eventType === 'conversation.item.input_audio_transcription.completed' ||
      eventType === 'conversation.item.input_audio_transcription.final'
    ) {
      const transcript = extractTranscriptFromEvent(payload, this.partialSegment).trim()
      this.partialSegment = ''
      if (transcript) {
        this.finalSegments.push(transcript)
      }

      const merged = this.buildMergedText()
      if (transcript) {
        this.callbacks.onFinalText?.(transcript, merged)
      }
      this.callbacks.onPartialText?.(merged)
      return
    }

    if (eventType === 'error' || eventType.endsWith('.failed')) {
      const message = this.extractRealtimeErrorMessage(payload)
      this.handleError(message || TEXT.recognitionFailed)
    }
  }

  private emitMergedText() {
    this.callbacks.onPartialText?.(this.buildMergedText())
  }

  private buildMergedText(): string {
    const parts = [...this.finalSegments]
    const partial = this.partialSegment.trim()
    if (partial) {
      parts.push(partial)
    }
    return parts.join('\n')
  }

  private extractRealtimeErrorMessage(payload: Record<string, unknown>): string {
    const directMessage = asText(payload.message).trim()
    if (directMessage) return directMessage

    const error = payload.error as Record<string, unknown> | undefined
    const nestedMessage = error ? asText(error.message).trim() : ''
    if (nestedMessage) return nestedMessage

    return ''
  }

  private handleError(message: string) {
    if (this.stopped) return
    this.callbacks.onError?.(message)
    void this.stop({ silent: true })
  }
}
