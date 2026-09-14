<script setup lang="ts">
import { computed } from 'vue'
import { toHref } from '../../shared/metaIcons'
import { useResumeTemplateData } from '../../shared/useResumeTemplateData'

const { store, hasAnyContent, moduleOrderStyle, formatMonth, formatDatePeriod } = useResumeTemplateData()

const contactItems = computed(() => {
  const items = [
    { label: '电话', value: store.basicInfo.phone.trim() },
    { label: '邮箱', value: store.basicInfo.email.trim() },
    { label: '微信', value: store.basicInfo.wechat.trim() },
    { label: '现居', value: store.basicInfo.currentCity.trim() },
  ]

  return items.filter((item) => item.value)
})

const baseItems = computed(() => {
  const items = [
    { label: '性别', value: store.basicInfo.gender.trim() },
    { label: '年龄', value: store.basicInfo.age.trim() },
    { label: '工作年限', value: store.basicInfo.workYears.trim() },
    { label: '学历', value: store.basicInfo.educationLevel.trim() },
    { label: '所在地', value: store.basicInfo.location.trim() },
  ]

  return items.filter((item) => item.value)
})

const intentionItems = computed(() => {
  const items = [
    { label: '目标岗位', value: store.basicInfo.jobTitle.trim() },
    { label: '当前状态', value: store.basicInfo.currentStatus.trim() },
    { label: '期望城市', value: store.basicInfo.expectedLocation.trim() },
    { label: '期望薪资', value: store.basicInfo.expectedSalary.trim() },
  ]

  return items.filter((item) => item.value)
})

const linkItems = computed(() =>
  [
    { label: '个人网站', value: store.basicInfo.website.trim() },
    { label: 'GitHub', value: store.basicInfo.github.trim() },
    { label: '博客', value: store.basicInfo.blog.trim() },
  ]
    .filter((item) => item.value)
    .map((item, index) => ({
      key: `${item.label}-${index}`,
      label: item.label,
      text: item.value,
      href: toHref(item.value),
    }))
)

function subLine(values: Array<string | undefined>): string {
  return values
    .map((value) => value?.trim() ?? '')
    .filter(Boolean)
    .join(' · ')
}
</script>

