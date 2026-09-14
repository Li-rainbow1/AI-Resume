<script setup lang="ts">
import { computed } from 'vue'
import { iconPaths, iconViewBox, isFilledIcon, toHref, type MetaIconKey } from '../../shared/metaIcons'
import { useResumeTemplateData } from '../../shared/useResumeTemplateData'

const { store, hasAnyContent, lineOneMeta, lineTwoMeta, lineThreeMeta, moduleOrderStyle, formatMonth, formatDatePeriod } = useResumeTemplateData()

const sectionIconMap: Record<'education' | 'skills' | 'workExperience' | 'projectExperience' | 'awards' | 'selfIntro', MetaIconKey> = {
  education: 'education',
  skills: 'status',
  workExperience: 'job',
  projectExperience: 'location',
  awards: 'workYears',
  selfIntro: 'user',
}

const lineThreeWithLocation = computed(() => {
  const items = [...lineThreeMeta.value]
  const locationText = store.basicInfo.location.trim()
  if (locationText) {
    items.unshift({
      key: 'locationRaw',
      icon: 'location' as MetaIconKey,
      text: locationText,
      isLink: false,
      href: '',
    })
  }
  return items
})

function joinMeta(values: Array<string | undefined>): string {
  return values
    .map((value) => value?.trim() ?? '')
    .filter(Boolean)
    .join(' · ')
}

function projectHref(link: string): string {
  return toHref(link)
}
</script>

