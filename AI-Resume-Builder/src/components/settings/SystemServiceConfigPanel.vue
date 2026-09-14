<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import {
  fetchSystemServiceSnapshots,
  restoreSystemService,
  saveSystemService,
  testSystemService,
  type SystemServiceKey,
  type SystemServiceSnapshot,
} from '@/services/systemServiceConfigService'
import { RAG_DEFAULTS } from '@/constants/ragDefaults'
import {
  getBailianHotwordLimit,
  normalizeBailianHotwords,
} from '@/constants/bailianHotwordPolicy'

type DraftValue = string | number
type RealtimeProvider = 'openai' | 'bailian' | 'volcengine'
type SmtpPresetKey = 'qq' | '163' | '126' | 'gmail' | 'outlook' | 'custom'
type SmtpSecurityMode = 'ssl' | 'starttls' | 'none'
type Draft = {
  config: Record<string, DraftValue>
  secrets: Record<string, string>
}

type DraftCollection = {
  embedding: Draft
  chat: Draft
  vision: Draft
  realtime: Draft
  rag: Draft
  smtp: Draft
}

type SnapshotCollection = {
  embedding: SystemServiceSnapshot | null
  chat: SystemServiceSnapshot | null
  vision: SystemServiceSnapshot | null
  realtime: SystemServiceSnapshot | null
  rag: SystemServiceSnapshot | null
  smtp: SystemServiceSnapshot | null
}

type FeedbackCollection = {
  embedding: string
  chat: string
  vision: string
  realtime: string
  rag: string
  smtp: string
}

type FeedbackKindCollection = {
  embedding: 'success' | 'error' | ''
  chat: 'success' | 'error' | ''
  vision: 'success' | 'error' | ''
  realtime: 'success' | 'error' | ''
  rag: 'success' | 'error' | ''
  smtp: 'success' | 'error' | ''
}

const serviceOrder: SystemServiceKey[] = ['embedding', 'chat', 'vision', 'realtime', 'rag', 'smtp']
const serviceLabels: Record<SystemServiceKey, string> = {
  embedding: '知识库 Embedding',
  chat: '聊天模型',
  vision: '图片 OCR',
  realtime: '实时语音',
  rag: 'RAG 检索',
  smtp: 'SMTP 邮件',
}
const serviceDescriptions: Record<SystemServiceKey, string> = {
  embedding: '用于知识库文档切块后的向量生成与语义检索。',
  chat: '用于简历优化、AI 面试和其他文本对话。',
  vision: '用于图片简历和图片资料的文字提取。',
  realtime: '用于浏览器实时语音转写会话。',
  rag: '设置 AI 面试的知识库检索，适用于“我是面试官”和“我是候选人”两种模式。',
  smtp: '仅用于注册和密码重置验证码邮件。',
}
const DEFAULT_OPENAI_BASE_URL = 'https://api.openai.com/v1'
const DEFAULT_OLLAMA_BASE_URL = 'http://127.0.0.1:11434'
const smtpPresetOptions: Record<SmtpPresetKey, { label: string; host: string; port: number; securityMode: SmtpSecurityMode }> = {
  qq: { label: 'QQ 邮箱', host: 'smtp.qq.com', port: 465, securityMode: 'ssl' },
  '163': { label: '网易 163 邮箱', host: 'smtp.163.com', port: 465, securityMode: 'ssl' },
  '126': { label: '网易 126 邮箱', host: 'smtp.126.com', port: 465, securityMode: 'ssl' },
  gmail: { label: 'Gmail', host: 'smtp.gmail.com', port: 465, securityMode: 'ssl' },
  outlook: { label: 'Outlook / Hotmail', host: 'smtp-mail.outlook.com', port: 587, securityMode: 'starttls' },
  custom: { label: '其他邮箱 / 自定义', host: '', port: 465, securityMode: 'ssl' },
}

const snapshots = ref<SnapshotCollection>(emptySnapshots())
const drafts = reactive<DraftCollection>(createDrafts())
const busyKey = ref<SystemServiceKey | null>(null)
const busyAction = ref<'load' | 'test' | 'save' | 'restore' | null>(null)
const loading = ref(true)
const pageError = ref('')
const smtpPreset = ref<SmtpPresetKey>('qq')
const realtimeSecretProvider = ref<RealtimeProvider | null>(null)
const realtimeDraftSecretConfigured = ref(false)
const realtimeHotwordsError = ref('')
const feedback = reactive<FeedbackCollection>({
  embedding: '',
  chat: '',
  vision: '',
  realtime: '',
  rag: '',
  smtp: '',
})
const feedbackKind = reactive<FeedbackKindCollection>({
  embedding: '',
  chat: '',
  vision: '',
  realtime: '',
  rag: '',
  smtp: '',
})

