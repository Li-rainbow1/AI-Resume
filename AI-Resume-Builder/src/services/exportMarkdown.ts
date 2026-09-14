import type { useResumeStore } from '@/stores/resume'

type ResumeStore = ReturnType<typeof useResumeStore>

/**
 * Convert simple HTML (from RichEditor) to Markdown.
 * Handles: ul/ol/li, p, br, bold, italic, links, headings.
 */
export function htmlToMarkdown(html: string): string {
  if (!html || !html.trim()) return ''

  let md = html
    // Normalize line endings
    .replace(/\r\n?/g, '\n')

  // Convert <br> / <br/> / <br />
  md = md.replace(/<br\s*\/?>/gi, '\n')

  // Bold
  md = md.replace(/<(b|strong)>([\s\S]*?)<\/\1>/gi, '**$2**')
  // Italic
  md = md.replace(/<(i|em)>([\s\S]*?)<\/\1>/gi, '*$2*')
  // Links
  md = md.replace(/<a\s+href="([^"]*)"[^>]*>([\s\S]*?)<\/a>/gi, '[$2]($1)')

  // Headings h1-h6
  md = md.replace(/<h([1-6])[^>]*>([\s\S]*?)<\/h\1>/gi, (_match, level, content) => {
    return '#'.repeat(Number(level)) + ' ' + content.trim()
  })

  // Ordered lists
  md = md.replace(/<ol[^>]*>([\s\S]*?)<\/ol>/gi, (_match, inner: string) => {
    let index = 0
    return inner.replace(/<li[^>]*>([\s\S]*?)<\/li>/gi, (_m: string, content: string) => {
      index++
      return `${index}. ${content.trim()}`
    }) + '\n'
  })

  // Unordered lists
  md = md.replace(/<ul[^>]*>([\s\S]*?)<\/ul>/gi, (_match, inner: string) => {
    return inner.replace(/<li[^>]*>([\s\S]*?)<\/li>/gi, (_m: string, content: string) => {
      return `- ${content.trim()}`
    }) + '\n'
  })

  // Standalone <li> (fallback)
  md = md.replace(/<li[^>]*>([\s\S]*?)<\/li>/gi, '- $1')

  // Paragraphs → double newline
  md = md.replace(/<p[^>]*>([\s\S]*?)<\/p>/gi, '$1\n\n')

  // Divs → newline
  md = md.replace(/<div[^>]*>([\s\S]*?)<\/div>/gi, '$1\n')

  // Strip any remaining tags
  md = md.replace(/<[^>]+>/g, '')

  // Decode common HTML entities
  md = md
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")

  // Collapse multiple blank lines to at most two
  md = md.replace(/\n{3,}/g, '\n\n')

  return md.trim()
}

/**
 * 将月份统一格式化为 YYYY.MM。
 */
function formatMonth(value?: string): string {
  if (!value) return ''
  const match = value.match(/^(\d{4})-(\d{1,2})$/)
  const year = match?.[1]
  const month = match?.[2]
  return year && month ? `${year}.${month.padStart(2, '0')}` : value
}

/**
 * 生成与简历预览一致的日期范围。
 */
function dateRange(start?: string, end?: string): string {
  if (!start && !end) return ''
  const formattedStart = formatMonth(start)
  const formattedEnd = end ? formatMonth(end) : '至今'
  return formattedStart ? `${formattedStart} - ${formattedEnd}` : formattedEnd
}

/**
 * Append a section only if it has meaningful content.
 */
function pushSection(lines: string[], title: string, content: string) {
  const trimmed = content.trim()
  if (!trimmed) return
  lines.push(`## ${title}`, '', trimmed, '')
}

/**
 * Generate a full Markdown string from the resume store.
 * Respects module visibility and ordering.
 */
