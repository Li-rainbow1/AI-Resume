<script setup lang="ts">
import { computed } from 'vue'
import { toHref } from '../../shared/metaIcons'
import { useResumeTemplateData } from '../../shared/useResumeTemplateData'

const { store, hasAnyContent, simpleContactMeta, moduleOrderStyle, formatMonth, formatDatePeriod } = useResumeTemplateData()

const moduleLabelMap = computed(() =>
  store.modules.reduce<Record<string, string>>((acc, module) => {
    acc[module.key] = module.label
    return acc
  }, {})
)

const heroContactLine = computed(() =>
  simpleContactMeta.value
    .map((item) => item.text.trim())
    .filter(Boolean)
    .join(' | ')
)

const heroBaseMeta = computed(() => {
  const items = [
    store.basicInfo.wechat.trim(),
    store.basicInfo.currentCity.trim(),
    store.basicInfo.location.trim(),
  ].filter(Boolean)

  return items.join(' | ')
})

const overviewFacts = computed(() => {
  const items = [
    { label: '年龄', value: store.basicInfo.age.trim() },
    { label: '性别', value: store.basicInfo.gender.trim() },
    { label: '工作年限', value: store.basicInfo.workYears.trim() },
    { label: '学历', value: store.basicInfo.educationLevel.trim() },
    { label: '当前状态', value: store.basicInfo.currentStatus.trim() },
  ]

  return items.filter((item) => item.value)
})

const intentionFacts = computed(() => {
  const items = [
    { label: '目标岗位', value: store.basicInfo.jobTitle.trim() },
    { label: '期望城市', value: store.basicInfo.expectedLocation.trim() },
    { label: '期望薪资', value: store.basicInfo.expectedSalary.trim() },
  ]

  return items.filter((item) => item.value)
})

const profileLinks = computed(() =>
  [
    { key: 'website', label: '个人网站', value: store.basicInfo.website.trim() },
    { key: 'github', label: 'GitHub', value: store.basicInfo.github.trim() },
    { key: 'blog', label: '博客', value: store.basicInfo.blog.trim() },
  ]
    .filter((item) => item.value)
    .map((item) => ({
      ...item,
      href: toHref(item.value),
    }))
)

function sectionTitle(key: string): string {
  return moduleLabelMap.value[key] ?? ''
}

function joinParts(values: Array<string | undefined>): string {
  return values
    .map((value) => value?.trim() ?? '')
    .filter(Boolean)
    .join(' · ')
}

function projectHref(link: string): string {
  return toHref(link.trim())
}
</script>