function emptySnapshots(): SnapshotCollection {
  return { embedding: null, chat: null, vision: null, realtime: null, rag: null, smtp: null }
}

function createDrafts(): DraftCollection {
  return {
    embedding: {
      config: {
        provider: 'openai',
        model: 'text-embedding-3-large',
        baseUrl: 'https://api.openai.com/v1',
      },
      secrets: { apiKey: '' },
    },
    chat: {
      config: {
        provider: 'openai',
        baseUrl: 'https://api.openai.com/v1',
        model: 'gpt-5.4',
        completionsPath: '/v1/chat/completions',
      },
      secrets: { apiKey: '' },
    },
    vision: {
      config: {
        provider: 'openai',
        baseUrl: 'https://api.openai.com/v1',
        model: 'gpt-4.1',
        detail: 'high',
      },
      secrets: { apiKey: '' },
    },
    realtime: {
      config: {
        provider: 'openai',
        transcriptionModel: 'gpt-4o-transcribe',
      },
      secrets: { apiKey: '' },
    },
    rag: {
      config: { ...RAG_DEFAULTS },
      secrets: {},
    },
    smtp: {
      config: {
        host: 'smtp.qq.com',
        port: 465,
        securityMode: 'ssl',
        username: '',
      },
      secrets: { authorizationCode: '' },
    },
  }
}

function copyConfig(config: Record<string, unknown>): Record<string, DraftValue> {
  const copied: Record<string, DraftValue> = {}
  Object.entries(config).forEach(([key, value]) => {
    if (typeof value === 'number' || typeof value === 'string') copied[key] = value
  })
  return copied
}

function normalizeProvider(value: unknown): 'openai' | 'ollama' {
  return String(value ?? '').trim().toLowerCase() === 'ollama' ? 'ollama' : 'openai'
}

function normalizeRealtimeProvider(value: unknown): RealtimeProvider {
  const provider = String(value ?? '').trim().toLowerCase()
  if (provider === 'dashscope' || provider === 'aliyun' || provider === 'qwen' || provider === 'bailian') return 'bailian'
  if (provider === 'volcengine' || provider === 'doubao') return 'volcengine'
  return 'openai'
}

function ollamaBaseUrl(serviceKey: 'embedding' | 'chat' | 'vision', openAiCompatible: boolean): string {
  let baseUrl = String(snapshots.value[serviceKey]?.ollamaBaseUrl ?? '').trim().replace(/\/+$/, '')
  if (!baseUrl) baseUrl = DEFAULT_OLLAMA_BASE_URL

  // Embedding 使用 Ollama 原生 /api/embed；Chat/OCR 使用 OpenAI 兼容 /v1。
  if (openAiCompatible) return baseUrl.endsWith('/v1') ? baseUrl : `${baseUrl}/v1`
  return baseUrl.endsWith('/v1') ? baseUrl.slice(0, -3) : baseUrl
}

function isKnownOllamaHost(value: unknown): boolean {
  const normalized = String(value ?? '').trim().toLowerCase().replace(/\/+$/, '')
  return [
    'http://127.0.0.1:11434',
    'http://localhost:11434',
    'http://host.docker.internal:11434',
    'http://127.0.0.1:11434/v1',
    'http://localhost:11434/v1',
    'http://host.docker.internal:11434/v1',
  ].includes(normalized)
}

function applySnapshot(snapshot: SystemServiceSnapshot) {
  snapshots.value[snapshot.serviceKey] = snapshot
  const config = copyConfig(snapshot.config)
  if (snapshot.serviceKey === 'embedding' || snapshot.serviceKey === 'chat' || snapshot.serviceKey === 'vision') {
    // 千问等兼容 OpenAI 协议的服务统一走 OpenAI 兼容接入方式。
    config.provider = normalizeProvider(config.provider)
    // 升级后把旧版本写入数据库的本机/容器默认地址切换到当前部署的
    // Ollama 地址；管理员填写的远程自定义地址不被覆盖。
    if (config.provider === 'ollama' && isKnownOllamaHost(config.baseUrl)) {
      config.baseUrl = ollamaBaseUrl(snapshot.serviceKey, snapshot.serviceKey !== 'embedding')
    }
  }
  if (snapshot.serviceKey === 'realtime') {
    config.provider = normalizeRealtimeProvider(config.provider)
    if (config.provider === 'bailian') {
      config.region = 'cn-beijing'
      realtimeHotwordsError.value = normalizeBailianHotwords(
        config.hotwords,
        config.model,
      ).error
    } else {
      realtimeHotwordsError.value = ''
    }
    realtimeSecretProvider.value = config.provider as RealtimeProvider
    realtimeDraftSecretConfigured.value = snapshot.secrets.apiKey?.configured === true
  }
  if (snapshot.serviceKey === 'smtp') {
    config.securityMode = normalizeSmtpSecurityMode(config.securityMode, Number(config.port))
    smtpPreset.value = detectSmtpPreset(config)
  }
  drafts[snapshot.serviceKey].config = config
  Object.keys(drafts[snapshot.serviceKey].secrets).forEach((name) => {
    drafts[snapshot.serviceKey].secrets[name] = ''
  })
}

