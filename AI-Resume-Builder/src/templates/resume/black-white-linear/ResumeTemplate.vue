<script setup lang="ts">
import { computed } from 'vue'
import { toHref } from '../../shared/metaIcons'
import { useResumeTemplateData } from '../../shared/useResumeTemplateData'

const { store, hasAnyContent, moduleOrderStyle, formatMonth, formatDatePeriod } = useResumeTemplateData()

const headerContact = computed(() => {
  const phone = store.basicInfo.phone.trim()
  const email = store.basicInfo.email.trim()
  const items = [phone, email].filter(Boolean)
  return items.join(' | ')
})

const baseMetaLineOne = computed(() => {
  const items = [
    store.basicInfo.age.trim(),
    store.basicInfo.gender.trim(),
    store.basicInfo.workYears ? `${store.basicInfo.workYears}经验` : '',
    store.basicInfo.educationLevel.trim(),
  ].filter(Boolean)

  return items.join(' | ')
})

const baseMetaLineTwo = computed(() => {
  const items = [
    store.basicInfo.currentStatus.trim(),
    store.basicInfo.jobTitle.trim(),
    (store.basicInfo.expectedLocation || store.basicInfo.currentCity).trim(),
    store.basicInfo.expectedSalary.trim(),
  ].filter(Boolean)

  return items.join(' | ')
})

const baseMetaLineThree = computed(() => {
  const items: string[] = []
  const wechat = store.basicInfo.wechat.trim()
  const currentCity = store.basicInfo.currentCity.trim()
  if (wechat) items.push(`微信: ${wechat}`)
  if (currentCity) items.push(`现居: ${currentCity}`)
  return items.join(' | ')
})

const extraLinks = computed(() => {
  const items = [store.basicInfo.website, store.basicInfo.github, store.basicInfo.blog]
    .map((item) => item.trim())
    .filter(Boolean)

  return items.map((text) => ({
    text,
    href: toHref(text),
  }))
})
</script>

<template>
  <div class="resume-template-black-white-linear">
    <header v-if="store.isModuleVisible('basicInfo')" class="resume-header">
      <div class="header-main">
        <h1 v-if="store.basicInfo.name" class="name">{{ store.basicInfo.name }}</h1>
        <p v-if="headerContact" class="header-contact">{{ headerContact }}</p>
        <p v-if="baseMetaLineOne" class="header-meta">{{ baseMetaLineOne }}</p>
        <p v-if="baseMetaLineTwo" class="header-meta">{{ baseMetaLineTwo }}</p>
        <p v-if="baseMetaLineThree" class="header-meta">{{ baseMetaLineThree }}</p>
        <div v-if="extraLinks.length" class="header-links">
          <a v-for="item in extraLinks" :key="item.text" :href="item.href" target="_blank" rel="noopener noreferrer">{{ item.text }}</a>
        </div>
      </div>

      <div class="avatar-wrap">
        <img v-if="store.basicInfo.avatar" :src="store.basicInfo.avatar" alt="头像" />
        <div v-else class="avatar-placeholder">头像</div>
      </div>
    </header>

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
        <p v-if="work.position || work.location" class="entry-subline">
          <span v-if="work.position">{{ work.position }}</span>
          <span v-if="work.location">{{ work.location }}</span>
        </p>
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
          <p class="entry-main">
            <strong>{{ project.name }}</strong>
          </p>
          <span v-if="project.startDate || project.endDate" class="entry-date">{{ formatDatePeriod(project.startDate, project.endDate) }}</span>
          <a v-else-if="project.link" class="entry-date entry-link" :href="project.link" target="_blank" rel="noopener noreferrer">{{ project.link }}</a>
        </div>
        <p v-if="project.role" class="entry-subline">
          <span>{{ project.role }}</span>
        </p>
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
      <h2 class="section-title">教育经历</h2>
      <article v-for="edu in store.educationList" :key="edu.id" class="entry" v-show="edu.school">
        <div class="entry-head">
          <p class="entry-main"><strong>{{ edu.school }}</strong></p>
          <span class="entry-date">{{ formatDatePeriod(edu.startDate, edu.endDate) }}</span>
        </div>
        <p class="entry-subline">
          <span v-if="edu.major || edu.degree">
            <template v-if="edu.major">{{ edu.major }}</template>
            <template v-if="edu.major && edu.degree"> · </template>
            <template v-if="edu.degree">{{ edu.degree }}</template>
          </span>
          <span v-if="edu.location">{{ edu.location }}</span>
          <span v-if="edu.gpa">GPA：{{ edu.gpa }}</span>
        </p>
        <div v-if="edu.description" class="entry-rich" v-html="edu.description"></div>
      </article>
    </section>

    <section v-if="store.isModuleVisible('skills') && store.skills" class="resume-section" :style="moduleOrderStyle('skills')">
      <h2 class="section-title">专业技能</h2>
      <div class="entry-rich" v-html="store.skills"></div>
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

    <section
      v-if="store.isModuleVisible('selfIntro') && store.selfIntro"
      class="resume-section"
      :style="moduleOrderStyle('selfIntro')"
    >
      <h2 class="section-title">其他</h2>
      <div class="entry-rich" v-html="store.selfIntro"></div>
    </section>

    <div v-if="!hasAnyContent" class="empty">
      <p>在左侧填写信息，这里实时预览</p>
    </div>
  </div>