<template>
  <div class="resume-template-red-gradient">
    <header v-if="store.isModuleVisible('basicInfo')" class="hero">
      <div class="photo-panel">
        <div class="photo-frame">
          <img v-if="store.basicInfo.avatar" :src="store.basicInfo.avatar" alt="头像" />
          <div v-else class="photo-placeholder">头像</div>
        </div>
      </div>

      <div class="hero-panel">
        <div class="hero-panel-inner">
          <h1 v-if="store.basicInfo.name" class="name">{{ store.basicInfo.name }}</h1>
          <p v-if="heroContactLine" class="hero-line">{{ heroContactLine }}</p>
          <p v-if="heroBaseMeta" class="hero-line hero-line-secondary">{{ heroBaseMeta }}</p>

          <div v-if="overviewFacts.length" class="fact-row">
            <span v-for="item in overviewFacts" :key="item.label" class="fact-chip">{{ item.label }}：{{ item.value }}</span>
          </div>

          <div v-if="intentionFacts.length" class="fact-row fact-row-soft">
            <span v-for="item in intentionFacts" :key="item.label" class="fact-chip">{{ item.label }}：{{ item.value }}</span>
          </div>

          <div v-if="profileLinks.length" class="link-row">
            <a
              v-for="item in profileLinks"
              :key="item.key"
              class="hero-link"
              :href="item.href"
              target="_blank"
              rel="noopener noreferrer"
            >
              {{ item.label }}：{{ item.value }}
            </a>
          </div>
        </div>
      </div>
    </header>

    <main class="resume-body">
      <section
        v-if="store.isModuleVisible('education') && store.educationList.some((entry) => entry.school)"
        class="resume-section"
        :style="moduleOrderStyle('education')"
      >
        <h2 class="section-title">
          <span>{{ sectionTitle('education') }}</span>
        </h2>

        <article v-for="education in store.educationList" :key="education.id" v-show="education.school" class="entry">
          <div class="entry-head">
            <p class="entry-main"><strong>{{ education.school }}</strong></p>
            <span class="entry-date">{{ formatDatePeriod(education.startDate, education.endDate) }}</span>
          </div>
          <p class="entry-subline">{{ joinParts([education.major, education.degree, education.college, education.type, education.location]) }}</p>
          <p v-if="education.gpa" class="entry-note">GPA：{{ education.gpa }}</p>
          <div v-if="education.description" class="entry-rich" v-html="education.description"></div>
        </article>
      </section>

      <section v-if="store.isModuleVisible('skills') && store.skills" class="resume-section" :style="moduleOrderStyle('skills')">
        <h2 class="section-title">
          <span>{{ sectionTitle('skills') }}</span>
        </h2>
        <div class="entry-rich" v-html="store.skills"></div>
      </section>

      <section
        v-if="store.isModuleVisible('workExperience') && store.workList.some((entry) => entry.company)"
        class="resume-section"
        :style="moduleOrderStyle('workExperience')"
      >
        <h2 class="section-title">
          <span>{{ sectionTitle('workExperience') }}</span>
        </h2>

        <article v-for="work in store.workList" :key="work.id" v-show="work.company" class="entry">
          <div class="entry-head">
            <p class="entry-main"><strong>{{ work.company }}</strong></p>
            <span class="entry-date">{{ formatDatePeriod(work.startDate, work.endDate) }}</span>
          </div>
          <p v-if="work.projectName" class="entry-subline entry-project-title">{{ work.projectName }}</p>
          <p class="entry-subline">{{ joinParts([work.position, work.department, work.location]) }}</p>
          <div v-if="work.description" class="entry-rich" v-html="work.description"></div>
        </article>
      </section>

      <section
        v-if="store.isModuleVisible('projectExperience') && store.projectList.some((entry) => entry.name)"
        class="resume-section"
        :style="moduleOrderStyle('projectExperience')"
      >
        <h2 class="section-title">
          <span>{{ sectionTitle('projectExperience') }}</span>
        </h2>

        <article v-for="project in store.projectList" :key="project.id" v-show="project.name" class="entry">
          <div class="entry-head">
            <p class="entry-main"><strong>{{ project.name }}</strong></p>
            <span v-if="project.startDate || project.endDate" class="entry-date">{{ formatDatePeriod(project.startDate, project.endDate) }}</span>
            <a v-else-if="project.link" class="entry-date entry-link" :href="projectHref(project.link)" target="_blank" rel="noopener noreferrer">{{ project.link }}</a>
          </div>
          <p class="entry-subline">{{ joinParts([project.role]) }}</p>
          <p v-if="project.link && (project.startDate || project.endDate)" class="entry-link-row">
            <a class="entry-link" :href="projectHref(project.link)" target="_blank" rel="noopener noreferrer">{{ project.link }}</a>
          </p>
          <div v-if="project.description" class="entry-rich" v-html="project.description"></div>
        </article>
      </section>

      <section
        v-if="store.isModuleVisible('awards') && store.awardList.some((entry) => entry.name)"
        class="resume-section"
        :style="moduleOrderStyle('awards')"
      >
        <h2 class="section-title">
          <span>{{ sectionTitle('awards') }}</span>
        </h2>

        <article v-for="award in store.awardList" :key="award.id" v-show="award.name" class="entry">
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
        <h2 class="section-title">
          <span>{{ sectionTitle('selfIntro') }}</span>
        </h2>
        <div class="entry-rich" v-html="store.selfIntro"></div>
      </section>

      <div v-if="!hasAnyContent" class="empty">
        <p>在左侧填写信息，这里实时预览</p>
      </div>
    </main>
  </div>
</template>

<style scoped>
.resume-template-red-gradient {
  position: relative;
  box-sizing: border-box;
  width: 100%;
  min-height: 100%;
  display: flex;
  flex-direction: column;
  color: #1f1a1a;
  background:
    radial-gradient(circle at 15% 8%, rgba(188, 16, 67, 0.05) 0, rgba(188, 16, 67, 0.05) 210px, transparent 211px),
    linear-gradient(180deg, rgba(255, 251, 252, 0.98) 0%, rgba(255, 255, 255, 0.98) 100%);
  overflow: hidden;
}

.resume-template-red-gradient::before {
  content: '';
  position: absolute;
  inset: 0;
  background:
    repeating-radial-gradient(circle at -4% 9%, rgba(114, 114, 114, 0.09) 0 2px, transparent 2px 18px),
    repeating-radial-gradient(circle at 108% -10%, rgba(195, 49, 95, 0.08) 0 1px, transparent 1px 16px);
  opacity: 0.45;
  pointer-events: none;
}

