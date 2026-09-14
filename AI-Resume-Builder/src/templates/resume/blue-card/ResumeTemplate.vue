<script setup lang="ts">
import { iconPaths, iconViewBox, isFilledIcon } from '../../shared/metaIcons'
import { useResumeTemplateData } from '../../shared/useResumeTemplateData'

const { store, hasAnyContent, lineOneMeta, lineTwoMeta, lineThreeMeta, moduleOrderStyle, formatMonth, formatDatePeriod } = useResumeTemplateData()
</script>

<template>
  <div class="resume-template-blue-card">
    <header v-if="store.isModuleVisible('basicInfo')" class="resume-header">
      <div class="header-main">
        <h1 v-if="store.basicInfo.name" class="name">{{ store.basicInfo.name }}</h1>

        <div class="contact-line">
          <span v-for="item in lineOneMeta" :key="item.key" class="meta-item">
            <svg
              class="meta-icon-svg"
              :class="{ 'meta-icon-fill': isFilledIcon(item.icon) }"
              :viewBox="iconViewBox[item.icon]"
              aria-hidden="true"
            >
              <path v-for="(d, idx) in iconPaths[item.icon]" :key="`${item.key}-${idx}`" :d="d" />
            </svg>
            {{ item.text }}
          </span>
        </div>

        <div class="contact-line">
          <span v-for="item in lineTwoMeta" :key="item.key" class="meta-item">
            <svg
              class="meta-icon-svg"
              :class="{ 'meta-icon-fill': isFilledIcon(item.icon) }"
              :viewBox="iconViewBox[item.icon]"
              aria-hidden="true"
            >
              <path v-for="(d, idx) in iconPaths[item.icon]" :key="`${item.key}-${idx}`" :d="d" />
            </svg>
            {{ item.text }}
          </span>
        </div>

        <div v-if="lineThreeMeta.length" class="contact-line">
          <span v-for="item in lineThreeMeta" :key="item.key" class="meta-item">
            <svg
              class="meta-icon-svg"
              :class="{ 'meta-icon-fill': isFilledIcon(item.icon) }"
              :viewBox="iconViewBox[item.icon]"
              aria-hidden="true"
            >
              <path v-for="(d, idx) in iconPaths[item.icon]" :key="`${item.key}-${idx}`" :d="d" />
            </svg>
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
      v-if="store.isModuleVisible('education') && store.educationList.some((e) => e.school)"
      class="resume-section"
      :style="moduleOrderStyle('education')"
    >
      <h2 class="section-title"><span class="section-badge">教育经历</span><span class="section-line"></span></h2>
      <article v-for="edu in store.educationList" :key="edu.id" class="entry" v-show="edu.school">
        <div class="entry-head">
          <p class="entry-main">
            <strong>{{ edu.school }}</strong>
            <span v-if="edu.major || edu.degree" class="entry-inline-parts">
              <span v-if="edu.major">{{ edu.major }}</span>
              <span v-if="edu.major && edu.degree" class="dot-sep">·</span>
              <span v-if="edu.degree">{{ edu.degree }}</span>
            </span>
          </p>
          <span class="entry-date">{{ formatDatePeriod(edu.startDate, edu.endDate) }}</span>
        </div>
        <p class="entry-meta entry-meta-row">
          <span v-if="edu.type">{{ edu.type }}</span>
          <span v-if="edu.college">{{ edu.college }}</span>
          <span v-if="edu.location">{{ edu.location }}</span>
          <span v-if="edu.gpa">GPA：{{ edu.gpa }}</span>
        </p>
        <div v-if="edu.description" class="entry-rich" v-html="edu.description"></div>
      </article>
    </section>

    <section v-if="store.isModuleVisible('skills') && store.skills" class="resume-section" :style="moduleOrderStyle('skills')">
      <h2 class="section-title"><span class="section-badge">专业技能</span><span class="section-line"></span></h2>
      <div class="entry-rich" v-html="store.skills"></div>
    </section>

    <section
      v-if="store.isModuleVisible('workExperience') && store.workList.some((w) => w.company)"
      class="resume-section"
      :style="moduleOrderStyle('workExperience')"
    >
      <h2 class="section-title"><span class="section-badge">实习经历</span><span class="section-line"></span></h2>
      <article v-for="work in store.workList" :key="work.id" class="entry" v-show="work.company">
        <div class="entry-head">
          <p class="entry-main entry-main-wrap">
            <strong>{{ work.company }}</strong>
            <span v-if="work.department">{{ work.department }}</span>
            <span v-if="work.department && work.position" class="dot-sep">·</span>
            <span v-if="work.position">{{ work.position }}</span>
            <span v-if="(work.department || work.position) && work.location" class="dot-sep">·</span>
            <span v-if="work.location">{{ work.location }}</span>
          </p>
          <span class="entry-date">{{ formatDatePeriod(work.startDate, work.endDate) }}</span>
        </div>
        <p v-if="work.projectName" class="entry-meta entry-project-title">{{ work.projectName }}</p>
        <div v-if="work.description" class="entry-rich" v-html="work.description"></div>
      </article>
    </section>

    <section
      v-if="store.isModuleVisible('projectExperience') && store.projectList.some((p) => p.name)"
      class="resume-section"
      :style="moduleOrderStyle('projectExperience')"
    >
      <h2 class="section-title"><span class="section-badge">项目经历</span><span class="section-line"></span></h2>
      <article v-for="project in store.projectList" :key="project.id" class="entry" v-show="project.name">
        <div class="entry-head">
          <p class="entry-main">
            <strong>{{ project.name }}</strong>
            <span v-if="project.role">{{ project.role }}</span>
          </p>
          <span v-if="project.startDate || project.endDate" class="entry-date">{{ formatDatePeriod(project.startDate, project.endDate) }}</span>
          <a v-else-if="project.link" class="entry-date entry-link" :href="project.link" target="_blank" rel="noopener noreferrer">{{ project.link }}</a>
        </div>
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
      <h2 class="section-title"><span class="section-badge">荣誉奖项</span><span class="section-line"></span></h2>
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
      <h2 class="section-title"><span class="section-badge">个人简介</span><span class="section-line"></span></h2>
      <div class="entry-rich" v-html="store.selfIntro"></div>
    </section>

    <div v-if="!hasAnyContent" class="empty">
      <p>在左侧填写信息，这里实时预览</p>
    </div>
  </div>
