<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Check, Database, X } from 'lucide-vue-next'
import type { RagKnowledgeBase } from '@/api/ragScopeApi'

const props = defineProps<{
  open: boolean
  modelValue: string
  knowledgeBases: RagKnowledgeBase[]
}>()

const emit = defineEmits<{
  (event: 'close'): void
  (event: 'confirm', knowledgeBaseId: string): void
}>()

const dialogRef = ref<HTMLElement | null>(null)
const selectedKnowledgeBaseId = ref('unclassified')
let previousActiveElement: HTMLElement | null = null
let previousBodyOverflow = ''
let isScrollLocked = false

const options = computed(() => [
  { knowledgeBaseId: 'unclassified', name: '未分类', aliases: [] },
  ...props.knowledgeBases,
])

function initializeSelection() {
  const currentId = props.modelValue || 'unclassified'
  selectedKnowledgeBaseId.value = options.value.some((item) => item.knowledgeBaseId === currentId)
    ? currentId
    : 'unclassified'
}

function restorePageScroll() {
  if (!isScrollLocked) return
  document.body.style.overflow = previousBodyOverflow
  isScrollLocked = false
}

function closeDialog() {
  emit('close')
}

function confirmSelection() {
  emit('confirm', selectedKnowledgeBaseId.value)
}

function handleWindowKeydown(event: KeyboardEvent) {
  if (!props.open) return

  if (event.key === 'Escape') {
    event.preventDefault()
    closeDialog()
    return
  }
  if (event.key !== 'Tab' || !dialogRef.value) return

  const focusableElements = Array.from(
    dialogRef.value.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  )
  if (focusableElements.length === 0) return

  const firstElement = focusableElements[0]
  const lastElement = focusableElements[focusableElements.length - 1]
  if (event.shiftKey && document.activeElement === firstElement) {
    event.preventDefault()
    lastElement?.focus()
  } else if (!event.shiftKey && document.activeElement === lastElement) {
    event.preventDefault()
    firstElement?.focus()
  }
}

watch(
  () => props.open,
  async (open) => {
    if (open) {
      initializeSelection()
      previousActiveElement = document.activeElement instanceof HTMLElement ? document.activeElement : null
      previousBodyOverflow = document.body.style.overflow
      document.body.style.overflow = 'hidden'
      isScrollLocked = true
      await nextTick()
      dialogRef.value?.focus()
      return
    }

    restorePageScroll()
    if (previousActiveElement?.isConnected) previousActiveElement.focus()
    previousActiveElement = null
  },
)

onMounted(() => window.addEventListener('keydown', handleWindowKeydown))

onBeforeUnmount(() => {
  restorePageScroll()
  window.removeEventListener('keydown', handleWindowKeydown)
})
</script>

<template>
  <Teleport to="body">
    <Transition name="knowledge-base-picker">
      <div v-if="open" class="knowledge-base-picker-layer" @click.self="closeDialog">
        <section
          ref="dialogRef"
          class="knowledge-base-picker-dialog"
          role="dialog"
          aria-modal="true"
          aria-labelledby="knowledge-base-picker-title"
          tabindex="-1"
        >
          <header class="knowledge-base-picker-header">
            <div class="knowledge-base-picker-heading">
              <span class="knowledge-base-picker-icon" aria-hidden="true">
                <Database :size="20" stroke-width="1.9" />
              </span>
              <h2 id="knowledge-base-picker-title">选择知识库</h2>
            </div>
            <button class="knowledge-base-picker-close" type="button" aria-label="关闭知识库选择" @click="closeDialog">
              <X :size="19" stroke-width="1.9" aria-hidden="true" />
            </button>
          </header>

          <div class="knowledge-base-picker-body">
            <fieldset class="knowledge-base-options">
              <legend class="sr-only">知识库选项</legend>
              <label
                v-for="option in options"
                :key="option.knowledgeBaseId"
                class="knowledge-base-option"
                :class="{ 'is-selected': selectedKnowledgeBaseId === option.knowledgeBaseId }"
              >
                <input v-model="selectedKnowledgeBaseId" type="radio" name="knowledge-base" :value="option.knowledgeBaseId" />
                <span class="knowledge-base-option-mark" aria-hidden="true">
                  <Check :size="15" stroke-width="2.2" />
                </span>
                <span class="knowledge-base-option-copy">
                  <strong>{{ option.name }}</strong>
                  <small v-if="option.aliases.length">{{ option.aliases.join('、') }}</small>
                </span>
              </label>
            </fieldset>
          </div>

          <footer class="knowledge-base-picker-actions">
            <button class="knowledge-base-picker-secondary" type="button" @click="closeDialog">取消</button>
            <button class="knowledge-base-picker-primary" type="button" @click="confirmSelection">确定</button>
          </footer>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.knowledge-base-picker-layer {
  position: fixed;
  inset: 0;
  z-index: 2100;
  display: grid;
  place-items: center;
  padding: 20px;
  background: var(--overlay-backdrop, rgba(2, 6, 23, 0.28));
  backdrop-filter: blur(8px);
}

.knowledge-base-picker-dialog {
  display: flex;
  width: min(430px, 100%);
  max-height: min(620px, calc(100dvh - 40px));
  overflow: hidden;
  flex-direction: column;
  border: 1px solid var(--border-color);
  border-radius: 22px;
  outline: none;
  background: var(--surface-raised);
  color: var(--text-primary);
  box-shadow: var(--shadow-dialog);
}