function detectSmtpPreset(config: Record<string, DraftValue>): SmtpPresetKey {
  const host = String(config.host ?? '').trim().toLowerCase()
  const port = Number(config.port)
  const matched = (Object.keys(smtpPresetOptions) as SmtpPresetKey[]).find((key) => {
    if (key === 'custom') return false
    const preset = smtpPresetOptions[key]
    return preset.host === host && preset.port === port
  })
  return matched ?? 'custom'
}

function normalizeSmtpSecurityMode(value: unknown, port: number): SmtpSecurityMode {
  const mode = String(value ?? '').trim().toLowerCase()
  if (mode === 'ssl' || mode === 'starttls' || mode === 'none') return mode
  return port === 465 ? 'ssl' : 'starttls'
}

function handleSmtpPresetChange() {
  if (smtpPreset.value === 'custom') return
  const preset = smtpPresetOptions[smtpPreset.value]
  drafts.smtp.config.host = preset.host
  drafts.smtp.config.port = preset.port
  drafts.smtp.config.securityMode = preset.securityMode
}

function syncSmtpPreset() {
  smtpPreset.value = detectSmtpPreset(drafts.smtp.config)
}

async function loadSnapshots() {
  loading.value = true
  busyAction.value = 'load'
  pageError.value = ''
  try {
    const loaded = await fetchSystemServiceSnapshots()
    loaded.forEach(applySnapshot)
  } catch (error) {
    pageError.value = error instanceof Error ? error.message : '系统服务配置加载失败'
  } finally {
    loading.value = false
    busyAction.value = null
  }
}

function setFeedback(key: SystemServiceKey, kind: 'success' | 'error', message: string) {
  feedbackKind[key] = kind
  feedback[key] = message
}

function clearFeedback(key: SystemServiceKey) {
  feedbackKind[key] = ''
  feedback[key] = ''
}

function isBusy(key: SystemServiceKey): boolean {
  return busyKey.value === key
}

function handleEmbeddingProviderChange() {
  const config = drafts.embedding.config
  if (config.provider === 'ollama') {
    config.baseUrl = ollamaBaseUrl('embedding', false)
    config.model = 'nomic-embed-text'
    return
  }
  config.baseUrl = DEFAULT_OPENAI_BASE_URL
  config.model = 'text-embedding-3-large'
}

function handleChatProviderChange() {
  const config = drafts.chat.config
  if (config.provider === 'ollama') {
    config.baseUrl = ollamaBaseUrl('chat', true)
    config.model = 'llama3.2'
    config.completionsPath = '/chat/completions'
    return
  }
  config.baseUrl = DEFAULT_OPENAI_BASE_URL
  config.model = 'gpt-5.4'
  config.completionsPath = '/v1/chat/completions'
}

function handleVisionProviderChange() {
  const config = drafts.vision.config
  if (config.provider === 'ollama') {
    config.baseUrl = ollamaBaseUrl('vision', true)
    config.model = 'gemma4'
    delete config.detail
    return
  }
  config.baseUrl = DEFAULT_OPENAI_BASE_URL
  config.model = 'gpt-4.1'
  config.detail = 'high'
}

function handleRealtimeProviderChange() {
  const config = drafts.realtime.config
  const provider = normalizeRealtimeProvider(config.provider)
  config.provider = provider
  realtimeHotwordsError.value = ''
  // 不要把上一个供应商的密钥带到新供应商配置中。密钥输入框只显示掩码状态，
  // 切换后必须重新输入，避免误把 OpenAI/百炼 Key 当成豆包 App Key 保存。
  drafts.realtime.secrets.apiKey = ''
  realtimeSecretProvider.value = null
  realtimeDraftSecretConfigured.value = false
  if (provider === 'openai') {
    delete config.baseUrl
    config.transcriptionModel = 'gpt-4o-transcribe'
    delete config.region
    delete config.workspaceId
    delete config.model
    delete config.hotwords
    return
  }
  if (provider === 'bailian') {
    config.region = 'cn-beijing'
    config.workspaceId = ''
    config.model = 'qwen-audio-3.0-asr-flash-streaming'
    config.hotwords = ''
    delete config.baseUrl
    delete config.transcriptionModel
    return
  }
  delete config.baseUrl
  delete config.transcriptionModel
  delete config.region
  delete config.workspaceId
  delete config.model
  delete config.hotwords
}

