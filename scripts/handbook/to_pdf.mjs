// Renders the print-styled HTML to docs/TechSched-产品手册.pdf with Chromium (Playwright, already a frontend devDependency).
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, resolve } from 'node:path'
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const { chromium } = await import(pathToFileURL(resolve(ROOT, 'frontend/node_modules/playwright/index.mjs')).href)
const LANG = process.env.HANDBOOK_LANG || 'zh'
const DOC = process.env.HANDBOOK_DOC || 'product-handbook'
const SUB = `${DOC}.${LANG}/`
const SRC = pathToFileURL(resolve(ROOT, `docs/.handbook-build/${SUB}handbook.html`)).href
const NAMES = { 'product-handbook.zh': 'TechSched-产品手册', 'product-handbook.en': 'TechSched-Product-Handbook',
                'technical-design.zh': 'TechSched-技术设计文档', 'technical-design.en': 'TechSched-Technical-Design',
                'business-proposal.zh': 'TechSched-商业计划书', 'business-proposal.en': 'TechSched-Business-Proposal' }
const OUT = resolve(ROOT, `docs/${NAMES[`${DOC}.${LANG}`]}.pdf`)
const b = await chromium.launch()
const p = await b.newPage()
await p.goto(SRC, { waitUntil: 'networkidle' })
await p.waitForTimeout(800)
const style = `font-family:"PingFang SC",-apple-system,Helvetica,Arial,sans-serif;font-size:7pt;color:#9aa3b0;width:100%;padding:0 15mm;`
await p.pdf({
  path: OUT,
  format: 'A4',
  printBackground: true,
  displayHeaderFooter: true,
  headerTemplate: `<div style="${style}display:flex;justify-content:space-between;"><span>${{ 'product-handbook.zh': 'TechSched 产品手册', 'product-handbook.en': 'TechSched Product Handbook', 'technical-design.zh': 'TechSched 技术设计文档', 'technical-design.en': 'TechSched Technical Design', 'business-proposal.zh': 'TechSched 商业计划书', 'business-proposal.en': 'TechSched Business Proposal' }[`${DOC}.${LANG}`]}</span><span>V3 · policy 2026-09-16-v3</span></div>`,
  footerTemplate: `<div style="${style}text-align:center;"><span class="pageNumber"></span> / <span class="totalPages"></span></div>`,
  margin: { top: '17mm', bottom: '16mm', left: '15mm', right: '15mm' },
  outline: true,
  tagged: true,
})
await b.close()
console.log('pdf written', OUT)
