import { COMMON_OUTPUT_RULES } from './shared'

const BASE_PROJECT_EXPERIENCE_OUTPUT_RULES = [
  ...COMMON_OUTPUT_RULES,
  '- 技术点、专业术语、系统名词统一使用 **加粗**。',
  '- 量化结果与量词（如 **40%**、**118ms**、**3倍**、**220QPS**）统一使用 **加粗**。',
  '- 若有多段项目经历，必须按原始顺序分段输出，分段标题必须使用该段真实项目名称（可用 `### 项目名称` 格式），禁止使用“项目经历1、项目经历2”等编号标题。',
  '- 每个项目分段下固定包含一个小节：#### 工作描述。',
  '- 若只有单段项目经历，可直接输出：### 工作描述。',
  '- 不要重复输出项目名称、角色、时间、链接这些基础字段。',
  '- ### 工作描述 使用列表输出，且每条必须单行，句首必须使用“负责…”，禁止使用“针对…”。',
  '- ### 工作描述 首条可先概述项目背景（技术点 + 业务场景 + 主要功能），再分条写职责与成果，禁止出现“建设目标”这四个字。',
  '- ### 工作描述 每条优先体现并加粗：1）**技术点** 2）**数字化提升结果**。',
  '- ### 工作描述 仅当原始内容明确提到“解决了xxx问题/痛点/瓶颈”时，才允许写“解决的问题”；若原文未提到问题，禁止新增“解决xxx问题”等表述，保持原文语义。',
  '- ### 工作描述 每条至少包含 3 处 **加粗**，优先加粗：技术栈/组件名、性能或规模数字、结果对比短语（如“从xx降至xx”“提升至xx”）。',
  '- ### 工作描述 若原始内容没有明确数字，不得编造数字，可使用“待补充量化数据”占位。',
  '- 对核心技术点、优化动作、关键结果中的名词与数字必须使用 **加粗**。',
]

export function getProjectExperienceOutputRules(projectNames?: string[]): string[] {
  const rules = [...BASE_PROJECT_EXPERIENCE_OUTPUT_RULES]
  const names = (projectNames ?? []).filter(Boolean)
  if (names.length > 1) {
    rules.push(`- 当前共有 ${names.length} 段项目经历，必须按顺序使用以下项目名称作为分段标题：${names.join('、')}。`)
  }
  return rules
}

export const projectExperienceOutputRules = getProjectExperienceOutputRules()