<template>
  <div class="resume-template-green-icon-linear">
    <div class="top-accent"></div>
    <div class="resume-body">
      <header v-if="store.isModuleVisible('basicInfo')" class="resume-header">
        <div class="header-main">
          <h1 v-if="store.basicInfo.name" class="name">{{ store.basicInfo.name }}</h1>

          <div class="meta-line">
            <span v-for="item in lineOneMeta" :key="item.key" class="meta-item">
              <span class="meta-icon-wrap">
                <svg
                  class="meta-icon-svg"
                  :class="{ 'meta-icon-fill': isFilledIcon(item.icon) }"
                  :viewBox="iconViewBox[item.icon]"
                  aria-hidden="true"
                >
                  <path v-for="(d, idx) in iconPaths[item.icon]" :key="`${item.key}-${idx}`" :d="d" />
                </svg>
              </span>
              <span>{{ item.text }}</span>
            </span>
          </div>

          <div class="meta-line">
            <span v-for="item in lineTwoMeta" :key="item.key" class="meta-item">
              <span class="meta-icon-wrap">
                <svg
                  class="meta-icon-svg"
                  :class="{ 'meta-icon-fill': isFilledIcon(item.icon) }"
                  :viewBox="iconViewBox[item.icon]"
                  aria-hidden="true"
                >
                  <path v-for="(d, idx) in iconPaths[item.icon]" :key="`${item.key}-${idx}`" :d="d" />
                </svg>
              </span>
              <span>{{ item.text }}</span>
            </span>
          </div>

          <div v-if="lineThreeWithLocation.length" class="meta-line">
            <span v-for="item in lineThreeWithLocation" :key="item.key" class="meta-item">
              <span class="meta-icon-wrap">
                <svg
                  class="meta-icon-svg"
                  :class="{ 'meta-icon-fill': isFilledIcon(item.icon) }"
                  :viewBox="iconViewBox[item.icon]"
                  aria-hidden="true"
                >
                  <path v-for="(d, idx) in iconPaths[item.icon]" :key="`${item.key}-${idx}`" :d="d" />
                </svg>
              </span>
              <a v-if="item.isLink" class="meta-link" :href="item.href" target="_blank" rel="noopener noreferrer">{{ item.text }}</a>
              <span v-else>{{ item.text }}</span>
            </span>
          </div>
        </div>

        <div v-if="store.basicInfo.avatar" class="avatar-wrap">
          <img :src="store.basicInfo.avatar" alt="头像" />
        </div>
      </header>

      <section
        v-if="store.isModuleVisible('selfIntro') && store.selfIntro"
        class="resume-section"
        :style="moduleOrderStyle('selfIntro')"
      >
        <h2 class="section-title">
          <span class="section-icon">
            <svg
              class="section-icon-svg"
              :class="{ 'section-icon-fill': isFilledIcon(sectionIconMap.selfIntro) }"
              :viewBox="iconViewBox[sectionIconMap.selfIntro]"
              aria-hidden="true"
            >
              <path v-for="(d, idx) in iconPaths[sectionIconMap.selfIntro]" :key="`selfIntro-${idx}`" :d="d" />
            </svg>
          </span>
          <span>个人简介</span>
          <span class="section-divider"></span>
        </h2>
        <div class="section-card">
          <div class="entry-rich" v-html="store.selfIntro"></div>
        </div>
      </section>

      <section
        v-if="store.isModuleVisible('education') && store.educationList.some((e) => e.school)"
        class="resume-section"
        :style="moduleOrderStyle('education')"
      >
        <h2 class="section-title">
          <span class="section-icon">
            <svg
              class="section-icon-svg"
              :class="{ 'section-icon-fill': isFilledIcon(sectionIconMap.education) }"
              :viewBox="iconViewBox[sectionIconMap.education]"
              aria-hidden="true"
            >
              <path v-for="(d, idx) in iconPaths[sectionIconMap.education]" :key="`education-${idx}`" :d="d" />
            </svg>
          </span>
          <span>教育经历</span>
          <span class="section-divider"></span>
        </h2>
        <div class="section-card">
          <article v-for="edu in store.educationList" :key="edu.id" class="entry" v-show="edu.school">
            <div class="entry-head">
              <p class="entry-title">
                <strong>{{ edu.school }}</strong>
              </p>
              <span class="entry-date">{{ formatDatePeriod(edu.startDate, edu.endDate) }}</span>
            </div>
            <p v-if="joinMeta([edu.major, edu.degree, edu.type, edu.college, edu.location, edu.gpa ? `GPA ${edu.gpa}` : ''])" class="entry-subline">
              {{ joinMeta([edu.major, edu.degree, edu.type, edu.college, edu.location, edu.gpa ? `GPA ${edu.gpa}` : '']) }}
            </p>
            <div v-if="edu.description" class="entry-rich" v-html="edu.description"></div>
          </article>
        </div>
      </section>

      <section v-if="store.isModuleVisible('skills') && store.skills" class="resume-section" :style="moduleOrderStyle('skills')">
        <h2 class="section-title">
          <span class="section-icon">
            <svg
              class="section-icon-svg"
              :class="{ 'section-icon-fill': isFilledIcon(sectionIconMap.skills) }"
              :viewBox="iconViewBox[sectionIconMap.skills]"
              aria-hidden="true"
            >
              <path v-for="(d, idx) in iconPaths[sectionIconMap.skills]" :key="`skills-${idx}`" :d="d" />
            </svg>
          </span>
          <span>专业技能</span>
          <span class="section-divider"></span>
        </h2>
        <div class="section-card">
          <div class="entry-rich" v-html="store.skills"></div>
        </div>
      </section>

      <section
        v-if="store.isModuleVisible('workExperience') && store.workList.some((w) => w.company)"
        class="resume-section"
        :style="moduleOrderStyle('workExperience')"
      >
        <h2 class="section-title">
          <span class="section-icon">
            <svg
              class="section-icon-svg"
              :class="{ 'section-icon-fill': isFilledIcon(sectionIconMap.workExperience) }"
              :viewBox="iconViewBox[sectionIconMap.workExperience]"
              aria-hidden="true"
            >
              <path v-for="(d, idx) in iconPaths[sectionIconMap.workExperience]" :key="`work-${idx}`" :d="d" />
            </svg>
          </span>
          <span>实习经历</span>
          <span class="section-divider"></span>
        </h2>
        <div class="section-card">
          <article v-for="work in store.workList" :key="work.id" class="entry" v-show="work.company">
            <div class="entry-head">
              <div class="entry-main">
                <p class="entry-title">
                  <strong>{{ work.company }}</strong>
                </p>
                <p v-if="work.projectName" class="entry-subline entry-project-title">{{ work.projectName }}</p>
                <p v-if="joinMeta([work.position, work.department])" class="entry-subline">{{ joinMeta([work.position, work.department]) }}</p>
              </div>
              <div class="entry-side">
                <span class="entry-date">{{ formatDatePeriod(work.startDate, work.endDate) }}</span>
                <span v-if="work.location" class="entry-location">{{ work.location }}</span>
              </div>
            </div>
            <div v-if="work.description" class="entry-rich" v-html="work.description"></div>
          </article>
        </div>
      </section>

      <section
        v-if="store.isModuleVisible('projectExperience') && store.projectList.some((p) => p.name)"
        class="resume-section"
        :style="moduleOrderStyle('projectExperience')"
      >
        <h2 class="section-title">
          <span class="section-icon">
            <svg
              class="section-icon-svg"
              :class="{ 'section-icon-fill': isFilledIcon(sectionIconMap.projectExperience) }"
              :viewBox="iconViewBox[sectionIconMap.projectExperience]"
              aria-hidden="true"
            >
              <path v-for="(d, idx) in iconPaths[sectionIconMap.projectExperience]" :key="`project-${idx}`" :d="d" />
            </svg>
          </span>
          <span>项目经历</span>
          <span class="section-divider"></span>
        </h2>
        <div class="section-card">
          <article v-for="project in store.projectList" :key="project.id" class="entry" v-show="project.name">
            <div class="entry-head">
              <div class="entry-main">
                <p class="entry-title">
                  <strong>{{ project.name }}</strong>
                </p>
                <p v-if="project.role" class="entry-subline">{{ project.role }}</p>
              </div>
              <span v-if="project.startDate || project.endDate" class="entry-date">{{ formatDatePeriod(project.startDate, project.endDate) }}</span>
              <a v-else-if="project.link" class="entry-date entry-link" :href="projectHref(project.link)" target="_blank" rel="noopener noreferrer">{{ project.link }}</a>
            </div>
            <p v-if="project.link && (project.startDate || project.endDate)" class="entry-link-row">
              <a class="entry-link" :href="projectHref(project.link)" target="_blank" rel="noopener noreferrer">{{ project.link }}</a>
            </p>
            <div v-if="project.description" class="entry-rich" v-html="project.description"></div>
          </article>
        </div>
      </section>

      <section
        v-if="store.isModuleVisible('awards') && store.awardList.some((a) => a.name)"
        class="resume-section"
        :style="moduleOrderStyle('awards')"
      >
        <h2 class="section-title">
          <span class="section-icon">
            <svg
              class="section-icon-svg"
              :class="{ 'section-icon-fill': isFilledIcon(sectionIconMap.awards) }"
              :viewBox="iconViewBox[sectionIconMap.awards]"
              aria-hidden="true"
            >
              <path v-for="(d, idx) in iconPaths[sectionIconMap.awards]" :key="`awards-${idx}`" :d="d" />
            </svg>
          </span>
          <span>荣誉奖项</span>
          <span class="section-divider"></span>
        </h2>
        <div class="section-card">
          <article v-for="award in store.awardList" :key="award.id" class="entry" v-show="award.name">
            <div class="entry-head">
              <p class="entry-title">
                <strong>{{ award.name }}</strong>
              </p>
              <span class="entry-date">{{ formatMonth(award.date) }}</span>
            </div>
            <div v-if="award.description" class="entry-rich" v-html="award.description"></div>
          </article>
        </div>
      </section>

      <div v-if="!hasAnyContent" class="empty">
        <p>在左侧填写信息，这里实时预览</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.resume-template-green-icon-linear {
  position: relative;
  box-sizing: border-box;
  width: 100%;
  min-height: 100%;
  display: flex;
  flex-direction: column;
  color: #1f2933;
  background: #edf2f5;
  overflow: hidden;
}

