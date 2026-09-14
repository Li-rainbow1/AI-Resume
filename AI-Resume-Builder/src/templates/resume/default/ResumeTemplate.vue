<script setup lang="ts">
import { iconPaths, iconViewBox, isFilledIcon } from '../../shared/metaIcons'
import { useResumeTemplateData } from '../../shared/useResumeTemplateData'

const { store, hasAnyContent, lineOneMeta, lineTwoMeta, lineThreeMeta, moduleOrderStyle, formatMonth, formatDatePeriod } = useResumeTemplateData()
</script>

<template>
  <div class="resume-template-default">
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
      <h2 class="section-title">教育经历</h2>
      <article v-for="edu in store.educationList" :key="edu.id" class="entry" v-show="edu.school">
        <div class="entry-head">
          <p class="entry-main education-main">
            <strong class="education-school">
              {{ edu.school }}<span v-if="edu.schoolTag" class="education-school-tag">（{{ edu.schoolTag }}）</span>
            </strong>
            <span v-if="edu.degree" class="education-divider">-</span>
            <span v-if="edu.degree" class="education-primary">{{ edu.degree }}</span>
            <span v-if="edu.major" class="education-divider">-</span>
            <span v-if="edu.major" class="education-primary">{{ edu.major }}</span>
          </p>
          <span class="entry-date education-date">{{ formatDatePeriod(edu.startDate, edu.endDate) }}</span>
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
        <div class="entry-head internship-head">
          <strong class="internship-company">{{ work.company }}</strong>
          <span v-if="work.position" class="internship-position">{{ work.position }}</span>
          <span class="entry-date internship-date">{{ formatDatePeriod(work.startDate, work.endDate) }}</span>
        </div>
        <p v-if="work.projectName" class="internship-project">{{ work.projectName }}</p>
        <p v-if="work.department || work.location" class="entry-meta entry-meta-row internship-details">
          <span v-if="work.department">{{ work.department }}</span>
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
        <div class="entry-head internship-head">
          <strong class="internship-company">{{ project.name }}</strong>
          <span v-if="project.role" class="internship-position">{{ project.role }}</span>
          <span v-if="project.startDate || project.endDate" class="entry-date internship-date">{{ formatDatePeriod(project.startDate, project.endDate) }}</span>
          <a v-else-if="project.link" class="entry-date entry-link internship-date" :href="project.link" target="_blank" rel="noopener noreferrer">{{ project.link }}</a>
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
      <h2 class="section-title">个人简介</h2>
      <div class="entry-rich" v-html="store.selfIntro"></div>
    </section>

    <div v-if="!hasAnyContent" class="empty">
      <p>在左侧填写信息，这里实时预览</p>
    </div>
  </div>
</template>

<style scoped>
.resume-template-default {
  box-sizing: border-box;
  width: 100%;
  min-height: 100%;
  padding: 28px 24px;
  color: #000;
  display: flex;
  flex-direction: column;
  /* 按视觉顺序统一模块间距，避免排序后末项选择器误清留白。 */
  row-gap: 8px;
}

.resume-header {
  display: flex;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 2px;
  order: 0;
}

.header-main {
  flex: 1;
}

.name {
  font-size: 26px;
  line-height: 1.1;
  color: #2d2521;
  margin-bottom: 10px;
}

.contact-line {
  display: flex;
  flex-wrap: wrap;
  column-gap: 14px;
  row-gap: 7px;
  color: #2d2521;
  font-size: 14px;
  line-height: 1.35;
  margin-bottom: 6px;
}

/* 消除最后一行联系信息的尾部间距，避免与头部外边距、父级行间距叠加。 */
.header-main > .contact-line:last-child {
  margin-bottom: 0;
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
  stroke: #72839a;
  stroke-width: 1.75;
  stroke-linecap: round;
  stroke-linejoin: round;
  flex-shrink: 0;
  margin-top: 1px;
}

.meta-icon-fill {
  fill: #72839a;
  stroke: none;
}

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

.resume-section {
  margin-bottom: 0;
}

.section-title {
  position: relative;
  height: 28px;
  line-height: 28px;
  margin-bottom: 6px;
  padding-left: 12px;
  font-size: 17px;
  font-weight: 700;
  color: #4d76e1;
  background: #e9eefb;
}

.section-title::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  width: 4px;
  height: 100%;
  background: #4d76e1;
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

.entry-head.internship-head {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  align-items: baseline;
  gap: 16px;
}

.internship-company {
  grid-column: 1;
  min-width: 0;
  color: #111827;
  font-size: 15px;
  font-weight: 700;
}

.internship-head .internship-position {
  grid-column: 2;
  justify-self: center;
  color: #111827;
  font-size: 15px;
  font-weight: 700;
  white-space: nowrap;
}

.internship-head .entry-date {
  grid-column: 3;
  justify-self: end;
}

.internship-details {
  margin-top: 2px;
}

.internship-project {
  margin: 2px 0 0;
  color: #111827;
  font-size: 14px;
  font-weight: 500;
  line-height: 1.5;
}

.education-main {
  gap: 8px;
}

.entry-main .education-school,
.entry-main .education-school-tag,
.entry-main .education-divider,
.entry-main .education-primary {
  color: #111827;
  font-size: 15px;
  font-weight: 700;
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

.entry-date.internship-date {
  color: #475569;
  font-weight: 400;
}

.entry-date.education-date {
  color: #475569;
  font-weight: 400;
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

.entry-link-row {
  margin-top: 2px;
  margin-bottom: 2px;
}

.entry-link {
  color: #4b89dc;
  font-size: 14px;
  text-decoration: none;
}

.entry-link:hover {
  text-decoration: underline;
}

.entry-rich {
  margin-top: 3px;
  color: #000;
  font-size: 12px;
  line-height: 1.5;
}

.empty {
  margin-top: 40px;
  text-align: center;
  color: #a08c7b;
  font-size: 12px;
  order: 999;
}

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

/* 补偿段内行高缩小的距离，保持相邻列表项之间的视觉间距。 */
:deep(.entry-rich ul > li + li),
:deep(.entry-rich ol > li + li) {
  margin-top: 5px;
}

:deep(.entry-rich li > p) {
  margin: 0;
}

:deep(.entry-rich p) {
  margin: 2px 0;
}
</style>
