<script setup lang="ts">
import { useResumeStore } from '@/stores/resume'
import { ref } from 'vue'
import RichEditor from '@/components/common/RichEditor.vue'

const store = useResumeStore()
const collapsed = ref(false)
</script>

<template>
  <section class="editor-section">
    <div class="section-header" @click="collapsed = !collapsed">
      <div class="section-toggle">
        <svg class="chevron" :class="{ rotated: !collapsed }" width="16" height="16" viewBox="0 0 16 16" fill="none">
          <path d="M6 4L10 8L6 12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        <h3>实习经历</h3>
      </div>
    </div>

    <div v-show="!collapsed" class="section-body">
      <div
        v-for="(work, index) in store.workList"
        :key="work.id"
        class="entry-card"
      >
        <div class="entry-header">
          <span class="entry-index">实习经历 {{ index + 1 }}</span>
          <button
            v-if="store.workList.length > 1"
            class="btn-remove"
            @click="store.removeWork(work.id)"
          >
            ✕
          </button>
        </div>

        <div class="form-grid">
          <div class="form-group">
            <label class="form-label">公司名称</label>
            <input v-model="work.company" type="text" class="form-input" placeholder="请输入公司名称" />
          </div>
          <div class="form-group">
            <label class="form-label">部门名称</label>
            <input v-model="work.department" type="text" class="form-input" placeholder="请输入部门名称" />
          </div>
          <div class="form-group span-2">
            <label class="form-label">项目名称</label>
            <input v-model="work.projectName" type="text" class="form-input" placeholder="例如：AI Agent 信用评分系统" />
          </div>
          <div class="form-group">
            <label class="form-label">职位</label>
            <input v-model="work.position" type="text" class="form-input" placeholder="请输入职位" />
          </div>
          <div class="form-group">
            <label class="form-label">开始时间</label>
            <input v-model="work.startDate" type="month" class="form-input" />
          </div>
          <div class="form-group">
            <label class="form-label">结束时间</label>
            <input v-model="work.endDate" type="month" class="form-input" />
          </div>
          <div class="form-group span-2">
            <label class="form-label">工作地点</label>
            <input v-model="work.location" type="text" class="form-input" placeholder="例如：深圳" />
          </div>
        </div>

        <div class="form-group form-group-full">
          <label class="form-label">工作描述</label>
          <RichEditor
            v-model="work.description"
            :rows="4"
            placeholder="描述工作职责、项目参与、技术贡献和成果..."
          />
        </div>
      </div>

      <button class="btn-add" @click="store.addWork()">
        <span class="btn-add-icon">+</span>
        添加实习经历
      </button>
    </div>
  </section>
</template>

<style scoped>
.editor-section {
  margin-bottom: var(--spacing-lg);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  background: white;
  overflow: hidden;
  transition: box-shadow var(--transition-base);
}

.editor-section:hover {
  box-shadow: var(--shadow-sm);
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--spacing-lg) var(--spacing-xl);
  cursor: pointer;
  user-select: none;
  transition: background var(--transition-fast);
}

.section-header:hover {
  background: var(--gray-50);
}

.section-toggle {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.section-toggle h3 {
  font-size: 0.95rem;
  font-weight: 600;
  color: var(--text-primary);
}

.chevron {
  color: var(--text-secondary);
  transition: transform var(--transition-base);
  transform: rotate(0deg);
}

.chevron.rotated {
  transform: rotate(90deg);
}

.section-body {
  padding: 0 var(--spacing-xl) var(--spacing-xl);
}

.entry-card {
  padding: var(--spacing-lg);
  margin-bottom: var(--spacing-md);
  background: var(--gray-50);
  border-radius: var(--radius-md);
  border: 1px solid var(--gray-100);
}

.entry-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--spacing-md);
}

.entry-index {
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--primary-600);
}

.btn-remove {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: var(--gray-200);
  color: var(--gray-500);
  border-radius: 50%;
  cursor: pointer;
  font-size: 0.7rem;
  transition: all var(--transition-fast);
}

.btn-remove:hover {
  background: var(--accent-red);
  color: white;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: var(--spacing-md) var(--spacing-lg);
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
}

.form-group-full {
  margin-top: var(--spacing-md);
}

.span-2 {
  grid-column: span 2;
}

.form-label {
  font-size: 0.78rem;
  font-weight: 500;
  color: var(--text-secondary);
}

.form-input {
  padding: var(--spacing-sm) var(--spacing-md);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  font-size: 0.88rem;
  color: var(--text-primary);
  background: white;
  transition: all var(--transition-fast);
  outline: none;
}

.form-input:focus {
  border-color: var(--primary-400);
  box-shadow: 0 0 0 3px var(--primary-50);
}

.form-input::placeholder {
  color: var(--gray-400);
}

.form-textarea {
  width: 100%;
  padding: var(--spacing-sm) var(--spacing-md);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  font-size: 0.88rem;
  color: var(--text-primary);
  background: white;
  transition: all var(--transition-fast);
  outline: none;
  resize: vertical;
  line-height: 1.6;
}

.form-textarea:focus {
  border-color: var(--primary-400);
  box-shadow: 0 0 0 3px var(--primary-50);
}

.form-textarea::placeholder {
  color: var(--gray-400);
}

.btn-add {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--spacing-sm);
  width: 100%;
  padding: var(--spacing-md);
  border: 2px dashed var(--primary-200);
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--primary-600);
  font-size: 0.88rem;
  font-weight: 500;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.btn-add:hover {
  background: var(--primary-50);
  border-color: var(--primary-400);
}

.btn-add-icon {
  font-size: 1.1rem;
  font-weight: 700;
}
</style>
