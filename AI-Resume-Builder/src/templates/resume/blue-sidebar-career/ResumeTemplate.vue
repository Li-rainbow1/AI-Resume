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

const linkItems = computed(() => {
  const items = [
    { label: '个人网站', value: store.basicInfo.website.trim() },
    { label: 'GitHub', value: store.basicInfo.github.trim() },
    { label: '博客', value: store.basicInfo.blog.trim() },
  ]

  return items
    .filter((item) => item.value)
    .map((item, idx) => ({
      key: `${item.label}-${idx}`,
      label: item.label,
      text: item.value,
      href: toHref(item.value),
    }))
})

function subLine(values: Array<string | undefined>): string {
  return values
    .map((value) => value?.trim() ?? '')
    .filter(Boolean)
    .join(' · ')
}
</script>

<template>
  <div class="resume-template-blue-sidebar">
    <aside class="sidebar">
      <div class="identity">
        <div class="avatar-wrap">
          <img v-if="store.basicInfo.avatar" :src="store.basicInfo.avatar" alt="头像" />
          <div v-else class="avatar-placeholder">头像</div>
        </div>
        <h1 v-if="store.basicInfo.name" class="sidebar-name">{{ store.basicInfo.name }}</h1>
        <p v-if="store.basicInfo.jobTitle" class="sidebar-job">{{ store.basicInfo.jobTitle }}</p>
      </div>

      <section class="side-block" v-if="contactItems.length">
        <h3>联系方式</h3>
        <p v-for="item in contactItems" :key="item.label"><span>{{ item.label }}</span>{{ item.value }}</p>
      </section>

      <section class="side-block" v-if="baseItems.length">
        <h3>基础信息</h3>
        <p v-for="item in baseItems" :key="item.label"><span>{{ item.label }}</span>{{ item.value }}</p>
      </section>

      <section class="side-block" v-if="intentionItems.length">
        <h3>求职意向</h3>
        <p v-for="item in intentionItems" :key="item.label"><span>{{ item.label }}</span>{{ item.value }}</p>
      </section>

      <section class="side-block" v-if="linkItems.length">
        <h3>个人链接</h3>
        <a v-for="item in linkItems" :key="item.key" :href="item.href" target="_blank" rel="noopener noreferrer">
          <span>{{ item.label }}</span>{{ item.text }}
        </a>
      </section>
    </aside>

    <main class="content">
      <section v-if="store.isModuleVisible('selfIntro') && store.selfIntro" class="resume-section" :style="moduleOrderStyle('selfIntro')">
        <h2 class="section-title"><i></i>个人总结</h2>
        <div class="entry-rich" v-html="store.selfIntro"></div>
      </section>

      <section
        v-if="store.isModuleVisible('workExperience') && store.workList.some((w) => w.company)"
        class="resume-section"
        :style="moduleOrderStyle('workExperience')"
      >
        <h2 class="section-title"><i></i>实习经历</h2>
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
        <h2 class="section-title"><i></i>项目经历</h2>
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
        v-if="store.isModuleVisible('education') && store.educationList.some((e) => e.school)"
        class="resume-section"
        :style="moduleOrderStyle('education')"
      >
        <h2 class="section-title"><i></i>教育经历</h2>
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
        <h2 class="section-title"><i></i>专业技能</h2>
        <div class="entry-rich" v-html="store.skills"></div>
      </section>

      <section
        v-if="store.isModuleVisible('awards') && store.awardList.some((a) => a.name)"
        class="resume-section"
        :style="moduleOrderStyle('awards')"
      >
        <h2 class="section-title"><i></i>荣誉奖项</h2>
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
.resume-template-blue-sidebar {
  box-sizing: border-box;
  width: 100%;
  min-height: 100%;
  display: flex;
  background: #eef4ff;
  color: #172033;
}

.sidebar {
  width: 218px;
  flex: 0 0 218px;
  background: linear-gradient(180deg, #1f73d9 0%, #1a5fb8 100%);
  color: #f8fbff;
  padding: 26px 18px;
}

.identity {
  text-align: center;
  padding-bottom: 12px;
  border-bottom: 1px solid rgba(226, 237, 255, 0.24);
}

.avatar-wrap {
  width: 96px;
  height: 118px;
  margin: 0 auto 10px;
  border: 2px solid rgba(255, 255, 255, 0.35);
  border-radius: 8px;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.18);
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
  color: rgba(255, 255, 255, 0.75);
}

.sidebar-name {
  margin: 0;
  text-align: center;
  font-size: 26px;
  line-height: 1.14;
  font-weight: 700;
  letter-spacing: 0.2px;
}

.sidebar-job {
  margin: 6px 0 0;
  text-align: center;
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
  color: #ffffff;
  line-height: 1.2;
  letter-spacing: 0.2px;
}

.side-block p,
.side-block a {
  margin: 0 0 5px;
  display: block;
  color: rgba(244, 250, 255, 0.95);
  font-size: 12px;
  line-height: 1.45;
  text-decoration: none;
  word-break: break-all;
}

.side-block p span {
  margin-right: 6px;
  color: rgba(222, 237, 255, 0.95);
}

.side-block a:hover {
  text-decoration: underline;
}

.content {
  position: relative;
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  padding: 20px 18px 16px;
  background:
    radial-gradient(circle at 95% 18%, rgba(47, 111, 219, 0.06) 0, rgba(47, 111, 219, 0.06) 180px, transparent 181px),
    repeating-radial-gradient(circle at 95% 18%, rgba(47, 111, 219, 0.05) 0 1px, transparent 1px 13px),
    #f7faff;
}

.resume-section {
  margin-bottom: 10px;
}

.resume-section:last-of-type {
  margin-bottom: 0;
}

.section-title {
  margin: 2px 0 8px;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 16px;
  color: #1f73d9;
  font-weight: 700;
  line-height: 1.2;
  border-bottom: 1px solid #bcd5f8;
  padding-bottom: 6px;
}

.section-title i {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #1f73d9;
  position: relative;
  flex-shrink: 0;
}

.section-title i::before {
  content: '';
  position: absolute;
  left: 50%;
  top: 50%;
  width: 6px;
  height: 6px;
  transform: translate(-50%, -50%);
  border-radius: 50%;
  background: #f8fbff;
}

.entry {
  margin-bottom: 10px;
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
  color: #162338;
  font-size: 16px;
}

.entry-main strong {
  font-size: 17px;
}

.entry-date {
  color: #355788;
  font-size: 14px;
  white-space: nowrap;
}

.entry-subline {
  margin: 2px 0 0;
  color: #3a5a84;
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
  color: #1f73d9;
  text-decoration: none;
  font-size: 13px;
}

.entry-link:hover {
  text-decoration: underline;
}

.entry-rich {
  margin-top: 3px;
  color: #162338;
  font-size: 14px;
  line-height: 1.8;
}

.empty {
  margin-top: 40px;
  text-align: center;
  color: #7d95b8;
  font-size: 12px;
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
