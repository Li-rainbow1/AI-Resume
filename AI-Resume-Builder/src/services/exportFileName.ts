import type { BasicInfo, EducationEntry } from '@/stores/resume'

/** 学历高低权重，用于在没有明确最高学历时兜底排序。 */
const DEGREE_WEIGHT: Record<string, number> = {
  大专: 1,
  本科: 2,
  硕士: 3,
  博士: 4,
}

/** 文件名中不允许出现的字符，统一替换为连字符。 */
const ILLEGAL_FILENAME_CHARS = /[\\/:*?"<>|]/g

/**
 * 取最高学历对应的学校名。
 * 优先用基本信息的「最高学历」去教育经历里匹配相同学历的条目，
 * 匹配不到时按学历权重取最高的一条，仍取不到则退回第一条。
 */
function resolveTopSchool(basicInfo: BasicInfo, educationList: EducationEntry[]): string {
  const entries = educationList.filter((entry) => entry.school?.trim())
  if (entries.length === 0) return ''

  const highestLevel = basicInfo.educationLevel?.trim()
  if (highestLevel) {
    const matched = entries.find((entry) => entry.degree?.trim() === highestLevel)
    if (matched) return matched.school.trim()
  }

  const ranked = [...entries].sort(
    (left, right) =>
      (DEGREE_WEIGHT[right.degree?.trim() ?? ''] ?? 0) - (DEGREE_WEIGHT[left.degree?.trim() ?? ''] ?? 0),
  )
  return ranked[0]?.school?.trim() ?? ''
}

/** 求职意向里表示「或」的分隔符，如「测试/测开」「后端、运维」。 */
const JOB_TITLE_SEPARATORS = /[/／\\、|｜,，;；·・\-–—~～]/

/** 岗位名里可被多个岗位共用的泛化后缀，如「测试/测开工程师」里的「工程师」。 */
const GENERIC_JOB_SUFFIX = /(工程师|工程岗|岗位|岗)$/

/**
 * 取求职意向里用于命名的「第一个岗位」。
 * 「测试/测开工程师」→「测试工程师」：取分隔符前的第一段，再把末段共用的泛化后缀补回来。
 * 不含分隔符的单岗位意向原样保留。
 */
function resolvePrimaryJobTitle(jobTitle: string): string {
  const title = jobTitle?.trim() ?? ''
  if (!title) return ''

  const tokens = title
    .split(JOB_TITLE_SEPARATORS)
    .map((token) => token.trim())
    .filter(Boolean)
  if (tokens.length === 0) return ''

  // 单一岗位、且原文没有分隔符：原样保留，不做裁剪。
  if (tokens.length === 1 && !JOB_TITLE_SEPARATORS.test(title)) return title

  const first = tokens[0] ?? ''
  if (!first) return title
  if (GENERIC_JOB_SUFFIX.test(first)) return first

  // 末段的「工程师」这类后缀是两个岗位共用的，补回到第一个岗位后面。
  const sharedSuffix = (tokens[tokens.length - 1] ?? '').match(GENERIC_JOB_SUFFIX)?.[1] ?? ''
  return sharedSuffix ? `${first}${sharedSuffix}` : first
}

/**
 * 生成导出文件名（不含扩展名），格式为「学校名-求职意向-姓名」。
 * 缺失的片段自动跳过；全部为空时退回「简历」。
 */
export function buildResumeFileName(basicInfo: BasicInfo, educationList: EducationEntry[]): string {
  const segments = [
    resolveTopSchool(basicInfo, educationList),
    resolvePrimaryJobTitle(basicInfo.jobTitle ?? ''),
    basicInfo.name?.trim() ?? '',
  ].filter(Boolean)

  const raw = segments.length > 0 ? segments.join('-') : '简历'
  // 结尾的点和空格在 Windows 上会导致文件无法保存，一并清掉。
  const sanitized = raw
    .replace(ILLEGAL_FILENAME_CHARS, '-')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/[. ]+$/, '')

  return sanitized || '简历'
}
