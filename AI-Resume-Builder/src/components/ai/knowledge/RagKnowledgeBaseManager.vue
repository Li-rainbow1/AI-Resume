<script setup lang="ts">
import { ref } from 'vue'
import { ChevronDown, Database, Pencil, Plus, Trash2 } from 'lucide-vue-next'
import { deleteRagKnowledgeBase, saveRagKnowledgeBase, type RagKnowledgeBase } from '@/api/ragScopeApi'

defineProps<{ knowledgeBases: RagKnowledgeBase[] }>()
const emit = defineEmits<{ changed: [] }>()
const editing = ref(false)
const busy = ref(false)
const error = ref('')
const knowledgeBaseId = ref('')
const name = ref('')
const aliases = ref('')

function edit(knowledgeBase?: RagKnowledgeBase) {
  knowledgeBaseId.value = knowledgeBase?.knowledgeBaseId || ''
  name.value = knowledgeBase?.name || ''
  aliases.value = knowledgeBase?.aliases.join('，') || ''
  error.value = ''
  editing.value = true
}

async function save() {
  busy.value = true
  error.value = ''
  try {
    await saveRagKnowledgeBase(
      name.value,
      aliases.value.split(/[,，\n]/).map((value) => value.trim()).filter(Boolean),
      knowledgeBaseId.value || undefined
    )
    editing.value = false
    emit('changed')
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '知识库保存失败'
  } finally {
    busy.value = false
  }
}

