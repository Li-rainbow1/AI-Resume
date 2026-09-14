import { awardsOutputRules } from './awardsPrompt'
import { basicInfoOutputRules } from './basicInfoPrompt'
import { educationOutputRules } from './educationPrompt'
import { getProjectExperienceOutputRules } from './projectExperiencePrompt'
import { selfIntroOutputRules } from './selfIntroPrompt'
import { skillsOutputRules } from './skillsPrompt'
import { COMMON_OUTPUT_RULES, SYSTEM_PROMPT } from './shared'
import { getWorkExperienceOutputRules } from './workExperiencePrompt'

export interface ModulePromptContext {
  projectNames?: string[]
  workCompanyNames?: string[]
}

type OutputRulesBuilder = (context: ModulePromptContext) => string[]

const MODULE_OUTPUT_RULE_BUILDERS: Record<string, OutputRulesBuilder> = {
  basicInfo: () => basicInfoOutputRules,
  education: () => educationOutputRules,
  skills: () => skillsOutputRules,
  workExperience: (context) => getWorkExperienceOutputRules(context.workCompanyNames),
  projectExperience: (context) => getProjectExperienceOutputRules(context.projectNames),
  awards: () => awardsOutputRules,
  selfIntro: () => selfIntroOutputRules,
}

export { SYSTEM_PROMPT }

export function getModuleOutputRules(moduleKey: string, context: ModulePromptContext = {}): string {
  const builder = MODULE_OUTPUT_RULE_BUILDERS[moduleKey]
  const rules = builder ? builder(context) : COMMON_OUTPUT_RULES
  return rules.join('\n')
}