function currentBailianHotwordValidation() {
  return normalizeBailianHotwords(
    drafts.realtime.config.hotwords,
    drafts.realtime.config.model,
  )
}

function handleRealtimeHotwordsInput() {
  if (normalizeRealtimeProvider(drafts.realtime.config.provider) !== 'bailian') {
    realtimeHotwordsError.value = ''
    return
  }
  realtimeHotwordsError.value = currentBailianHotwordValidation().error
}

function validateRealtimeHotwords(): boolean {
  if (normalizeRealtimeProvider(drafts.realtime.config.provider) !== 'bailian') return true
  const validation = currentBailianHotwordValidation()
  realtimeHotwordsError.value = validation.error
  if (validation.error) return false
  drafts.realtime.config.hotwords = validation.normalized
  return true
}

function payloadFor(key: SystemServiceKey) {
  const draft = drafts[key]
  const config = { ...draft.config }
  if (key === 'rag') {
    delete config.chunkSize
    delete config.chunkOverlap
    delete config.maxFileSizeMb
  }
  if (key === 'vision' && config.provider === 'ollama') delete config.detail
  const secrets = Object.fromEntries(
    Object.entries(draft.secrets).map(([name, value]) => [name, String(value ?? '')]),
  )
  if (key === 'realtime') {
    const provider = normalizeRealtimeProvider(config.provider)
    if (provider === 'openai') {
      return {
        config: {
          provider,
          transcriptionModel: String(config.transcriptionModel ?? ''),
        },
        secrets,
      }
    }
    if (provider === 'bailian') {
      const hotwordValidation = normalizeBailianHotwords(config.hotwords, config.model)
      return {
        config: {
          provider,
          region: 'cn-beijing',
          workspaceId: String(config.workspaceId ?? ''),
          model: String(config.model ?? 'qwen-audio-3.0-asr-flash-streaming'),
          hotwords: hotwordValidation.normalized,
        },
        secrets,
      }
    }
    return { config: { provider }, secrets }
  }
  return { config, secrets }
}

async function handleTest(key: SystemServiceKey) {
  clearFeedback(key)
  if (key === 'realtime' && !validateRealtimeHotwords()) {
    setFeedback(key, 'error', realtimeHotwordsError.value)
    return
  }
  busyKey.value = key
  busyAction.value = 'test'
  try {
    const result = await testSystemService(key, payloadFor(key))
    if (!result.success) {
      setFeedback(key, 'error', result.message)
      return
    }
    setFeedback(key, 'success', `${result.message}（${result.elapsedMs}ms）`)
  } catch (error) {
    setFeedback(key, 'error', error instanceof Error ? error.message : '连接测试失败')
  } finally {
    busyKey.value = null
    busyAction.value = null
  }
}

async function handleSave(key: SystemServiceKey) {
  clearFeedback(key)
  if (key === 'realtime' && !validateRealtimeHotwords()) {
    setFeedback(key, 'error', realtimeHotwordsError.value)
    return
  }
  const snapshot = snapshots.value[key]
  if (!snapshot) return
  busyKey.value = key
  busyAction.value = 'save'
  try {
    const saved = await saveSystemService(key, {
      ...payloadFor(key),
      expectedVersion: snapshot.version,
    })
    applySnapshot(saved)
    setFeedback(
      key,
      'success',
      key === 'rag'
        ? 'RAG 配置已保存并启用。'
        : '连接测试通过，配置已保存并启用。',
    )
  } catch (error) {
    setFeedback(key, 'error', error instanceof Error ? error.message : '系统服务保存失败')
  } finally {
    busyKey.value = null
    busyAction.value = null
  }
}

