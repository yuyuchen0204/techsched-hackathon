// Browser smoke: opens dashboard + customer pages, takes screenshots, reports console errors.
import { chromium } from 'playwright'
const base = process.env.FRONTEND_URL ?? 'http://localhost:5174'
const out = process.env.SHOT_DIR ?? '/tmp'
const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1500, height: 950 } })
const errors = []
page.on('pageerror', (e) => errors.push(String(e)))
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
await page.goto(base + '/')
await page.waitForSelector('text=Technician timeline')
await page.waitForTimeout(2500)
await page.screenshot({ path: `${out}/dashboard.png`, fullPage: true })
await page.goto(base + '/customer')
await page.waitForSelector('text=Customer chat')
await page.waitForTimeout(1500)
await page.screenshot({ path: `${out}/customer.png`, fullPage: true })
console.log('errors:', errors)
await browser.close()
