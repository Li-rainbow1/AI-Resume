import { computed } from 'vue'
import { useResumeStore } from '@/stores/resume'
import type { MetaIconKey } from './metaIcons'
import { toHref } from './metaIcons'

export function useResumeTemplateData() {
  const store = useResumeStore()

  const hasBasicInfo = computed(() => {
    const b = store.basicInfo
    return Boolean(b.name || b.phone || b.email || b.jobTitle || b.wechat || b.currentCity || b.website || b.github || b.blog)
  })

  const hasAnyContent = computed(
    () =>
      hasBasicInfo.value ||
      store.educationList.some((e) => e.school) ||
      Boolean(store.skills) ||
      store.workList.some((w) => w.company) ||
      store.projectList.some((p) => p.name) ||
      store.awardList.some((a) => a.name) ||
      Boolean(store.selfIntro)
  )

  const lineOneMeta = computed(() =>
    [
      { key: 'phone', icon: 'phone' as MetaIconKey, text: store.basicInfo.phone.trim() },
      { key: 'mail', icon: 'mail' as MetaIconKey, text: store.basicInfo.email.trim() },
      { key: 'user', icon: 'user' as MetaIconKey, text: store.basicInfo.age.trim() },
      { key: 'workYears', icon: 'workYears' as MetaIconKey, text: store.basicInfo.workYears.trim() },
    ].filter((item) => item.text)
  )

  const lineTwoMeta = computed(() =>
    [
      { key: 'gender', icon: 'gender' as MetaIconKey, text: store.basicInfo.gender.trim() },
      { key: 'status', icon: 'status' as MetaIconKey, text: store.basicInfo.currentStatus.trim() },
      {
        key: 'job',
        icon: 'job' as MetaIconKey,
        text: store.basicInfo.jobTitle.trim() ? `求职意向：${store.basicInfo.jobTitle.trim()}` : '',
      },
      { key: 'location', icon: 'location' as MetaIconKey, text: store.basicInfo.expectedLocation.trim() },
      { key: 'salary', icon: 'salary' as MetaIconKey, text: store.basicInfo.expectedSalary.trim() },
      { key: 'education', icon: 'education' as MetaIconKey, text: store.basicInfo.educationLevel.trim() },
    ].filter((item) => item.text)
  )

  const simpleContactMeta = computed(() =>
    [
      { key: 'phone', icon: 'phone' as MetaIconKey, text: store.basicInfo.phone.trim() },
      { key: 'mail', icon: 'mail' as MetaIconKey, text: store.basicInfo.email.trim() },
    ].filter((item) => item.text)
  )

  const lineThreeMeta = computed(() => {
    const items = [
      { key: 'wechat', icon: 'wechat' as MetaIconKey, text: store.basicInfo.wechat || '', isLink: false },
      { key: 'currentCity', icon: 'currentCity' as MetaIconKey, text: store.basicInfo.currentCity || '', isLink: false },
      { key: 'website', icon: 'website' as MetaIconKey, text: store.basicInfo.website || '', isLink: true },
      { key: 'github', icon: 'github' as MetaIconKey, text: store.basicInfo.github || '', isLink: true },
      { key: 'blog', icon: 'blog' as MetaIconKey, text: store.basicInfo.blog || '', isLink: true },
    ]

    return items
      .filter((item) => item.text.trim())
      .map((item) => ({
        ...item,
        href: item.isLink ? toHref(item.text) : '',
      }))
  })

  const moduleOrderMap = computed(() => {
    const map: Record<string, number> = {}
    let order = 1
    store.modules.forEach((mod) => {
      if (mod.key === 'basicInfo') return
      map[mod.key] = order
      order += 1
    })
    return map
  })

  function moduleOrderStyle(key: string): { order: number } {
    return { order: moduleOrderMap.value[key] ?? 99 }
  }

  function formatMonth(value: string): string {
    const match = /^(\d{4})-(\d{2})$/.exec(value)
    return match ? `${match[1]}.${match[2]}` : value
  }

  function formatDatePeriod(startDate: string, endDate: string): string {
    const start = formatMonth(startDate)
    const end = endDate ? formatMonth(endDate) : '至今'
    return start ? `${start} - ${end}` : end
  }

  return {
    store,
    hasAnyContent,
    lineOneMeta,
    lineTwoMeta,
    simpleContactMeta,
    lineThreeMeta,
    moduleOrderStyle,
    formatMonth,
    formatDatePeriod,
  }
}