async function handleRestore(key: SystemServiceKey) {
  const snapshot = snapshots.value[key]
  if (!snapshot) return
  if (!window.confirm(`确认恢复“${serviceLabels[key]}”的部署默认配置吗？数据库覆盖配置会被删除。`)) return
  clearFeedback(key)
  busyKey.value = key
  busyAction.value = 'restore'
  try {
    const restored = await restoreSystemService(key, snapshot.version)
    applySnapshot(restored)
    setFeedback(key, 'success', '已恢复部署默认配置。')
  } catch (error) {
    setFeedback(key, 'error', error instanceof Error ? error.message : '恢复部署默认配置失败')
  } finally {
    busyKey.value = null
    busyAction.value = null
  }
}

function snapshotStatus(key: SystemServiceKey): string {
  const snapshot = snapshots.value[key]
  if (!snapshot) return '未加载'
  if (snapshot.source !== 'database') return '部署默认 · 未校验'
  const statusLabel = snapshot.status === 'success'
    ? '通过'
    : snapshot.status === 'failed' || snapshot.status === 'error'
      ? '失败'
      : '未记录'
  return `数据库配置 · 上次校验${statusLabel}`
}

function secretHint(key: SystemServiceKey, name: string): string {
  if (key === 'realtime' && name === 'apiKey') {
    const provider = normalizeRealtimeProvider(drafts.realtime.config.provider)
    if (realtimeSecretProvider.value !== provider || !realtimeDraftSecretConfigured.value) return '未配置'
  }
  const state = snapshots.value[key]?.secrets[name]
  if (!state?.configured) return '未配置'
  return state.masked || (state.lastFour ? `*****${state.lastFour}` : '*****')
}

function secretInputClass(key: SystemServiceKey, name: string): Record<string, boolean> {
  if (key === 'realtime' && name === 'apiKey') {
    const provider = normalizeRealtimeProvider(drafts.realtime.config.provider)
    return { 'secret-input-unconfigured': realtimeSecretProvider.value !== provider || !realtimeDraftSecretConfigured.value }
  }
  return { 'secret-input-unconfigured': snapshots.value[key]?.secrets[name]?.configured !== true }
}

onMounted(() => {
  void loadSnapshots()
})
</script>