</template>

<style scoped>
.resume-template-black-white-linear {
  box-sizing: border-box;
  width: 100%;
  min-height: 100%;
  padding: 20px 28px 16px;
  color: #111;
  display: flex;
  flex-direction: column;
}

.resume-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 24px;
  margin-bottom: 10px;
  order: 0;
}

.header-main {
  flex: 1;
  text-align: center;
  padding-top: 4px;
}

.name {
  margin: 0;
  font-size: 30px;
  line-height: 1.08;
  font-weight: 800;
  letter-spacing: 1px;
  color: #111;
}

.header-contact {
  margin: 8px 0 0;
  font-size: 14px;
  color: #3b3b3b;
  font-weight: 600;
}

.header-meta {
  margin: 4px 0 0;
  font-size: 13px;
  color: #666;
}

.header-links {
  margin-top: 4px;
  display: flex;
  justify-content: center;
  flex-wrap: wrap;
  gap: 10px;
}

.header-links a {
  color: #2d2d2d;
  text-decoration: underline;
  text-decoration-thickness: 1px;
  text-underline-offset: 2px;
  font-size: 12px;
}

.avatar-wrap {
  width: 88px;
  height: 108px;
  border: 1px solid #b9b9b9;
  border-radius: 4px;
  overflow: hidden;
  flex-shrink: 0;
  background: #f2f2f2;
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
  color: #9a9a9a;
  font-size: 12px;
}

.resume-section {
  margin-bottom: 10px;
}

.resume-section:last-of-type {
  margin-bottom: 0;
}

.section-title {
  margin: 0 0 8px;
  padding: 0 0 6px;
  font-size: 16px;
  font-weight: 700;
  color: #111;
  border-bottom: 1px solid #2c2c2c;
  line-height: 1.3;
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
  font-size: 16px;
  color: #111;
}

.entry-main strong {
  font-size: 17px;
  font-weight: 700;
}

.entry-date {
  color: #3a3a3a;
  font-size: 14px;
  white-space: nowrap;
}

.entry-subline {
  margin: 3px 0 0;
  display: flex;
  justify-content: space-between;
  gap: 14px;
  font-size: 14px;
  color: #434343;
}

.entry-project-title {
  justify-content: flex-start;
  color: #1f2937;
  font-weight: 500;
}

.entry-link-row {
  margin-top: 4px;
  margin-bottom: 2px;
}

.entry-link {
  color: #2d2d2d;
  text-decoration: underline;
  font-size: 13px;
}

.entry-rich {
  margin-top: 3px;
  color: #111;
  font-size: 14px;
  line-height: 1.85;
}

.empty {
  margin-top: 40px;
  text-align: center;
  color: #8d8d8d;
  font-size: 12px;
  order: 999;
}

:deep(.entry-rich ul) {
  margin: 0;
  padding-left: 1.2em;
  list-style: disc;
}

:deep(.entry-rich ul li) {
  margin: 4px 0;
  padding-left: 0;
}

:deep(.entry-rich ol) {
  margin: 0;
  padding-left: 1.25em;
  list-style: decimal;
  list-style-position: outside;
}

:deep(.entry-rich ol li) {
  margin: 4px 0;
  padding-left: 0;
}

:deep(.entry-rich li > p) {
  margin: 0;
}

:deep(.entry-rich p) {
  margin: 3px 0;
}
</style>