</template>

<style scoped>
.resume-template-blue-card {
  box-sizing: border-box;
  width: 100%;
  min-height: 100%;
  padding: 28px 24px;
  color: #000;
  display: flex;
  flex-direction: column;
  background: #ffffff;
}

/* ─── Header ─── */
.resume-header {
  display: flex;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 12px;
  order: 0;
}

.header-main {
  flex: 1;
}

.name {
  font-size: 26px;
  line-height: 1.1;
  color: #1a1a1a;
  margin-bottom: 10px;
  text-align: center;
}

.contact-line {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  column-gap: 14px;
  row-gap: 7px;
  color: #333;
  font-size: 14px;
  line-height: 1.35;
  margin-bottom: 6px;
}

.meta-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  line-height: 1.25;
}

.meta-link {
  color: #2563eb;
  text-decoration: none;
}

.meta-link:hover {
  color: #1d4ed8;
  text-decoration: underline;
}

.meta-icon-svg {
  display: block;
  width: 14px;
  height: 14px;
  fill: none;
  stroke: #3b82f6;
  stroke-width: 1.75;
  stroke-linecap: round;
  stroke-linejoin: round;
  flex-shrink: 0;
  margin-top: 1px;
}

.meta-icon-fill {
  fill: #3b82f6;
  stroke: none;
}

/* ─── Avatar ─── */
.avatar-wrap {
  width: 84px;
  height: 104px;
  border-radius: 6px;
  overflow: hidden;
  border: 1px solid #dbe1ea;
  flex-shrink: 0;
}

.avatar-wrap img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

/* ─── Section ─── */
.resume-section {
  margin-bottom: 10px;
}

.resume-section:last-of-type {
  margin-bottom: 0;
}

/* ─── Section Title — Blue Pill Badge ─── */
.section-title {
  margin: 0 0 8px;
  font-size: 16px;
  line-height: 1;
  display: flex;
  align-items: flex-end;
  gap: 0;
}

.section-badge {
  display: inline-block;
  background: #2855a0;
  color: #ffffff;
  font-size: 16px;
  font-weight: 700;
  line-height: 1;
  padding: 6px 18px;
  border-radius: 4px;
  letter-spacing: 0.5px;
  flex-shrink: 0;
}

.section-line {
  flex: 1;
  height: 0;
  border-top: 1px solid #d0d7e2;
  margin-left: 0;
  margin-bottom: 1px;
}

/* ─── Entry ─── */
.entry {
  margin-bottom: 8px;
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
  display: flex;
  align-items: baseline;
  gap: 10px;
  color: #000;
  font-size: 16px;
}

.entry-main-wrap {
  flex-wrap: wrap;
  gap: 4px;
  row-gap: 4px;
}

.entry-main strong {
  font-size: 17px;
}

.entry-main span {
  font-size: 14px;
  color: rgb(148, 163, 184);
}

.entry-inline-parts {
  display: inline-flex;
  align-items: baseline;
  gap: 3px;
}

.dot-sep {
  color: rgb(148, 163, 184);
  margin: 0 1px;
}

.entry-date {
  color: #94a3b8;
  font-size: 14px;
  white-space: nowrap;
}

.entry-meta {
  margin-top: 1px;
  color: #94a3b8;
  font-size: 14px;
}

.entry-meta-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
}

.entry-project-title {
  color: #1f2937;
  font-weight: 500;
}

.entry-link-row {
  margin-top: 2px;
  margin-bottom: 2px;
}

.entry-link {
  color: #3b82f6;
  font-size: 14px;
  text-decoration: none;
}

.entry-link:hover {
  text-decoration: underline;
}

/* ─── Rich Text ─── */
.entry-rich {
  margin-top: 3px;
  color: #000;
  font-size: 12px;
  line-height: 1.75;
}

.empty {
  margin-top: 40px;
  text-align: center;
  color: #94a3b8;
  font-size: 12px;
  order: 999;
}

/* ─── Deep selectors for rich content ─── */
:deep(.entry-rich ul) {
  margin: 0;
  padding: 0;
  list-style: none;
}

:deep(.entry-rich ul li) {
  position: relative;
  margin: 2px 0;
  padding-left: 16px;
}

:deep(.entry-rich ul li::marker) {
  content: '';
}

:deep(.entry-rich ul li::before) {
  content: '';
  position: absolute;
  left: 2px;
  top: 0.95em;
  transform: translateY(-50%);
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
}

:deep(.entry-rich ol) {
  margin: 0;
  padding-left: 1.25em;
  list-style: decimal;
  list-style-position: outside;
}

:deep(.entry-rich ol li) {
  margin: 2px 0;
  padding-left: 0.1em;
}

:deep(.entry-rich ol li::marker) {
  color: #000;
  font-size: 1em;
  font-weight: inherit;
}

:deep(.entry-rich li > p) {
  margin: 0;
}

:deep(.entry-rich p) {
  margin: 2px 0;
}
</style>