<template>
  <main class="system-service-panel">
    <div class="system-service-shell">
      <header class="page-header">
        <div>
          <h1>系统服务配置</h1>
          <p>管理员统一维护 AI、RAG 和注册邮件服务。密钥只在服务端加密保存，不会回填到浏览器。</p>
        </div>
        <button class="refresh-btn" type="button" :disabled="loading" @click="loadSnapshots">刷新状态</button>
      </header>

      <div v-if="pageError" class="page-alert error" role="alert">{{ pageError }}</div>
      <div v-if="loading" class="loading-card">正在读取系统服务状态…</div>

      <section v-else class="service-grid" aria-label="系统服务配置列表">
        <article v-for="key in serviceOrder" :key="key" class="service-card">
          <header class="card-header">
            <div>
              <div class="card-title-row">
                <h2>{{ serviceLabels[key] }}</h2>
                <span class="status-pill">{{ snapshotStatus(key) }}</span>
              </div>
              <p>{{ serviceDescriptions[key] }}</p>
            </div>
          </header>

          <div v-if="key === 'embedding'" class="form-grid">
            <label class="field field-full"><span>接入方式</span><select v-model="drafts.embedding.config.provider" @change="handleEmbeddingProviderChange"><option value="openai">OpenAI 兼容 API</option><option value="ollama">Ollama 本地模型</option></select></label>
            <label class="field field-full"><span>Base URL</span><input v-model="drafts.embedding.config.baseUrl" type="url" autocomplete="off" /></label>
            <label class="field field-full"><span>模型</span><input v-model="drafts.embedding.config.model" type="text" autocomplete="off" /></label>
            <label v-if="drafts.embedding.config.provider !== 'ollama'" class="field field-full"><span>API Key</span><input v-model="drafts.embedding.secrets.apiKey" class="secret-input" :class="secretInputClass('embedding', 'apiKey')" type="password" autocomplete="new-password" :placeholder="secretHint('embedding', 'apiKey')" /></label>
          </div>

          <div v-else-if="key === 'chat'" class="form-grid">
            <label class="field field-full"><span>接入方式</span><select v-model="drafts.chat.config.provider" @change="handleChatProviderChange"><option value="openai">OpenAI 兼容 API</option><option value="ollama">Ollama 本地模型</option></select></label>
            <label class="field field-full"><span>Base URL</span><input v-model="drafts.chat.config.baseUrl" type="url" autocomplete="off" /></label>
            <label class="field"><span>模型</span><input v-model="drafts.chat.config.model" type="text" autocomplete="off" /></label>
            <label class="field"><span>接口路径</span><input v-model="drafts.chat.config.completionsPath" type="text" autocomplete="off" /></label>
            <label v-if="drafts.chat.config.provider !== 'ollama'" class="field field-full"><span>API Key</span><input v-model="drafts.chat.secrets.apiKey" class="secret-input" :class="secretInputClass('chat', 'apiKey')" type="password" autocomplete="new-password" :placeholder="secretHint('chat', 'apiKey')" /></label>
          </div>

          <div v-else-if="key === 'vision'" class="form-grid">
            <label class="field field-full"><span>接入方式</span><select v-model="drafts.vision.config.provider" @change="handleVisionProviderChange"><option value="openai">OpenAI 兼容 API</option><option value="ollama">Ollama 本地模型</option></select></label>
            <label class="field field-full"><span>Base URL</span><input v-model="drafts.vision.config.baseUrl" type="url" autocomplete="off" /></label>
            <label class="field" :class="{ 'field-full': drafts.vision.config.provider === 'ollama' }"><span>模型</span><input v-model="drafts.vision.config.model" type="text" autocomplete="off" /></label>
            <label v-if="drafts.vision.config.provider !== 'ollama'" class="field"><span>图片识别精度</span><select v-model="drafts.vision.config.detail"><option value="high">high</option><option value="auto">auto</option><option value="low">low</option></select></label>
            <label v-if="drafts.vision.config.provider !== 'ollama'" class="field field-full"><span>API Key</span><input v-model="drafts.vision.secrets.apiKey" class="secret-input" :class="secretInputClass('vision', 'apiKey')" type="password" autocomplete="new-password" :placeholder="secretHint('vision', 'apiKey')" /></label>
          </div>

          <div v-else-if="key === 'realtime'" class="form-grid">
            <label class="field field-full"><span>接入方式</span><select v-model="drafts.realtime.config.provider" @change="handleRealtimeProviderChange"><option value="openai">OpenAI 实时转写</option><option value="bailian">阿里百炼 Inference ASR</option><option value="volcengine">豆包流式语音识别 2.0</option></select></label>
            <template v-if="normalizeRealtimeProvider(drafts.realtime.config.provider) === 'openai'">
              <label class="field field-full"><span>识别模型</span><input v-model="drafts.realtime.config.transcriptionModel" type="text" autocomplete="off" /></label>
              <label class="field field-full"><span>API Key</span><input v-model="drafts.realtime.secrets.apiKey" class="secret-input" :class="secretInputClass('realtime', 'apiKey')" type="password" autocomplete="new-password" :placeholder="secretHint('realtime', 'apiKey')" /></label>
            </template>
            <template v-else-if="normalizeRealtimeProvider(drafts.realtime.config.provider) === 'bailian'">
              <label class="field"><span>地域</span><input value="华北 2（北京）" type="text" readonly /></label>
              <label class="field"><span>Workspace ID</span><input v-model="drafts.realtime.config.workspaceId" type="text" autocomplete="off" /></label>
              <label class="field field-full"><span>模型</span><select v-model="drafts.realtime.config.model" @change="handleRealtimeHotwordsInput"><option value="qwen-audio-3.0-asr-flash-streaming">qwen-audio-3.0-asr-flash-streaming</option><option value="fun-asr-realtime">fun-asr-realtime</option></select></label>
              <label class="field field-full"><span>API Key</span><input v-model="drafts.realtime.secrets.apiKey" class="secret-input" :class="secretInputClass('realtime', 'apiKey')" type="password" autocomplete="new-password" :placeholder="secretHint('realtime', 'apiKey')" /></label>
              <label class="field field-full"><span>热词（每行一个）</span><textarea v-model="drafts.realtime.config.hotwords" rows="4" autocomplete="off" placeholder="Java&#10;MySQL&#10;Redis" @input="handleRealtimeHotwordsInput"></textarea></label>
              <div v-if="realtimeHotwordsError" class="field-help field-full realtime-hotwords-error" role="alert">{{ realtimeHotwordsError }}</div>
              <div class="field-help field-full">按百炼规则：含非 ASCII 字符的热词不超过 15 个字符；纯 ASCII 热词按空格分隔不超过 7 个片段；当前模型最多 {{ getBailianHotwordLimit(drafts.realtime.config.model) }} 条。空行会忽略，重复热词会合并。</div>
              <div class="readonly-note field-full">面试上下文自动使用最近对话；VAD 使用百炼官方默认值。</div>
            </template>
            <template v-else>
              <div class="readonly-note field-full">资源、WebSocket 地址和协议参数由后端固定。</div>
              <label class="field field-full"><span>App Key</span><input v-model="drafts.realtime.secrets.apiKey" class="secret-input" :class="secretInputClass('realtime', 'apiKey')" type="password" autocomplete="new-password" :placeholder="secretHint('realtime', 'apiKey')" /></label>
            </template>
          </div>

          <div v-else-if="key === 'rag'" class="form-grid">
            <label class="field field-full"><span>面试检索数量</span><input v-model.number="drafts.rag.config.interviewTopK" type="number" min="1" :max="RAG_DEFAULTS.interviewTopK" /></label>
            <div class="field-help field-full">两种面试模式共用此配置，每轮最多使用 {{ RAG_DEFAULTS.interviewTopK }} 段相关内容。</div>
            <label class="field field-full"><span>相似度阈值</span><input v-model.number="drafts.rag.config.similarityThreshold" type="number" min="0" max="1" step="0.01" /></label>
            <div class="field-help field-full">筛选与当前面试内容相关的资料，阈值越高，筛选越严格。</div>
            <div class="readonly-note field-full">只读部署参数：切块 {{ drafts.rag.config.chunkSize }} · 重叠 {{ drafts.rag.config.chunkOverlap }} · 单文件 {{ drafts.rag.config.maxFileSizeMb }} MB。它们与已有向量和 Nginx 限制绑定。</div>
          </div>

          <div v-else class="form-grid">
            <label class="field field-full"><span>邮箱服务商</span><select v-model="smtpPreset" @change="handleSmtpPresetChange"><option v-for="(preset, presetKey) in smtpPresetOptions" :key="presetKey" :value="presetKey">{{ preset.label }}</option></select></label>
            <label class="field"><span>SMTP 主机</span><input v-model="drafts.smtp.config.host" type="text" autocomplete="off" @input="syncSmtpPreset" /></label>
            <label class="field"><span>端口</span><input v-model.number="drafts.smtp.config.port" type="number" min="1" max="65535" @input="syncSmtpPreset" /></label>
            <label class="field field-full"><span>加密方式</span><select v-model="drafts.smtp.config.securityMode"><option value="ssl">SSL（465）</option><option value="starttls">STARTTLS（常用 587）</option><option value="none">无加密</option></select></label>
            <label class="field field-full"><span>邮箱账号</span><input v-model="drafts.smtp.config.username" type="email" autocomplete="off" /></label>
            <label class="field field-full"><span>SMTP 授权码</span><input v-model="drafts.smtp.secrets.authorizationCode" class="secret-input" :class="secretInputClass('smtp', 'authorizationCode')" type="password" autocomplete="new-password" :placeholder="secretHint('smtp', 'authorizationCode')" /></label>
          </div>

          <div v-if="key === 'embedding' && snapshots.embedding?.requiresRebuild" class="rebuild-warning">当前 Embedding 配置与已有向量不兼容（模型或维度可能已变化）。请重新上传知识库资料；旧向量不会删除，也不会参与当前检索。</div>
          <div v-if="feedback[key]" class="card-feedback" :class="feedbackKind[key]" role="status">{{ feedback[key] }}</div>
          <footer class="card-actions">
            <button v-if="key !== 'rag'" type="button" class="secondary-btn" :disabled="isBusy(key)" @click="handleTest(key)">{{ isBusy(key) && busyAction === 'test' ? '测试中…' : '测试连接' }}</button>
            <button type="button" class="primary-btn" :disabled="isBusy(key)" @click="handleSave(key)">{{ isBusy(key) && busyAction === 'save' ? '保存中…' : '保存并启用' }}</button>
            <button type="button" class="text-btn" :disabled="isBusy(key) || snapshots[key]?.source !== 'database'" @click="handleRestore(key)">{{ isBusy(key) && busyAction === 'restore' ? '恢复中…' : '恢复部署默认' }}</button>
          </footer>
        </article>
      </section>
    </div>
  </main>