.resume-template-green-icon-linear::before {
  content: '';
  position: absolute;
  right: -128px;
  top: 24px;
  width: 520px;
  height: 360px;
  border-radius: 50%;
  background:
    repeating-radial-gradient(circle at 0 0, rgba(152, 169, 177, 0.32) 0 2px, transparent 2px 22px);
  opacity: 0.6;
  pointer-events: none;
}

.top-accent {
  height: 18px;
  background: linear-gradient(90deg, #0b7f7d 0%, #148f89 55%, #1a9e98 100%);
  order: 0;
  z-index: 1;
}

.resume-body {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  padding: 12px 20px 20px;
}

.resume-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 24px;
  margin-bottom: 8px;
  order: 0;
}

.header-main {
  flex: 1;
  min-width: 0;
}

.name {
  margin: 0 0 10px;
  font-size: 26px;
  line-height: 1.05;
  font-weight: 700;
  color: #1c252d;
}

.meta-line {
  display: flex;
  flex-wrap: wrap;
  row-gap: 6px;
  column-gap: 12px;
  margin-bottom: 4px;
  font-size: 14px;
  line-height: 1.35;
  color: #2b3640;
}

.meta-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.meta-item + .meta-item::before {
  content: '|';
  color: #738089;
  margin-right: 6px;
}