<template>
  <div class="resume-template-blue-split-pro">
    <aside class="left-panel">
      <div class="identity">
        <div class="avatar-wrap">
          <img v-if="store.basicInfo.avatar" :src="store.basicInfo.avatar" alt="头像" />
          <div v-else class="avatar-placeholder">头像</div>
        </div>
        <h1 v-if="store.basicInfo.name" class="name">{{ store.basicInfo.name }}</h1>
        <p v-if="store.basicInfo.jobTitle" class="job">{{ store.basicInfo.jobTitle }}</p>
      </div>

      <section v-if="contactItems.length" class="side-block">
        <h3>联系方式</h3>
        <p v-for="item in contactItems" :key="item.label"><span>{{ item.label }}</span>{{ item.value }}</p>
      </section>

      <section v-if="baseItems.length" class="side-block">
        <h3>基础信息</h3>
        <p v-for="item in baseItems" :key="item.label"><span>{{ item.label }}</span>{{ item.value }}</p>
      </section>

      <section v-if="intentionItems.length" class="side-block">
        <h3>求职意向</h3>
        <p v-for="item in intentionItems" :key="item.label"><span>{{ item.label }}</span>{{ item.value }}</p>
      </section>

      <section v-if="linkItems.length" class="side-block">
        <h3>个人链接</h3>
        <a v-for="item in linkItems" :key="item.key" :href="item.href" target="_blank" rel="noopener noreferrer">
          <span>{{ item.label }}</span>{{ item.text }}
        </a>
      </section>
    </aside>

    <main class="right-panel">
      <section v-if="store.isModuleVisible('selfIntro') && store.selfIntro" class="resume-section" :style="moduleOrderStyle('selfIntro')">
        <h2 class="section-title">个人简介</h2>
        <div class="entry-rich" v-html="store.selfIntro"></div>
      </section>

      <section
        v-if="store.isModuleVisible('education') && store.educationList.some((e) => e.school)"
        class="resume-section"
        :style="moduleOrderStyle('education')"
      >
        <h2 class="section-title">教育经历</h2>
        <article v-for="edu in store.educationList" :key="edu.id" class="entry" v-show="edu.school">
          <div class="entry-head">
            <p class="entry-main"><strong>{{ edu.school }}</strong></p>
            <span class="entry-date">{{ formatDatePeriod(edu.startDate, edu.endDate) }}</span>
          </div>
          <p class="entry-subline">{{ subLine([edu.major, edu.degree, edu.college, edu.location, edu.gpa ? `GPA：${edu.gpa}` : '']) }}</p>
          <div v-if="edu.description" class="entry-rich" v-html="edu.description"></div>
        </article>
      </section>

      <section v-if="store.isModuleVisible('skills') && store.skills" class="resume-section" :style="moduleOrderStyle('skills')">
        <h2 class="section-title">专业技能</h2>
        <div class="entry-rich" v-html="store.skills"></div>
      </section>

      <section
        v-if="store.isModuleVisible('workExperience') && store.workList.some((w) => w.company)"
        class="resume-section"
        :style="moduleOrderStyle('workExperience')"
      >
        <h2 class="section-title">实习经历</h2>
        <article v-for="work in store.workList" :key="work.id" class="entry" v-show="work.company">
          <div class="entry-head">
            <p class="entry-main"><strong>{{ work.company }}</strong></p>
            <span class="entry-date">{{ formatDatePeriod(work.startDate, work.endDate) }}</span>
          </div>
          <p v-if="work.projectName" class="entry-subline entry-project-title">{{ work.projectName }}</p>
          <p class="entry-subline">{{ subLine([work.position, work.department, work.location]) }}</p>
          <div v-if="work.description" class="entry-rich" v-html="work.description"></div>
        </article>
      </section>

      <section
        v-if="store.isModuleVisible('projectExperience') && store.projectList.some((p) => p.name)"
        class="resume-section"
        :style="moduleOrderStyle('projectExperience')"
      >
        <h2 class="section-title">项目经历</h2>
        <article v-for="project in store.projectList" :key="project.id" class="entry" v-show="project.name">
          <div class="entry-head">
            <p class="entry-main"><strong>{{ project.name }}</strong></p>
            <span v-if="project.startDate || project.endDate" class="entry-date">{{ formatDatePeriod(project.startDate, project.endDate) }}</span>
            <a v-else-if="project.link" class="entry-date entry-link" :href="project.link" target="_blank" rel="noopener noreferrer">{{ project.link }}</a>
          </div>
          <p class="entry-subline">{{ subLine([project.role]) }}</p>
          <p v-if="project.link && (project.startDate || project.endDate)" class="entry-link-row">
            <a class="entry-link" :href="project.link" target="_blank" rel="noopener noreferrer">{{ project.link }}</a>
          </p>
          <div v-if="project.description" class="entry-rich" v-html="project.description"></div>
        </article>
      </section>

      <section
        v-if="store.isModuleVisible('awards') && store.awardList.some((a) => a.name)"
        class="resume-section"
        :style="moduleOrderStyle('awards')"
      >
        <h2 class="section-title">荣誉奖项</h2>
        <article v-for="award in store.awardList" :key="award.id" class="entry" v-show="award.name">
          <div class="entry-head">
            <p class="entry-main"><strong>{{ award.name }}</strong></p>
            <span class="entry-date">{{ formatMonth(award.date) }}</span>
          </div>
          <div v-if="award.description" class="entry-rich" v-html="award.description"></div>
        </article>
      </section>

      <div v-if="!hasAnyContent" class="empty">
        <p>在左侧填写信息，这里实时预览</p>
      </div>
    </main>
  </div>
</template>

<style scoped>
.resume-template-blue-split-pro {
  box-sizing: border-box;
  width: 100%;
  min-height: 100%;
  display: grid;
  grid-template-columns: 228px 1fr;
  background: #ffffff;
  color: #0f172a;
}

