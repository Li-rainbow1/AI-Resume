<script setup lang="ts">
import { computed } from 'vue'
import { toHref } from '../../shared/metaIcons'
import { useResumeTemplateData } from '../../shared/useResumeTemplateData'

const { store, hasAnyContent, simpleContactMeta, moduleOrderStyle, formatMonth, formatDatePeriod } = useResumeTemplateData()

const contactText = computed(() => {
  const items = simpleContactMeta.value.map((item) => item.text.trim()).filter(Boolean)
  return items.join(' | ')
})

const baseMetaLineOne = computed(() =>
  [store.basicInfo.age, store.basicInfo.gender, store.basicInfo.workYears, store.basicInfo.educationLevel]
    .map((item) => item.trim())
    .filter(Boolean)
    .join(' | ')
)

const baseMetaLineTwo = computed(() =>
  [store.basicInfo.currentStatus, store.basicInfo.jobTitle, store.basicInfo.expectedLocation, store.basicInfo.expectedSalary]
    .map((item) => item.trim())
    .filter(Boolean)
    .join(' | ')
)

const baseMetaLineThree = computed(() =>
  [store.basicInfo.wechat, store.basicInfo.currentCity]
    .map((item) => item.trim())
    .filter(Boolean)
    .join(' | ')
)

const extraLinks = computed(() => {
  return [
    { key: 'website', text: store.basicInfo.website.trim() },
    { key: 'github', text: store.basicInfo.github.trim() },
    { key: 'blog', text: store.basicInfo.blog.trim() },
  ]
    .filter((item) => item.text)
    .map((item, index) => ({
      key: `${item.key}-${index}`,
      text: item.text,
      href: toHref(item.text),
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
  <div class="resume-template-workplace">
    <div class="hero-bg">
      <div class="avatar-wrap">
        <img v-if="store.basicInfo.avatar" :src="store.basicInfo.avatar" alt="头像" />
        <div v-else class="avatar-placeholder">头像</div>
      </div>
    </div>

    <header v-if="store.isModuleVisible('basicInfo')" class="header-main">
      <h1 v-if="store.basicInfo.name" class="name">{{ store.basicInfo.name }}</h1>
      <p v-if="contactText" class="contact-line">{{ contactText }}</p>
      <p v-if="baseMetaLineOne" class="meta-line">{{ baseMetaLineOne }}</p>
      <p v-if="baseMetaLineTwo" class="meta-line">{{ baseMetaLineTwo }}</p>
      <p v-if="baseMetaLineThree" class="meta-line">{{ baseMetaLineThree }}</p>
      <div v-if="extraLinks.length" class="extra-links">
        <a v-for="item in extraLinks" :key="item.key" :href="item.href" target="_blank" rel="noopener noreferrer">{{ item.text }}</a>
      </div>
    </header>

    <section v-if="store.isModuleVisible('selfIntro') && store.selfIntro" class="resume-section" :style="moduleOrderStyle('selfIntro')">
      <h2 class="section-title"><span>个人总结</span></h2>
      <div class="entry-rich" v-html="store.selfIntro"></div>
    </section>

    <section
      v-if="store.isModuleVisible('workExperience') && store.workList.some((w) => w.company)"
      class="resume-section"
      :style="moduleOrderStyle('workExperience')"
    >
      <h2 class="section-title"><span>实习经历</span></h2>
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
      <h2 class="section-title"><span>项目经历</span></h2>
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
      <h2 class="section-title"><span>教育经历</span></h2>
      <article v-for="edu in store.educationList" :key="edu.id" class="entry" v-show="edu.school">
        <div class="entry-head">
          <p class="entry-main"><strong>{{ edu.school }}</strong></p>
          <span class="entry-date">{{ formatDatePeriod(edu.startDate, edu.endDate) }}</span>
        </div>
        <p class="entry-subline">{{ subLine([edu.major, edu.degree, edu.location, edu.gpa ? `GPA：${edu.gpa}` : '']) }}</p>
        <div v-if="edu.description" class="entry-rich" v-html="edu.description"></div>
      </article>
    </section>

    <section v-if="store.isModuleVisible('skills') && store.skills" class="resume-section" :style="moduleOrderStyle('skills')">
      <h2 class="section-title"><span>专业技能</span></h2>
      <div class="entry-rich" v-html="store.skills"></div>
    </section>

    <section
      v-if="store.isModuleVisible('awards') && store.awardList.some((a) => a.name)"
      class="resume-section"
      :style="moduleOrderStyle('awards')"
    >
      <h2 class="section-title"><span>荣誉奖项</span></h2>
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
  </div>
</template>

<style scoped>
.resume-template-workplace {
  box-sizing: border-box;
  width: 100%;
  min-height: 100%;
  padding: 0 28px 16px;
  color: #1a1a1a;
  background: linear-gradient(160deg, #f8fbff 0%, #f3f6ff 100%);
  position: relative;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.resume-template-workplace::after {
  content: '';
  position: absolute;
  right: -70px;
  top: 120px;
  width: 310px;
  height: 310px;
  border-radius: 50%;
  border: 1px solid rgba(59, 130, 246, 0.12);
  box-shadow:
    inset 0 0 0 38px transparent,
    0 0 0 40px rgba(59, 130, 246, 0.06),
    0 0 0 80px rgba(59, 130, 246, 0.04);
  pointer-events: none;
}

.hero-bg {
  position: relative;
  margin: 0 -28px;
  height: 116px;
  overflow: visible;
  display: flex;
  justify-content: center;
  align-items: flex-end;
  background: linear-gradient(180deg, #4e86f2 0%, #3d74e4 100%);
  border-bottom-left-radius: 50% 28%;
  border-bottom-right-radius: 50% 28%;
}

.avatar-wrap {
  position: relative;
  left: auto;
  bottom: -24px;
  transform: none;
  margin: 0 auto -24px;
  width: 96px;
  height: 96px;
  border-radius: 50%;
  overflow: hidden;
  background: #fff;
  border: 5px solid #fff;
  box-shadow: 0 8px 24px rgba(61, 116, 228, 0.24);
  z-index: 2;
  isolation: isolate;
}

.avatar-wrap img {
  display: block;
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
  color: #8ea5d9;
  font-size: 12px;
  background: #f2f6ff;
}

.header-main {
  order: 0;
  text-align: center;
  margin-top: 54px;
  margin-bottom: 8px;
}

.name {
  margin: 0;
  font-size: 32px;
  line-height: 1.05;
  color: #1f2f4d;
  font-weight: 700;
}

.contact-line {
  margin: 8px 0 0;
  font-size: 14px;
  color: #34445f;
}

.meta-line {
  margin: 3px 0 0;
  font-size: 13px;
  color: #4b5b77;
}

.extra-links {
  margin-top: 6px;
  display: flex;
  justify-content: center;
  flex-wrap: wrap;
  gap: 10px;
}

.extra-links a {
  flex: 0 0 auto;
  max-width: 320px;
  white-space: nowrap;
  overflow-wrap: normal;
  word-break: keep-all;
  color: #2f67da;
  text-decoration: none;
  font-size: 12px;
}

.extra-links a:hover {
  text-decoration: underline;
}

.resume-section {
  margin-bottom: 8px;
}

.resume-section:last-of-type {
  margin-bottom: 0;
}

.section-title {
  position: relative;
  margin: 4px 0 4px;
  text-align: center;
  color: #3c78ef;
  font-size: 18px;
  font-weight: 500;
  line-height: 1.2;
}

.section-title::before {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  top: 50%;
  border-top: 1px solid #4f82f3;
  opacity: 0.75;
}

.section-title span {
  position: relative;
  z-index: 1;
  padding: 0 14px;
  background: linear-gradient(160deg, #f8fbff 0%, #f3f6ff 100%);
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
  color: #111827;
  font-size: 16px;
}

.entry-main strong {
  font-size: 18px;
}

.entry-date {
  color: #334155;
  font-size: 14px;
  white-space: nowrap;
}

.entry-subline {
  margin: 2px 0 0;
  color: #334155;
  font-size: 14px;
  min-height: 20px;
}

.entry-project-title {
  color: #1f2937;
  font-weight: 500;
}

.entry-link-row {
  margin: 4px 0 2px;
}

.entry-link {
  color: #2f67da;
  text-decoration: none;
  font-size: 13px;
}

.entry-link:hover {
  text-decoration: underline;
}

.entry-rich {
  margin-top: 3px;
  color: #111827;
  font-size: 13px;
  line-height: 1.85;
}

.empty {
  margin-top: 40px;
  text-align: center;
  color: #8a94ab;
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