.knowledge-base-picker-header {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 20px 22px;
  border-bottom: 1px solid var(--border-soft);
}

.knowledge-base-picker-heading {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 11px;
}

.knowledge-base-picker-heading h2 {
  margin: 0;
  color: var(--text-primary);
  font-size: 18px;
  font-weight: 700;
  line-height: 1.25;
}

.knowledge-base-picker-icon {
  display: grid;
  width: 38px;
  height: 38px;
  flex: 0 0 auto;
  place-items: center;
  border: 1px solid var(--primary-200);
  border-radius: 12px;
  background: var(--primary-50);
  color: var(--primary-500);
}

.knowledge-base-picker-close {
  display: grid;
  width: 34px;
  height: 34px;
  flex: 0 0 auto;
  place-items: center;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: var(--surface-base);
  color: var(--text-secondary);
  cursor: pointer;
  transition: border-color 0.18s ease, background-color 0.18s ease, color 0.18s ease;
}

.knowledge-base-picker-close:hover,
.knowledge-base-picker-close:focus-visible {
  border-color: var(--primary-500);
  background: var(--primary-50);
  color: var(--primary-500);
}

.knowledge-base-picker-body {
  min-height: 0;
  overflow-y: auto;
  padding: 16px 22px;
}

.knowledge-base-options {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  border: 0;
}

.knowledge-base-option {
  display: flex;
  min-height: 58px;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid var(--border-color);
  border-radius: 14px;
  background: var(--surface-base);
  color: var(--text-primary);
  cursor: pointer;
  transition: border-color 0.18s ease, background-color 0.18s ease, box-shadow 0.18s ease;
}

.knowledge-base-option:hover {
  border-color: var(--primary-300);
  background: var(--primary-50);
}

.knowledge-base-option.is-selected {
  border-color: var(--primary-500);
  background: var(--primary-50);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--primary-500) 12%, transparent);
}

.knowledge-base-option input {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  opacity: 0;
}

.knowledge-base-option-mark {
  display: grid;
  width: 22px;
  height: 22px;
  flex: 0 0 auto;
  place-items: center;
  border: 1px solid var(--border-strong);
  border-radius: 50%;
  color: transparent;
}

.knowledge-base-option.is-selected .knowledge-base-option-mark {
  border-color: var(--primary-500);
  background: var(--primary-500);
  color: var(--text-inverse);
}

.knowledge-base-option-copy {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 3px;
}

.knowledge-base-option-copy strong {
  overflow-wrap: anywhere;
  font-size: 14px;
  font-weight: 650;
  line-height: 1.35;
}

.knowledge-base-option-copy small {
  overflow-wrap: anywhere;
  color: var(--text-tertiary);
  font-size: 11px;
  line-height: 1.35;
}

.knowledge-base-picker-actions {
  display: grid;
  grid-template-columns: minmax(0, 0.8fr) minmax(0, 1.2fr);
  gap: 8px;
  padding: 16px 22px 20px;
  border-top: 1px solid var(--border-soft);
}

.knowledge-base-picker-secondary,
.knowledge-base-picker-primary {
  min-height: 40px;
  border-radius: 11px;
  font-size: 13px;
  font-weight: 650;
  cursor: pointer;
  transition: transform 0.18s ease, border-color 0.18s ease, background-color 0.18s ease, box-shadow 0.18s ease;
}

.knowledge-base-picker-secondary {
  border: 1px solid var(--border-color);
  background: var(--surface-base);
  color: var(--text-secondary);
}

.knowledge-base-picker-primary {
  border: 1px solid var(--primary-500);
  background: var(--primary-500);
  color: var(--text-inverse);
  box-shadow: var(--shadow-brand);
}

.knowledge-base-picker-secondary:hover,
.knowledge-base-picker-secondary:focus-visible {
  border-color: var(--primary-500);
  color: var(--primary-500);
}

.knowledge-base-picker-primary:hover,
.knowledge-base-picker-primary:focus-visible {
  transform: translateY(-1px);
}

.knowledge-base-picker-close:focus-visible,
.knowledge-base-option:focus-within,
.knowledge-base-picker-secondary:focus-visible,
.knowledge-base-picker-primary:focus-visible {
  outline: 2px solid var(--primary-500);
  outline-offset: 3px;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.knowledge-base-picker-enter-active,
.knowledge-base-picker-leave-active {
  transition: opacity 0.18s ease;
}

.knowledge-base-picker-enter-active .knowledge-base-picker-dialog,
.knowledge-base-picker-leave-active .knowledge-base-picker-dialog {
  transition: opacity 0.18s ease, transform 0.2s ease;
}

.knowledge-base-picker-enter-from,
.knowledge-base-picker-leave-to {
  opacity: 0;
}

.knowledge-base-picker-enter-from .knowledge-base-picker-dialog,
.knowledge-base-picker-leave-to .knowledge-base-picker-dialog {
  opacity: 0;
  transform: translateY(8px) scale(0.98);
}

@media (prefers-reduced-motion: reduce) {
  .knowledge-base-picker-layer,
  .knowledge-base-picker-dialog,
  .knowledge-base-picker-close,
  .knowledge-base-option,
  .knowledge-base-picker-secondary,
  .knowledge-base-picker-primary {
    transition: none;
  }
}
</style>
