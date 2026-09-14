import {
  getRagDocumentImageEnrichment,
  type RagDocumentImageEnrichmentResponse,
} from '@/api/ragApi'

type PollingDocument = {
  documentId?: string | null
  imageEnrichmentStatus?: string
}

type PollingUpdate = (documentId: string, response: RagDocumentImageEnrichmentResponse) => void

export function createKnowledgeUploadImageEnrichmentPoller(
  getDocuments: () => readonly PollingDocument[],
  onUpdate: PollingUpdate,
  onCompleted: () => void,
  intervalMilliseconds = 3000
) {
  let timer: number | null = null
  let requestInFlight = false
  let generation = 0

  const hasActiveDocuments = () =>
    getDocuments().some((item) => Boolean(item.documentId) && isActiveStatus(item.imageEnrichmentStatus))

  const stop = () => {
    generation += 1
    if (timer === null) return
    window.clearInterval(timer)
    timer = null
  }

  const poll = async () => {
    if (requestInFlight) return
    const activeDocuments = getDocuments().filter(
      (item): item is PollingDocument & { documentId: string } =>
        Boolean(item.documentId) && isActiveStatus(item.imageEnrichmentStatus)
    )
    if (!activeDocuments.length) {
      stop()
      return
    }

    requestInFlight = true
    const currentGeneration = generation
    try {
      const results = await Promise.allSettled(
        activeDocuments.map((item) => getRagDocumentImageEnrichment(item.documentId))
      )
      if (currentGeneration !== generation) return
      results.forEach((result, index) => {
        const document = activeDocuments[index]
        if (!document || result.status !== 'fulfilled') return
        onUpdate(document.documentId, result.value)
      })
      if (!hasActiveDocuments()) {
        onCompleted()
        stop()
      }
    } finally {
      requestInFlight = false
    }
  }

  const start = () => {
    if (timer !== null) return
    timer = window.setInterval(() => void poll(), intervalMilliseconds)
    void poll()
  }

  return { start, stop }
}

function isActiveStatus(status?: string): boolean {
  return status === 'queued' || status === 'processing'
}