</template>

<style scoped>
.system-service-panel,
.system-service-panel * { box-sizing: border-box; }
.system-service-panel { flex: 1; width: 100%; min-width: 0; height: 100%; overflow: auto; padding: 28px 20px 72px; background: var(--app-background); color: var(--text-primary); font-family: var(--font-sans); }
.system-service-shell { width: 100%; max-width: 1180px; margin: 0 auto; }
.page-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; margin-bottom: 22px; }
.refresh-btn { border: 0; background: transparent; color: var(--primary-600); font-size: 12px; font-weight: 700; cursor: pointer; }
.refresh-btn { min-height: 36px; padding: 0 14px; border: 1px solid var(--border-color); border-radius: 10px; background: var(--surface-raised); }
.refresh-btn:disabled { opacity: .55; cursor: default; }
.page-header h1 { margin: 0; font-size: clamp(24px, 3vw, 32px); letter-spacing: -.03em; }
.page-header p { max-width: 720px; margin: 8px 0 0; color: var(--text-secondary); font-size: 13px; line-height: 1.7; }
.page-alert, .loading-card { padding: 16px 18px; border: 1px solid var(--border-color); border-radius: 14px; background: var(--surface-raised); color: var(--text-secondary); }
.page-alert.error { border-color: var(--border-danger); color: var(--accent-red-strong); background: var(--accent-red-soft); }
.service-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.service-card { min-width: 0; padding: 20px; border: 1px solid var(--border-color); border-radius: 20px; background: var(--surface-raised); box-shadow: var(--shadow-md); }
.card-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 18px; }
.card-title-row { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.card-header h2 { margin: 0; font-size: 16px; }
.card-header p { margin: 7px 0 0; color: var(--text-secondary); font-size: 12px; line-height: 1.6; }
.status-pill { display: inline-flex; align-items: center; min-height: 24px; padding: 0 8px; border-radius: 999px; font-size: 10px; font-weight: 700; white-space: nowrap; }
.status-pill { color: var(--primary-600); background: var(--primary-50); }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.field { min-width: 0; display: flex; flex-direction: column; gap: 6px; }
.field-full { grid-column: 1 / -1; }
.field > span { color: var(--text-secondary); font-size: 11px; font-weight: 700; }
.field input, .field select, .field textarea { width: 100%; min-height: 38px; padding: 0 11px; border: 1px solid var(--border-color); border-radius: 10px; outline: none; background: var(--surface-base); color: var(--text-primary); font: inherit; font-size: 12px; }
.field textarea { min-height: 84px; padding-top: 9px; resize: vertical; line-height: 1.5; }
.field input:focus, .field select:focus, .field textarea:focus { border-color: var(--primary-500); box-shadow: 0 0 0 3px var(--primary-50); }
.field input::placeholder { color: var(--text-primary); font-size: inherit; opacity: 1; }
.field input.secret-input-unconfigured::placeholder { color: var(--text-tertiary); }
  .field-help { color: var(--text-tertiary); font-size: 10px; line-height: 1.55; }
  .realtime-hotwords-error { color: var(--accent-red-strong); }
.readonly-note { padding: 11px 12px; border: 1px dashed var(--border-color); border-radius: 10px; color: var(--text-tertiary); font-size: 11px; line-height: 1.6; }
.rebuild-warning { margin-top: 14px; padding: 10px 12px; border-radius: 10px; background: #fff8e8; color: #8a5b08; font-size: 11px; line-height: 1.6; }
.card-feedback { margin-top: 14px; padding: 9px 11px; border-radius: 9px; font-size: 11px; line-height: 1.5; }
.card-feedback.success { background: var(--primary-50); color: var(--primary-600); }
.card-feedback.error { background: var(--accent-red-soft); color: var(--accent-red-strong); }
.card-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin-top: 18px; }
.secondary-btn, .primary-btn, .text-btn { min-height: 36px; padding: 0 13px; border-radius: 10px; font-size: 11px; font-weight: 700; cursor: pointer; }
.secondary-btn { border: 1px solid var(--border-color); background: var(--surface-base); color: var(--text-secondary); }
.primary-btn { border: 1px solid var(--primary-500); background: var(--primary-500); color: var(--text-inverse); }
.text-btn { border: 0; background: transparent; color: var(--text-tertiary); }
.secondary-btn:hover, .secondary-btn:focus-visible { border-color: var(--primary-500); color: var(--primary-600); outline: none; }
.primary-btn:hover, .primary-btn:focus-visible { background: var(--primary-600); outline: none; }
.text-btn:hover:not(:disabled), .text-btn:focus-visible:not(:disabled) { color: var(--accent-red-strong); outline: none; }
.secondary-btn:disabled, .primary-btn:disabled, .text-btn:disabled { opacity: .55; cursor: default; }
@media (max-width: 900px) { .service-grid { grid-template-columns: 1fr; } }
@media (max-width: 560px) { .system-service-panel { padding: 18px 12px calc(84px + env(safe-area-inset-bottom)); } .page-header { flex-direction: column; } .refresh-btn { align-self: flex-start; } .service-card { padding: 16px; border-radius: 17px; } .form-grid { grid-template-columns: 1fr; } .field-full { grid-column: auto; } .card-actions > * { flex: 1 1 auto; } }
</style>