.left-panel {
  background:
    radial-gradient(circle at 100% 0, rgba(255, 255, 255, 0.16) 0 120px, transparent 121px),
    linear-gradient(180deg, #3f71d8 0%, #345fc0 56%, #2d54ab 100%);
  color: #e8f1ff;
  padding: 22px 16px 18px;
}

.identity {
  text-align: center;
  padding-bottom: 12px;
  border-bottom: 1px solid rgba(226, 237, 255, 0.24);
}

.avatar-wrap {
  width: 96px;
  height: 118px;
  border-radius: 8px;
  overflow: hidden;
  margin: 0 auto 10px;
  border: 2px solid rgba(226, 237, 255, 0.42);
  background: rgba(255, 255, 255, 0.15);
}

.avatar-wrap img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.avatar-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: rgba(232, 241, 255, 0.82);
}

.name {
  margin: 0;
  font-size: 26px;
  line-height: 1.14;
  font-weight: 700;
  color: #f8fbff;
}

.job {
  margin: 6px 0 0;
  font-size: 13px;
  color: rgba(224, 236, 255, 0.92);
}

.side-block {
  margin-top: 14px;
}

.side-block h3 {
  margin: 0 0 7px;
  font-size: 14px;
  font-weight: 700;
  color: #f8fbff;
  letter-spacing: 0.2px;
}

.side-block p,
.side-block a {
  margin: 0 0 5px;
  display: block;
  color: rgba(232, 241, 255, 0.95);
  font-size: 12px;
  line-height: 1.45;
  text-decoration: none;
  word-break: break-all;
}

.side-block span {
  margin-right: 6px;
  color: rgba(213, 228, 255, 0.95);
}

.side-block a:hover {
  text-decoration: underline;
}

.right-panel {
  min-width: 0;
  padding: 18px 24px 14px;
  display: flex;
  flex-direction: column;
  background:
    linear-gradient(180deg, rgba(59, 130, 246, 0.05), rgba(59, 130, 246, 0) 120px),
    #ffffff;
}

.resume-section {
  margin-bottom: 10px;
}

.resume-section:last-of-type {
  margin-bottom: 0;
}

.section-title {
  margin: 0 0 6px;
  font-size: 16px;
  font-weight: 700;
  color: #2f63c4;
  line-height: 1.2;
  display: flex;
  align-items: center;
  gap: 10px;
}

.section-title::after {
  content: '';
  flex: 1;
  min-width: 120px;
  border-top: 1px solid #d0d7e2;
  transform: translateY(1px);
  opacity: 1;
}

.entry {
  margin-bottom: 9px;
}

.entry:last-child {
  margin-bottom: 0;
}

.entry-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 16px;
}

.entry-main {
  margin: 0;
  color: #0f172a;
  font-size: 16px;
}

.entry-main strong {
  font-size: 17px;
}

.entry-date {
  color: #64748b;
  font-size: 14px;
  white-space: nowrap;
}

.entry-subline {
  margin: 2px 0 0;
  color: #64748b;
  font-size: 14px;
}

.entry-project-title {
  color: #1f2937;
  font-weight: 500;
}

.entry-link-row {
  margin: 4px 0 2px;
}

.entry-link {
  color: #2563eb;
  text-decoration: none;
  font-size: 13px;
}

.entry-link:hover {
  text-decoration: underline;
}

.block-title {
  margin: 7px 0 2px;
  color: #0f172a;
  font-size: 14px;
  font-weight: 700;
}

.entry-rich {
  margin-top: 2px;
  color: #0f172a;
  font-size: 13px;
  line-height: 1.8;
}

.empty {
  margin-top: 40px;
  text-align: center;
  color: #94a3b8;
  font-size: 12px;
  order: 999;
}

:deep(.entry-rich ul) {
  margin: 0;
  padding-left: 1.2em;
  list-style: disc;
}

:deep(.entry-rich ul li) {
  margin: 3px 0;
}

:deep(.entry-rich ol) {
  margin: 0;
  padding-left: 1.25em;
  list-style: decimal;
  list-style-position: outside;
}

:deep(.entry-rich ol li) {
  margin: 3px 0;
}

:deep(.entry-rich li > p) {
  margin: 0;
}

:deep(.entry-rich p) {
  margin: 3px 0;
}
</style>
