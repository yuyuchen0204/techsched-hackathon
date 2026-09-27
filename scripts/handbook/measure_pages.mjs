// Pass 1 of the two-pass build: measure which PDF page each contents entry lands on.
// Chromium fragments a fixed-height multi-column container with the same engine it uses for
// print pagination, so one column == one page as long as the break rules are translated.
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, resolve } from 'node:path'
import { writeFileSync } from 'node:fs'
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const { chromium } = await import(pathToFileURL(resolve(ROOT, 'frontend/node_modules/playwright/index.mjs')).href)

const PX = 96 / 25.4                       // CSS px per mm
const W = Math.round((210 - 14 - 14) * PX) // A4 width  minus @page left/right margins
const H = Math.round((297 - 15 - 14) * PX) // A4 height minus @page top/bottom margins
const GAP = 40

const b = await chromium.launch()
const p = await b.newPage({ viewport: { width: W + 200, height: H } })
await p.emulateMedia({ media: 'print' })
const LANG = process.env.HANDBOOK_LANG || 'zh'
const DOC = process.env.HANDBOOK_DOC || 'product-handbook'
const SUB = `${DOC}.${LANG}/`
await p.goto(pathToFileURL(resolve(ROOT, `docs/.handbook-build/${SUB}handbook.html`)).href, { waitUntil: 'networkidle' })
await p.addStyleTag({ content: `
  html,body{ margin:0; padding:0; }
  body{ width:${W}px; height:${H}px; column-width:${W}px; column-gap:${GAP}px; column-fill:auto; }
  h1{ break-before:column !important; }
  section.cover{ break-after:column !important; }
  section.toc{ break-after:column !important; }
` })
await p.waitForTimeout(600)
const out = await p.evaluate(({ W, GAP }) => {
  const originX = document.body.getBoundingClientRect().x + scrollX
  const col = el => Math.floor((el.getBoundingClientRect().x + scrollX - originX + 2) / (W + GAP))
  const pages = {}
  document.querySelectorAll('[id^="ch-"],[id^="part-"],[id^="app-"]').forEach(el => { pages[el.id] = col(el) + 1 })
  return { pages, columns: Math.ceil((document.body.scrollWidth + GAP) / (W + GAP)) }
}, { W, GAP })
writeFileSync(resolve(ROOT, `docs/.handbook-build/${SUB}pages.json`), JSON.stringify(out.pages, null, 1))
console.log(`measured ${Object.keys(out.pages).length} entries over ~${out.columns} pages`)
await b.close()