.meta-icon-wrap {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #198e89;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.meta-icon-svg {
  width: 9px;
  height: 9px;
  fill: none;
  stroke: #ffffff;
  stroke-width: 1.85;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.meta-icon-fill {
  fill: #ffffff;
  stroke: none;
}

.meta-link {
  color: #198e89;
  text-decoration: none;
  word-break: break-all;
}

.meta-link:hover {
  text-decoration: underline;
}

.avatar-wrap {
  width: 104px;
  height: 136px;
  border: 1px solid #d0dce2;
  background: #f8fafb;
  overflow: hidden;
  flex-shrink: 0;
}

.avatar-wrap img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.resume-section {
  margin-bottom: 8px;
}

.section-title {
  margin: 0 0 6px;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 20px;
  font-weight: 700;
  color: #198e89;
  line-height: 1.25;
}

.section-icon {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #198e89;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.section-icon-svg {
  width: 11px;
  height: 11px;
  fill: none;
  stroke: #ffffff;
  stroke-width: 1.75;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.section-icon-fill {
  fill: #ffffff;
  stroke: none;
}

.section-divider {
  flex: 1;
  min-width: 80px;
  border-top: 1px solid #c3d5dd;
  transform: translateY(1px);
}

.section-card {
  border: 1px solid #d7e1e7;
  border-radius: 12px;
  background: #f7fafc;
  padding: 9px 12px;
}

.entry {
  margin-bottom: 8px;
}

.entry:last-child {
  margin-bottom: 0;
}

.entry-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 14px;
}

.entry-main {
  min-width: 0;
}

.entry-side {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 1px;
  flex-shrink: 0;
}

.entry-title {
  margin: 0;
  color: #1f2933;
  font-size: 16px;
  line-height: 1.35;
}

.entry-title strong {
  font-weight: 700;
}

.entry-date {
  color: #4b5660;
  font-size: 14px;
  white-space: nowrap;
}

.entry-location {
  color: #4b5660;
  font-size: 14px;
}

.entry-subline {
  margin: 2px 0 0;
  color: #4b5660;
  font-size: 14px;
  line-height: 1.5;
}

.entry-project-title {
  color: #1f2937;
  font-weight: 500;
}

.entry-link-row {
  margin: 3px 0 0;
}

.entry-link {
  color: #198e89;
  text-decoration: none;
  font-size: 14px;
  word-break: break-all;
}

.entry-link:hover {
  text-decoration: underline;
}

.entry-rich {
  margin-top: 3px;
  color: #1f2933;
  font-size: 12px;
  line-height: 1.65;
}

.empty {
  margin-top: 30px;
  text-align: center;
  color: #748089;
  font-size: 12px;
  order: 999;
}

:deep(.entry-rich ul) {
  margin: 0;
  padding-left: 1.2em;
  list-style: disc;
}

:deep(.entry-rich ul li) {
  margin: 2px 0;
}

:deep(.entry-rich ol) {
  margin: 0;
  padding-left: 1.25em;
  list-style: decimal;
  list-style-position: outside;
}

:deep(.entry-rich ol li) {
  margin: 2px 0;
}

:deep(.entry-rich li > p) {
  margin: 0;
}

:deep(.entry-rich p) {
  margin: 2px 0;
}
</style>