.hero {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: 176px minmax(0, 1fr);
  align-items: stretch;
  margin-bottom: 18px;
}

.photo-panel {
  position: relative;
  padding: 18px 14px 0 20px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.photo-frame {
  width: 110px;
  height: 140px;
  border-radius: 10px;
  overflow: hidden;
  background: #ffffff;
  box-shadow:
    0 16px 30px rgba(157, 23, 70, 0.14),
    0 0 0 1px rgba(162, 17, 64, 0.08);
}

.photo-frame img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.photo-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: #9c7683;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.95) 0%, rgba(251, 239, 243, 0.95) 100%);
}

.hero-panel {
  position: relative;
  min-width: 0;
  padding-top: 0;
}

.hero-panel::before {
  content: '';
  position: absolute;
  inset: 0 0 0 -18px;
  border-bottom-left-radius: 34px;
  background:
    linear-gradient(90deg, rgba(189, 13, 66, 0.94) 0%, rgba(214, 88, 129, 0.88) 100%),
    linear-gradient(180deg, rgba(255, 255, 255, 0.18) 0%, rgba(255, 255, 255, 0) 100%);
  box-shadow: 0 14px 34px rgba(190, 35, 87, 0.16);
}

.hero-panel::after {
  content: '';
  position: absolute;
  inset: 0 0 0 -18px;
  border-bottom-left-radius: 34px;
  background: repeating-radial-gradient(circle at 100% 0%, rgba(255, 255, 255, 0.15) 0 1px, transparent 1px 15px);
  mix-blend-mode: soft-light;
  opacity: 0.6;
}

.hero-panel-inner {
  position: relative;
  z-index: 1;
  min-height: 144px;
  padding: 26px 26px 20px 38px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.name {
  margin: 0;
  font-size: 27px;
  line-height: 1.08;
  font-weight: 700;
  color: #1c1012;
}

.hero-line {
  margin: 0;
  font-size: 14px;
  line-height: 1.5;
  color: rgba(28, 16, 18, 0.95);
}

.hero-line-secondary {
  color: rgba(28, 16, 18, 0.76);
}

.fact-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.fact-row-soft .fact-chip {
  background: rgba(255, 240, 244, 0.55);
}

.fact-chip {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 0 10px;
  border-radius: 999px;
  background: rgba(255, 248, 250, 0.72);
  color: rgba(38, 18, 21, 0.88);
  font-size: 12px;
  line-height: 1.4;
}

.link-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
}

.hero-link {
  color: #2d1418;
  font-size: 12px;
  line-height: 1.5;
  text-decoration: none;
  word-break: break-all;
}

.hero-link:hover {
  text-decoration: underline;
}

.resume-body {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  gap: 0;
  padding: 0 22px 18px;
}

.resume-section {
  margin-bottom: 12px;
}

.resume-section:last-of-type {
  margin-bottom: 0;
}

.section-title {
  position: relative;
  margin: 0 0 10px;
  min-height: 30px;
  display: flex;
  align-items: center;
  padding: 0 12px 0 24px;
  background: linear-gradient(90deg, rgba(244, 219, 226, 0.44) 0%, rgba(255, 244, 247, 0.72) 100%);
  color: #c11b4c;
  font-size: 15px;
  font-weight: 700;
}

.section-title::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 5px;
  background: #c11b4c;
}

.entry {
  margin-bottom: 12px;
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
  color: #1a1a1a;
  font-size: 16px;
}

.entry-main strong {
  font-size: 17px;
}

.entry-date {
  color: #4b4244;
  font-size: 14px;
  white-space: nowrap;
}

.entry-subline,
.entry-note {
  margin: 3px 0 0;
  color: #43393b;
  font-size: 14px;
  line-height: 1.6;
}

.entry-project-title {
  color: #2f2628;
  font-weight: 500;
}

.entry-link-row {
  margin: 4px 0 0;
}

.entry-link {
  color: #b31846;
  font-size: 13px;
  text-decoration: none;
  word-break: break-all;
}

.entry-link:hover {
  text-decoration: underline;
}

.entry-rich {
  margin-top: 4px;
  color: #1f1a1a;
  font-size: 13px;
  line-height: 1.85;
}

.empty {
  margin-top: 40px;
  order: 999;
  text-align: center;
  color: #917780;
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
