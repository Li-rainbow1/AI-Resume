<script setup lang="ts">
import type { RagKnowledgeBase } from '@/api/ragScopeApi'

const model = defineModel<string>({ required: true })
defineProps<{ knowledgeBases: RagKnowledgeBase[]; disabled?: boolean; label?: string }>()
</script>

<template>
  <label class="scope-select">{{ label || '所属知识库' }}
    <select v-model="model" :disabled="disabled">
      <option value="unclassified">未分类</option>
      <option v-for="knowledgeBase in knowledgeBases" :key="knowledgeBase.knowledgeBaseId" :value="knowledgeBase.knowledgeBaseId">
        {{ knowledgeBase.name }}
      </option>
    </select>
  </label>
</template>
<style scoped>
.scope-select{display:flex;align-items:center;gap:10px;font-size:13px;color:var(--text-secondary,#64748b)}
select{max-width:240px;padding:7px 10px;border:1px solid var(--border-color,#e2e8f0);border-radius:8px;background:var(--bg-primary,#fff);color:inherit}
</style>