export function generateResumeMarkdown(store: ResumeStore): string {
  const lines: string[] = []

  // Iterate the module list in order, only include visible ones
  for (const mod of store.modules) {
    if (!mod.visible) continue

    switch (mod.key) {
      case 'basicInfo': {
        const b = store.basicInfo
        // Title
        lines.push(`# ${b.name || '未命名简历'}`, '')

        // Contact & personal info as a compact list
        const infoItems: string[] = []
        if (b.jobTitle) infoItems.push(`**求职意向**：${b.jobTitle}`)
        if (b.phone) infoItems.push(`**电话**：${b.phone}`)
        if (b.email) infoItems.push(`**邮箱**：${b.email}`)
        if (b.age) infoItems.push(`**年龄**：${b.age}`)
        if (b.gender) infoItems.push(`**性别**：${b.gender}`)
        if (b.educationLevel) infoItems.push(`**学历**：${b.educationLevel}`)
        if (b.workYears) infoItems.push(`**工作年限**：${b.workYears}`)
        if (b.currentCity) infoItems.push(`**所在城市**：${b.currentCity}`)
        if (b.location) infoItems.push(`**籍贯**：${b.location}`)
        if (b.expectedLocation) infoItems.push(`**期望工作地点**：${b.expectedLocation}`)
        if (b.expectedSalary) infoItems.push(`**期望薪资**：${b.expectedSalary}`)
        if (b.currentStatus) infoItems.push(`**当前状态**：${b.currentStatus}`)
        if (b.wechat) infoItems.push(`**微信**：${b.wechat}`)
        if (b.website) infoItems.push(`**个人网站**：${b.website}`)
        if (b.github) infoItems.push(`**GitHub**：${b.github}`)
        if (b.blog) infoItems.push(`**博客**：${b.blog}`)

        if (infoItems.length > 0) {
          lines.push(infoItems.map((item) => `- ${item}`).join('\n'), '')
        }
        break
      }

      case 'education': {
        const entries = store.educationList.filter(
          (e) => e.school || e.major || e.degree || e.startDate
        )
        if (entries.length === 0) break
        lines.push('## 教育经历', '')
        for (const e of entries) {
          const header = [e.school, e.college, e.major, e.degree].filter(Boolean).join(' · ')
          lines.push(`### ${header || '未填写'}`)
          const dr = dateRange(e.startDate, e.endDate)
          if (dr) lines.push(`> ${dr}`)
          if (e.gpa) lines.push(`- **GPA**：${e.gpa}`)
          if (e.location) lines.push(`- **地点**：${e.location}`)
          const desc = htmlToMarkdown(e.description)
          if (desc) lines.push('', desc)
          lines.push('')
        }
        break
      }

      case 'skills': {
        pushSection(lines, '专业技能', htmlToMarkdown(store.skills))
        break
      }

      case 'workExperience': {
        const entries = store.workList.filter(
          (w) => w.company || w.projectName || w.position || w.startDate || w.description
        )
        if (entries.length === 0) break
        lines.push('## 实习经历', '')
        for (const w of entries) {
          const header = [w.company, w.department, w.position].filter(Boolean).join(' · ')
          lines.push(`### ${header || '未填写'}`)
          const dr = dateRange(w.startDate, w.endDate)
          if (dr) lines.push(`> ${dr}`)
          if (w.projectName) lines.push(w.projectName)
          if (w.location) lines.push(`- **地点**：${w.location}`)
          const desc = htmlToMarkdown(w.description)
          if (desc) lines.push('', desc)
          lines.push('')
        }
        break
      }

      case 'projectExperience': {
        const entries = store.projectList.filter(
          (p) => p.name || p.role || p.startDate || p.description
        )
        if (entries.length === 0) break
        lines.push('## 项目经历', '')
        for (const p of entries) {
          const header = [p.name, p.role].filter(Boolean).join(' · ')
          lines.push(`### ${header || '未填写'}`)
          const dr = dateRange(p.startDate, p.endDate)
          if (dr) lines.push(`> ${dr}`)
          if (p.link) lines.push(`- **链接**：${p.link}`)
          const desc = htmlToMarkdown(p.description)
          if (desc) lines.push('', '**工作描述**', '', desc)
          lines.push('')
        }
        break
      }

      case 'awards': {
        const entries = store.awardList.filter((a) => a.name || a.date)
        if (entries.length === 0) break
        lines.push('## 荣誉奖项', '')
        for (const a of entries) {
          const header = [a.name, a.date].filter(Boolean).join(' — ')
          lines.push(`- **${header}**`)
          const desc = htmlToMarkdown(a.description)
          if (desc) lines.push(`  ${desc}`)
        }
        lines.push('')
        break
      }

      case 'selfIntro': {
        pushSection(lines, '个人简介', htmlToMarkdown(store.selfIntro))
        break
      }
    }
  }

  return lines.join('\n').trim() + '\n'
}

/**
 * Trigger a file download in the browser.
 */
export function downloadMarkdown(filename: string, content: string) {
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename.endsWith('.md') ? filename : `${filename}.md`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}