async function remove(knowledgeBase: RagKnowledgeBase) {
  if (busy.value) return
  if (!window.confirm(`确定删除知识库“${knowledgeBase.name}”吗？仅空知识库可以删除。`)) return
  busy.value = true
  error.value = ''
  try {
    await deleteRagKnowledgeBase(knowledgeBase.knowledgeBaseId)
    emit('changed')
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '知识库删除失败'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <details class="knowledge-base-manager">
    <summary class="knowledge-base-manager-summary">
      <span class="knowledge-base-manager-heading">
        <span class="knowledge-base-manager-icon" aria-hidden="true">
          <Database :size="17" stroke-width="1.9" />
        </span>
        <strong>知识库管理</strong>
      </span>
      <ChevronDown class="knowledge-base-manager-chevron" :size="17" stroke-width="1.9" aria-hidden="true" />
    </summary>

    <div class="knowledge-base-manager-body">
      <div class="knowledge-base-list">
        <button class="knowledge-base-create" type="button" :disabled="busy" @click="edit()">
          <Plus :size="16" stroke-width="2" aria-hidden="true" />
          <span>新建知识库</span>
        </button>
        <span v-for="knowledgeBase in knowledgeBases" :key="knowledgeBase.knowledgeBaseId" class="knowledge-base-entry">
          <button class="knowledge-base-entry-name" type="button" :disabled="busy" @click="edit(knowledgeBase)">
            <span>{{ knowledgeBase.name }}</span>
            <Pencil :size="14" stroke-width="1.9" aria-hidden="true" />
          </button>
          <button
            type="button"
            class="knowledge-base-entry-delete"
            :disabled="busy"
            :aria-label="`删除 ${knowledgeBase.name}`"
            @click="remove(knowledgeBase)"
          >
            <Trash2 :size="14" stroke-width="1.9" aria-hidden="true" />
          </button>
        </span>
      </div>

      <form v-if="editing" class="knowledge-base-form" @submit.prevent="save">
        <div class="knowledge-base-form-title">{{ knowledgeBaseId ? '编辑知识库' : '新建知识库' }}</div>
        <div class="knowledge-base-form-fields">
          <label>
            <span>知识库名称</span>
            <input v-model="name" maxlength="128" required :disabled="busy" />
          </label>
          <label>
            <span>别名</span>
            <input v-model="aliases" placeholder="多个别名用逗号分隔" :disabled="busy" />
          </label>
        </div>
        <div class="knowledge-base-form-actions">
          <button class="knowledge-base-form-cancel" type="button" :disabled="busy" @click="editing = false">取消</button>
          <button class="knowledge-base-form-save" type="submit" :disabled="busy">{{ busy ? '保存中' : '保存' }}</button>
        </div>
        <p v-if="error" class="knowledge-base-error" role="alert">{{ error }}</p>
      </form>
    </div>
  </details>
</template>
<style scoped>
.knowledge-base-manager {
  overflow: hidden;
  margin: 0;
  border: 1px solid var(--border-color);
  border-radius: 16px;
  background: var(--surface-soft);
  color: var(--text-primary);
}

.knowledge-base-manager-summary {
  display: flex;
  min-height: 56px;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 9px 14px;
  color: var(--text-primary);
  cursor: pointer;
  list-style: none;
  transition: background-color 0.18s ease;
}

.knowledge-base-manager-summary::-webkit-details-marker {
  display: none;
}

.knowledge-base-manager-summary:hover {
  background: var(--surface-base);
}

.knowledge-base-manager-heading {
  display: inline-flex;
  min-width: 0;
  align-items: center;
  gap: 10px;
}

.knowledge-base-manager-heading strong {
  font-size: 14px;
  font-weight: 700;
  line-height: 1.3;
}

.knowledge-base-manager-icon {
  display: grid;
  width: 32px;
  height: 32px;
  flex: 0 0 auto;
  place-items: center;
  border: 1px solid var(--primary-200);
  border-radius: 10px;
  background: var(--primary-50);
  color: var(--primary-500);
}

.knowledge-base-manager-chevron {
  flex: 0 0 auto;
  color: var(--text-tertiary);
  transition: transform 0.18s ease;
}

.knowledge-base-manager[open] .knowledge-base-manager-chevron {
  transform: rotate(180deg);
}

.knowledge-base-manager-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 12px 14px 14px;
  border-top: 1px solid var(--border-soft);
  background: var(--surface-base);
}

.knowledge-base-list {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.knowledge-base-create,
.knowledge-base-entry-name,
.knowledge-base-entry-delete,
.knowledge-base-form-actions button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 10px;
  cursor: pointer;
  font: inherit;
  transition: border-color 0.18s ease, background-color 0.18s ease, color 0.18s ease, transform 0.18s ease;
}

.knowledge-base-create {
  min-height: 34px;
  gap: 6px;
  padding: 0 11px;
  border: 1px solid var(--primary-500);
  background: var(--primary-500);
  color: var(--text-inverse);
  font-size: 12px;
  font-weight: 700;
}

.knowledge-base-create:hover:not(:disabled),
.knowledge-base-form-save:hover:not(:disabled) {
  transform: translateY(-1px);
}

.knowledge-base-entry {
  display: inline-flex;
  align-items: stretch;
  overflow: hidden;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: var(--surface-soft);
}

.knowledge-base-entry-name {
  min-height: 34px;
  min-width: 0;
  gap: 7px;
  padding: 0 10px;
  border: 0;
  background: transparent;
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 650;
}

.knowledge-base-entry-name span {
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.knowledge-base-entry-delete {
  width: 32px;
  min-height: 34px;
  border: 0;
  border-left: 1px solid var(--border-color);
  border-radius: 0;
  background: transparent;
  color: var(--text-tertiary);
}

.knowledge-base-entry:hover,
.knowledge-base-entry:focus-within {
  border-color: var(--primary-300);
}

.knowledge-base-entry-name:hover:not(:disabled),
.knowledge-base-entry-name:focus-visible {
  background: var(--primary-50);
  color: var(--primary-600);
}

.knowledge-base-entry-delete:hover:not(:disabled),
.knowledge-base-entry-delete:focus-visible {
  background: var(--accent-red-soft);
  color: var(--accent-red);
}

.knowledge-base-form {
  display: flex;
  flex-direction: column;
  gap: 11px;
  padding: 13px;
  border: 1px solid var(--border-color);
  border-radius: 13px;
  background: var(--surface-soft);
}

.knowledge-base-form-title {
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 700;
}

.knowledge-base-form-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.knowledge-base-form-fields label {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 5px;
  color: var(--text-tertiary);
  font-size: 11px;
  font-weight: 650;
}

.knowledge-base-form-fields input {
  width: 100%;
  min-height: 36px;
  padding: 0 10px;
  border: 1px solid var(--border-color);
  border-radius: 9px;
  outline: none;
  background: var(--surface-base);
  color: var(--text-primary);
  font: inherit;
  font-size: 12px;
}

.knowledge-base-form-fields input:focus {
  border-color: var(--primary-500);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--primary-500) 12%, transparent);
}

.knowledge-base-form-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.knowledge-base-form-actions button {
  min-height: 34px;
  padding: 0 12px;
  font-size: 12px;
  font-weight: 650;
}

.knowledge-base-form-cancel {
  border: 1px solid var(--border-color);
  background: var(--surface-base);
  color: var(--text-secondary);
}

.knowledge-base-form-save {
  border: 1px solid var(--primary-500);
  background: var(--primary-500);
  color: var(--text-inverse);
  box-shadow: var(--shadow-brand);
}

.knowledge-base-form-actions button:disabled,
.knowledge-base-create:disabled,
.knowledge-base-entry-name:disabled,
.knowledge-base-entry-delete:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.knowledge-base-error {
  margin: 0;
  color: var(--accent-red);
  font-size: 12px;
  line-height: 1.5;
}

.knowledge-base-manager-summary:focus-visible,
.knowledge-base-create:focus-visible,
.knowledge-base-entry-name:focus-visible,
.knowledge-base-entry-delete:focus-visible,
.knowledge-base-form-actions button:focus-visible {
  outline: 2px solid var(--primary-500);
  outline-offset: 3px;
}

@media (max-width: 560px) {
  .knowledge-base-form-fields {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
