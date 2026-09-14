import { COMMON_OUTPUT_RULES } from './shared'

const BASE_WORK_EXPERIENCE_OUTPUT_RULES = [
  ...COMMON_OUTPUT_RULES,
  '- 若有多段实习经历，必须按原始顺序逐段输出，不要合并成一段。',
  '- 每段分段标题必须使用该段真实公司名称（可用 `### 公司名` 格式），禁止使用“实习经历1、实习经历2”等编号标题。',
  '- 每段只输出该经历的“工作描述”优化结果，不要重复公司、部门、职位、时间字段。',
  '- 若原始内容为有序列表则保持有序列表；若原始内容为无序列表则保持无序列表。',
  '- 每段内容使用列表输出，且每条必须单行。',
]

export function getWorkExperienceOutputRules(companyNames?: string[]): string[] {
  const rules = [...BASE_WORK_EXPERIENCE_OUTPUT_RULES]
  const names = (companyNames ?? []).filter(Boolean)
  if (names.length > 1) {
    rules.push(`- 当前共有 ${names.length} 段实习经历，必须按顺序使用以下公司名称作为分段标题：${names.join('、')}。`)
  }
  return rules
}

export const workExperienceOutputRules = getWorkExperienceOutputRules()
