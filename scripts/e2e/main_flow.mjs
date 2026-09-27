/* Browser main flow (brief §24.6 + V3 §18.3 three-end loop):
   scenario reset → customer app (mobile width): P3 booking with address+unit → paid P1 with tight window → lock order →
   technician calls in sick shortly before departure (demo controls) → P0 approval → schedule update → dispatcher cancel →
   new order uses the gap → technician app drives a job manually → customer order page shows status → handoff → dispatcher
   human queue take/resolve → customer sees the resolution → reload keeps state.
   Run from ./frontend (node_modules) with backend :8100 and vite :5174 up:  node ../scripts/e2e/main_flow.mjs */
import { chromium } from 'playwright'
const base = process.env.FRONTEND_URL ?? 'http://localhost:5174'
const api = process.env.BACKEND_URL ?? 'http://localhost:8100'
const out = process.env.SHOT_DIR ?? '.'
const LLM_WAIT = 90000   // real models (DeepSeek/Claude) take 10–20 s per turn; mock answers instantly
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a)
const get = async (p) => (await fetch(api + p)).json()
const check = (cond, msg) => { if (!cond) { throw new Error('CHECK FAILED: ' + msg) } log('✓', msg) }

const browser = await chromium.launch()
const errors = []
const newPage = async (w, h) => {
  const ctx = await browser.newContext({ viewport: { width: w, height: h } })
  const p = await ctx.newPage()
  p.on('pageerror', (e) => errors.push(String(e)))
  p.on('console', (m) => { if (m.type() === 'error' && !m.text().includes('tile')) errors.push(m.text()) })
  return p
}
const page = await newPage(1500, 1000)      // dispatcher
const mobile = await newPage(400, 820)      // customer app (phone width)
const techApp = await newPage(400, 900)     // technician app

/** Drive the chat until the confirm card appears: address modal (unit mandatory), urgency question, window pick, payment
 *  question (urgent only), contact form — in whatever order the assistant asks. `expedite`: answer "Yes, expedite", take the first
 *  💳 (expedited, ≤2 moves) window when one is offered, and confirm the simulated payment. Returns the created order. */
/** Things that need a person now open a blocking dialog (4th meeting 二.3 / "risks能够真实弹窗").
 *  Acknowledging closes the alert only — the case stays in the queue, which is what the test asserts elsewhere. */
let attentionSeen = false
async function dismissAttention() {
  const m = page.locator('[data-testid="attention-modal"]')
  if (!(await m.count())) return false
  if (!attentionSeen) { attentionSeen = true; log('  ⚠ attention dialog:', (await m.innerText()).split('\n').slice(0, 2).join(' · ')) }
  await m.locator('button:has-text("Acknowledge")').click()
  await m.waitFor({ state: 'detached', timeout: 5000 }).catch(() => undefined)
  return true
}

let slotShapeChecked = false
async function bookViaChat(sid, text, { unit = '05-01', expedite = false, pickProblem = null } = {}) {
  await mobile.goto(base + '/customer')
  await mobile.evaluate((s) => localStorage.setItem('techsched_customer_session', s), sid)
  await mobile.reload(); await mobile.waitForSelector('[data-testid="chat-input"]')
  // the chat auto-scrolls smoothly on every new message, which keeps buttons moving under Playwright's stability check
  await mobile.addStyleTag({ content: '*{scroll-behavior:auto !important;animation-duration:0s !important;transition:none !important}' })
  await mobile.fill('[data-testid="chat-input"]', text)
  await mobile.click('[data-testid="chat-send"]')
  for (let i = 0; i < 10; i++) {
    await mobile.waitForFunction(() => !document.body.innerText.includes('thinking…'), { timeout: LLM_WAIT })
    if (await mobile.locator('[data-testid="opt-confirm"]').count()) break
    if (pickProblem && await mobile.locator(`[data-testid="opt-catalog"]:has-text("${pickProblem}")`).count() && !(await mobile.locator('[data-testid="opt-address"]').count())) {
      await mobile.click(`[data-testid="opt-catalog"]:has-text("${pickProblem}")`); continue
    }
    if (await mobile.locator('[data-testid="opt-address"]').count()) {
      await mobile.click('[data-testid="opt-address"]')
      await mobile.waitForSelector('[data-testid="address-modal"]')
      if (!(await mobile.locator('[data-testid="address-modal"] >> text=Selected:').count())) {   // no geocoded point yet → search
        await mobile.fill('[data-testid="address-search"]', 'Hougang Mall')
        await mobile.waitForSelector('[data-testid="address-result"]', { timeout: 30000 })
        await mobile.click('[data-testid="address-result"]')
      }
      await mobile.fill('[data-testid="address-unit"]', unit)
      await mobile.click('[data-testid="address-confirm"]')
      continue
    }
    if (await mobile.locator('[data-testid="contact-form"]').count()) {
      await mobile.fill('input[placeholder="Your name"]', 'E2E Customer'); await mobile.fill('input[placeholder="Phone"]', '+65 9000 0001')
      await mobile.click('[data-testid="contact-form"] button'); continue
    }
    // 4th meeting 加急逻辑: "send someone now?" first, then the whole day with free / purchasable slots
    if (await mobile.locator('[data-testid="opt-expedite-now"]').count()) { await mobile.click(`[data-testid="opt-expedite-now"][data-now="${expedite ? 1 : 0}"]`); continue }
    if (await mobile.locator('[data-testid="opt-payment"]').count()) { await mobile.click(`[data-testid="opt-payment"][data-paid="${expedite ? 1 : 0}"]`); continue }
    if (await mobile.locator('[data-testid="opt-window"]').count()) {
      const all = mobile.locator('[data-testid="opt-window"]')
      const free = mobile.locator('[data-testid="opt-window"][data-state="available"]')
      const paidSlot = mobile.locator('[data-testid="opt-window"][data-state="paid"]')
      if (!slotShapeChecked) {
        slotShapeChecked = true
        check(await all.count() > await free.count() || await all.count() >= 8,
          `the day is shown whole: ${await all.count()} slot(s), ${await free.count()} free, ${await paidSlot.count()} purchasable`)
      }
      if (expedite && await paidSlot.count()) { log(`  💳 purchasable slot: ${await paidSlot.first().innerText()}`); await paidSlot.first().click() }
      else { await free.first().click() }
      continue
    }
    if (await mobile.locator('[data-testid="opt-catalog"]').count()) { await mobile.click('[data-testid="opt-catalog"]'); continue }
    throw new Error('chat did not progress: ' + (await mobile.locator('body').innerText()).slice(-400))
  }
  await mobile.waitForSelector('[data-testid="opt-confirm"]', { timeout: LLM_WAIT })
  // the card must show the paid expedite WITHOUT leaking the internal grade (the P1 fact is asserted via the API below)
  if (expedite) { await mobile.waitForSelector('[data-testid="card-paid"]:has-text("yes")', { timeout: LLM_WAIT }) }
  const cardText = await mobile.locator('[data-testid="confirm-card"]').innerText()
  check(!/\bP[0-3]\b/.test(cardText) && !/complexity/i.test(cardText), `confirm card carries no internal grading: "${cardText.replace(/\s+/g, ' ').slice(0, 90)}…"`)
  await mobile.click('[data-testid="opt-confirm"]')
  await mobile.waitForSelector('text=/Order wo_[a-z0-9]+ created/', { timeout: LLM_WAIT })
  const orders = await get(`/api/orders?customer_ref=${sid}`)
  return orders[0]
}

// 0. one-click "Reset demo" (scenario main at 08:30; customer / technician tabs in the same browser reset themselves)
await page.goto(base + '/')
await dismissAttention()
await page.waitForSelector('text=Technician timeline')
page.once('dialog', (d) => d.accept())
await dismissAttention()
await page.click('[data-testid="reset-demo"]')
await page.waitForSelector('text=Demo reset · scenario main', { timeout: 30000 })
await page.click('[data-testid="open-demo"]')
await page.waitForSelector('[data-testid="demo-controls"] >> text=main · loaded')   // scenario drawer still lists the three load profiles
await page.click('[data-testid="demo-controls"] button:has-text("close")')
const health = await get('/health')
check(health.catalog_configured && health.catalog_items === 46, 'catalog configured from real CSV (46 items)')
check(!(await page.locator('button:has-text("Inject Event")').count()), 'no Inject Event button on the main dispatcher surface')
await page.screenshot({ path: `${out}/01-dashboard-baseline.png` })

// 1. Customer app: P3 order with address + unit (mobile width)
const p3 = await bookViaChat('sess_e2e_p3', 'My fridge is making a loud noise, I am at Hougang Mall, please come around 2pm', { unit: '04-12' })
check(p3.effective_priority === 'P3' && p3.scheduling_status === 'ASSIGNED', `P3 order ${p3.id} created via the mobile chat and auto-assigned to ${p3.technician_name}`)
const p3full = await get(`/api/orders/${p3.id}`)
check(p3full.address && p3full.address.unit_number === '04-12', `order carries the confirmed address + unit (#${p3full.address.unit_number})`)
await mobile.screenshot({ path: `${out}/02-chat-p3.png` })

// 2. "Send someone now" (paid, simulated) → P0 by policy (4th meeting: 由于安全问题P0), scheduled as early as possible
const p1o = await bookViaChat('sess_e2e_p1', 'Aircon not cooling at all in Paya Lebar, my name is Priya, phone 91110002', { unit: '11-02', expedite: true })
const p1 = await get(`/api/orders/${p1o.id}`)
check(p1.effective_priority === 'P0' && p1.paid_expedite && p1.expedite_now,
  `"send someone now" order ${p1.id} entered at ${p1.effective_priority} (paid, simulated), promised within ${p1.window_start.slice(11, 16)}–${p1.window_end.slice(11, 16)}`)
// P0 authority (config/policy.yaml): up to 5 P2/P3 may move, but any movement needs a dispatcher — so it lands in review
const p0plans = p1.plans.filter((p) => ['PENDING_REVIEW', 'COMMITTED'].includes(p.status))
check(p0plans.length > 0 && p0plans.every((p) => p.affected_order_ids.length <= 5),
  `P0 produced ${p0plans.length} plan(s), each within the P0 limit of 5: ${p0plans.map((p) => `${p.status} affects ${p.affected_order_ids.length}`).join(', ')}`)
const needsReview = p0plans.find((p) => p.status === 'PENDING_REVIEW')
if (needsReview) {
  check(needsReview.policy_check.decision === 'manual' && needsReview.policy_check.reasons.some((r) => r.includes('P0')),
    `a P0 that moves other work waits for a dispatcher: "${needsReview.policy_check.reasons[0]}"`)
  await fetch(`${api}/api/plans/${needsReview.id}/approve`, { method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ actor: 'e2e dispatcher', reason: 'paid "send someone now"' }) })
  const p1b = await get(`/api/orders/${p1.id}`)
  check(p1b.scheduling_status === 'ASSIGNED', `after approval the P0 is scheduled: ${p1b.technician_name} starts ${p1b.assignment?.service_start?.slice(11, 16)}`)
}
const over = p1.plans.find((p) => p.status === 'OVER_LIMIT')
if (over) check(!over.approvable, `over-limit plan (${over.affected_order_ids.length} affected) shown as explanation only`)
await mobile.screenshot({ path: `${out}/03-chat-p1.png` })

// 3. Lock order at Punggol (10:30–10:40) → Farah (tech_06). Just before her departure she calls in sick from the technician app:
//    <30 min to the deadline → P0 by the cancellation rule → the only plan must move a P3 → dispatcher approval required.
const lock = await bookViaChat('sess_e2e_lock', 'My door lock is damaged and needs replacement, Punggol, between 10:30 and 10:40, my name is Lee Ann phone 91110004', { unit: '02-08' })
check(lock.scheduling_status === 'ASSIGNED' && lock.technician_id === 'tech_06', `lock order ${lock.id} assigned to Farah (tech_06), departs ${lock.assignment.departure.slice(11, 16)}`)
await page.goto(base + '/'); await page.waitForSelector('text=Technician timeline')
await dismissAttention()
const depMin = parseInt(lock.assignment.departure.slice(11, 13)) * 60 + parseInt(lock.assignment.departure.slice(14, 16))
const nowMin = 8 * 60 + 30
const stepsTo = depMin - 1 - nowMin   // stop one minute before Farah departs
for (let left = stepsTo; left > 0;) { const m = left >= 15 ? 15 : left >= 5 ? 5 : 1; await page.click(`button:has-text("+${m}m")`); await page.waitForTimeout(350); left -= m }
await page.waitForFunction((t) => document.body.innerText.includes(t), `${String(Math.floor((depMin - 1) / 60)).padStart(2, '0')}:${String((depMin - 1) % 60).padStart(2, '0')}`, { timeout: 60000 })
await techApp.goto(base + '/technician'); await techApp.waitForSelector('[data-testid="tech-login"]')
await techApp.selectOption('[data-testid="tech-login"]', 'tech_06'); await techApp.waitForSelector('text=Farah Osman', { timeout: 15000 })
await techApp.click('[data-testid="leave-open"]'); await techApp.waitForSelector('[data-testid="leave-send"]')
await techApp.click('[data-testid="leave-send"]'); await techApp.waitForSelector('text=Leave recorded', { timeout: 20000 })
await techApp.screenshot({ path: `${out}/04a-tech-leave.png` })
let risks = await get('/api/risks')
check(risks.some((r) => r.type === 'TECHNICIAN_CANCELLED' && r.target_order_id === lock.id && r.severity === 'P0'), `technician leave (from the technician app) <30 min before deadline → ${lock.id} is P0 (customer demand kept)`)
const w19 = await get('/api/orders/wo_019')
check(w19.scheduling_status === 'ASSIGNED' && w19.technician_id !== 'tech_06', `wo_019 (>120 min left → P2) re-dispatched with zero disturbance to ${w19.technician_name}`)
await page.screenshot({ path: `${out}/04-tech-cancel.png` })
await page.waitForSelector(`[data-testid="plan-card"][data-status="PENDING_REVIEW"][data-target="${lock.id}"]`, { timeout: 20000 })
const pend = (await get('/api/plans?status=PENDING_REVIEW')).filter((p) => p.target_order_id === lock.id)
check(pend.length >= 1 && pend[0].affected_order_ids.length >= 1 && pend[0].policy_check.decision === 'manual', `P0 plan affects ${pend[0].affected_order_ids.length} P3 order(s) (${pend[0].affected_order_ids.join(',')}) → manual by rule even at score ${pend[0].decision_score?.toFixed(1)}`)
check(attentionSeen || (await page.locator('[data-testid="attention-modal"]').count()) > 0,
  'a case needing a person opened a blocking dialog instead of only sitting in a tab')
await dismissAttention()
const kpi = await page.locator('[data-testid="kpi-pending-human"]').innerText()
check(/[1-9]/.test(kpi), `pending-human KPI shows the policy-required case (${kpi.replace(/\s+/g, ' ')})`)
await page.screenshot({ path: `${out}/05-p0-review.png` })
const card = page.locator(`[data-testid="plan-card"][data-status="PENDING_REVIEW"][data-target="${lock.id}"]`).first()
await card.locator('[data-testid="approve"]').click()
await page.waitForSelector('text=Approve:', { timeout: 20000 })
await page.waitForTimeout(3500)
const lock2 = await get(`/api/orders/${lock.id}`)
check(lock2.scheduling_status === 'ASSIGNED' && lock2.technician_id !== 'tech_06', `P0 plan approved & committed: ${lock2.technician_name} starts ${lock2.assignment.service_start.slice(11, 16)} (window end ${lock2.window_end.slice(11, 16)})`)
const moved = await get(`/api/orders/${pend[0].affected_order_ids[0]}`)
check(moved.scheduling_status === 'ASSIGNED' && moved.assignment.service_start <= moved.window_end, `moved order ${moved.id} still inside its own window (${moved.technician_name} @ ${moved.assignment.service_start.slice(11, 16)})`)
const sched = await get('/api/schedules/current')
check(sched.version.reason.startsWith('approved plan'), `schedule updated to v${sched.version.id} (${sched.version.reason})`)
await page.screenshot({ path: `${out}/06-p0-approved.png` })

// 4. Cancel another undeparted order from the dashboard; departed one must be refused
const open = (await get('/api/orders')).filter((o) => o.lifecycle_status === 'OPEN' && o.assignment && o.id !== 'wo_007')
const victim = open[open.length - 1]
await dismissAttention()
await page.click(`[data-testid="order-row"][data-order="${victim.id}"]`)
await page.waitForSelector(`[data-testid="detail-order-id"]:has-text("${victim.id}")`)
await page.click('[data-testid="cancel-order"]')
await page.waitForSelector('text=Cancel (dispatcher on behalf of customer) ok')
const v2 = await get(`/api/orders/${victim.id}`)
check(v2.lifecycle_status === 'CANCELLED' && !v2.assignment, `${victim.id} cancelled (undeparted) and technician ${victim.technician_name} released`)
const departed = (await get('/api/orders')).find((o) => ['EN_ROUTE', 'ARRIVED', 'IN_PROGRESS'].includes(o.lifecycle_status))
if (departed) {
  const r = await fetch(`${api}/api/orders/${departed.id}/cancel`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ customer_ref: departed.customer_ref }) })
  check(r.status === 409, `cancel of departed ${departed.id} (${departed.lifecycle_status}) refused with 409`)
}
await page.click('button:has-text("close")')

// 5. New order (chat) uses the freed gap: same location/trade/window as the cancelled one
const ws = victim.window_start.slice(11, 16), we = victim.window_end.slice(11, 16)
const gap = await bookViaChat('sess_e2e_gap', `${victim.catalog_snapshot.trade_type} ${victim.catalog_snapshot.problem_name} at ${victim.location.name}, between ${ws} and ${we}, name Gap Customer phone 90001234`, { unit: '01-01', pickProblem: victim.catalog_snapshot.problem_name })
check(gap.scheduling_status === 'ASSIGNED', `new order ${gap.id} placed into the freed capacity (${gap.technician_name} @ ${gap.assignment.service_start.slice(11, 16)})`)

// 6. Technician app drives the P3 order manually; the customer's order page reflects it (tracking after departure)
const p3now = await get(`/api/orders/${p3.id}`)
await techApp.goto(base + '/technician'); await techApp.waitForSelector('[data-testid="tech-login"]')
await techApp.selectOption('[data-testid="tech-login"]', p3now.technician_id); await techApp.waitForSelector(`text=${p3now.technician_name}`, { timeout: 15000 })
// the simulator-control panel sits outside the phone frame now; make sure the switch actually landed before driving
const modeBtn = techApp.locator('[data-testid="mode-toggle"]')
await modeBtn.waitFor({ state: 'visible', timeout: 15000 })
for (let i = 0; i < 5; i++) {
  if ((await get(`/api/technician/${p3now.technician_id}/today`)).technician.sim_mode === 'manual') break
  if ((await modeBtn.innerText()).includes('Take manual')) await modeBtn.click()
  await techApp.waitForTimeout(600)
}
check((await get(`/api/technician/${p3now.technician_id}/today`)).technician.sim_mode === 'manual',
  'technician switched to manual mode from the simulator-control panel outside the phone frame')
const nextAct = await techApp.locator('[data-testid^="act-"]').first().getAttribute('data-testid')
if (nextAct === 'act-depart') {
  // a technician may not leave before the planned departure (policy execution.depart_grace_minutes)
  const notYet = techApp.locator('[data-testid="depart-not-yet"]')
  if (await notYet.count()) {
    check(!(await techApp.locator('[data-testid="act-depart"]').isEnabled()), `depart is blocked before the planned time: "${(await notYet.innerText()).trim()}"`)
    const job = (await get(`/api/technician/${p3now.technician_id}/today`)).jobs.find((j) => j.next_action === 'depart')
    const due = Math.max(1, Math.round((new Date(job.planned.departure) - new Date((await get('/api/demo/clock')).now)) / 60000))
    await fetch(`${api}/api/demo/clock/advance`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ minutes: due }) })
    await techApp.waitForSelector('[data-testid="act-depart"]:not([disabled])', { timeout: 20000 })
    log(`  advanced ${due} sim min to the planned departure ${job.planned.departure.slice(11, 16)}`)
  }
}
await techApp.click(`[data-testid="${nextAct}"]`)
await techApp.waitForSelector('[data-testid="tech-msg"]', { timeout: 20000 })
const techOrder = (await get(`/api/technician/${p3now.technician_id}/today`))
const driven = techOrder.jobs.find((j) => ['EN_ROUTE', 'ARRIVED', 'IN_PROGRESS', 'COMPLETED'].includes(j.lifecycle_status))
check(driven, `technician app moved ${driven?.order_id} to ${driven?.lifecycle_status} via ${nextAct} (manual mode, single driver)`)

// 6a. The dispatcher map must show the drive shrinking, not a static full-length line (4th meeting 三.1).
// Measured on the drawn SVG so a wrong leg or a frozen line fails here, not only in a screenshot.
{
  const enRoute = (await get('/api/positions')).technicians.find((t) => t.moving && (t.leg_progress ?? 0) < 0.8)
  if (enRoute) {
    check(enRoute.current_leg && enRoute.current_leg.order_id === enRoute.next_destination?.order_id,
      `position names the leg actually being driven: ${enRoute.name} → ${enRoute.next_destination?.name} (${enRoute.current_leg?.order_id})`)
    await page.goto(base + '/'); await page.waitForSelector('text=Technician timeline'); await dismissAttention()
    await page.locator(`text=${enRoute.name}`).first().click(); await page.waitForTimeout(2500)
    const measure = () => page.evaluate(() => {
      const paths = [...document.querySelectorAll('.leaflet-overlay-pane path')]
      const len = (c) => paths.filter((p) => (p.getAttribute('stroke') || '').toLowerCase() === c).reduce((a, p) => a + p.getTotalLength(), 0)
      return { remaining: Math.round(len('#2563eb') + len('#7c3aed')), driven: Math.round(len('#9ca3af')) }
    })
    const before = await measure()
    await fetch(`${api}/api/demo/clock/advance`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ minutes: 5 }) })
    await page.waitForTimeout(3000)
    const after = await measure()
    check(after.remaining < before.remaining && after.driven > before.driven,
      `the drawn route follows the technician: remaining ${before.remaining}→${after.remaining}px, already driven ${before.driven}→${after.driven}px`)
  } else {
    log('  (nobody mid-drive at this point — route-shrink check skipped)')
  }
}
await techApp.screenshot({ path: `${out}/07-technician-app.png`, fullPage: true })
await mobile.goto(base + '/customer'); await mobile.evaluate((s) => localStorage.setItem('techsched_customer_session', s), 'sess_e2e_p3')
await mobile.goto(`${base}/customer/orders/${p3.id}`); await mobile.waitForSelector('[data-testid="order-status-text"]', { timeout: 15000 })
const statusText = await mobile.locator('[data-testid="order-status-text"]').innerText()
check(statusText.length > 5, `customer order page shows live status: “${statusText}”`)
if (driven && driven.order_id === p3.id && driven.lifecycle_status === 'EN_ROUTE') { await mobile.waitForSelector('[data-testid="tracking-map"]', { timeout: 10000 }); log('✓ tracking map visible after departure') }

// 6b. Post-order expedite from the order page = simulated payment + automatic move to the earliest start (P1 rules, ≤2 P3 moved)
const p3before = await get(`/api/orders/${p3.id}`)
if (p3before.lifecycle_status === 'OPEN' && p3before.assignment) {
  await mobile.waitForSelector('[data-testid="expedite-preview"]', { timeout: 15000 })
  await mobile.waitForFunction(() => !document.body.innerText.includes('Checking the earliest possible start'), { timeout: 30000 })
  log('  preview:', (await mobile.locator('[data-testid="expedite-preview"]').innerText()).trim())
  await mobile.click('[data-testid="act-expedite"]')
  await mobile.waitForSelector('text=Your order is expedited', { timeout: 30000 })
  const p3x = await get(`/api/orders/${p3.id}`)
  const banner = (await mobile.locator('[data-testid="expedite-banner"]').innerText()).trim()
  check(p3x.effective_priority === 'P1' && p3x.paid_expedite, `order page Expedite → “${banner.slice(0, 110)}…” · API effective_priority ${p3x.effective_priority}`)
  const summary = (await mobile.locator('[data-testid="expedite-summary"]').innerText()).trim()
  check(summary.startsWith('Expedited') && !summary.includes('P1'), `order page info line: “${summary}”`)
  if (banner.includes('A dispatcher is confirming')) {
    // score ≤ 70 → review queue: the original slot stays until the dispatcher approves; approval applies plan + window together
    await page.goto(base + '/'); await page.waitForSelector('text=Technician timeline')
await dismissAttention()
    const sel = `[data-testid="plan-card"][data-status="PENDING_REVIEW"][data-target="${p3.id}"]`
    await page.waitForSelector(sel, { timeout: 20000 })
    await dismissAttention()
    await page.locator(sel).first().locator('[data-testid="approve"]').click()
    await page.waitForSelector('text=Approve:', { timeout: 20000 })
    await mobile.waitForSelector('text=New appointment:', { timeout: 20000 })
  }
  const p3m = await get(`/api/orders/${p3.id}`)
  if (p3m.assignment && p3m.assignment.service_start < p3before.assignment.service_start) {
    check(p3m.window_start === p3m.assignment.service_start, `expedited order moved earlier: planned start ${p3m.assignment.service_start.slice(11, 16)} (was ${p3before.assignment.service_start.slice(11, 16)}), window moved with it (${p3m.window_start.slice(11, 16)}–${p3m.window_end.slice(11, 16)})`)
    await mobile.waitForSelector('[data-testid="act-keep-original"]', { timeout: 15000 })
    log('✓ "Keep original time" offered after the move')
  } else {
    check(p3m.effective_priority === 'P1', `no earlier slot today: time unchanged, priority P1 kept (${p3m.assignment?.service_start.slice(11, 16)})`)
  }
} else {
  log('  (p3 already departed — post-order expedite skipped in this run)')
}
await mobile.screenshot({ path: `${out}/08-customer-order.png`, fullPage: true })

// 7. Handoff → dispatcher human queue → take + resolve; the resolution reaches the customer
// (the free-text reply box was removed: the customer app is not a chat console for the dispatcher)
await mobile.click('[data-testid="act-handoff"]'); await mobile.waitForSelector('[data-testid="order-msg"]', { timeout: 20000 })
await page.goto(base + '/'); await page.waitForSelector('text=Technician timeline')
await dismissAttention()
await dismissAttention()
await page.click('[data-testid="tab-human"]'); await page.waitForSelector('[data-testid="human-case"]', { timeout: 15000 })
await page.locator('[data-testid="human-case"]').first().locator('[data-testid="case-toggle"]').click()
check(!(await page.locator('[data-testid="case-reply"]').count()), 'no free-text reply box to the customer in the human queue')
await page.click('[data-testid="case-take"]'); await page.waitForSelector('text=Take: done')
await page.screenshot({ path: `${out}/09-human-queue.png` })
await page.fill('[data-testid="case-resolution"]', 'A coordinator called the customer; the technician is on the way.')
await page.click('[data-testid="case-resolve"]'); await page.waitForSelector('text=Resolve: done')
await mobile.reload(); await mobile.waitForSelector('text=the technician is on the way', { timeout: 15000 })
check(true, 'the dispatcher resolution reaches the customer order page (support case attached, no repetition)')

// 7b. Agents tab: the reasoning timeline, the playbooks and the scorecard are on the main surface, not in a dev drawer
await page.click('[data-testid="tab-agents"]')
await page.click('[data-testid="agent-view-skills"]')
await page.waitForSelector('[data-testid="skill-toggle"]', { timeout: 10000 })
const skillCount = await page.locator('[data-testid="skill-toggle"]').count()
check(skillCount === 5, `five agent playbooks are readable in the UI (${skillCount})`)
// the recovery card, matched on its badge — several descriptions mention the word "recovery"
const recoveryCard = page.locator('[data-testid="skill-toggle"]').filter({ has: page.locator('span.badge', { hasText: /^recovery$/ }) })
await recoveryCard.click()
const withheld = page.locator('[data-testid="skill-withheld"]')
await withheld.waitFor({ timeout: 5000 })
check(await withheld.locator('span', { hasText: 'submit_break' }).count() === 1,
  'the recovery playbook visibly withholds submit_break — the fence between agents is shown, not just described')
await page.screenshot({ path: `${out}/11-agent-skills.png` })

await page.click('[data-testid="agent-view-scorecard"]')
await page.waitForSelector('text=Settled without a human', { timeout: 10000 })
const scorecard = await get('/api/agent-scorecard')
check(scorecard.transparency.rate_pct === 100, `every agent step recorded why it acted (${scorecard.transparency.with_reason}/${scorecard.transparency.steps})`)
check(scorecard.skills_loaded.length === 5, 'the scorecard reads the same five playbooks the runtime loaded')
await page.screenshot({ path: `${out}/12-agent-scorecard.png` })

await page.click('[data-testid="agent-view-reasoning"]')
const tasks = await get('/api/agent-tasks')
if (tasks.length) {
  await page.waitForSelector('[data-testid="reasoning-toggle"]', { timeout: 10000 })
  await page.locator('[data-testid="reasoning-toggle"]').first().click()
  await page.waitForTimeout(1200)
  const body = await page.locator('[data-testid="agent-reasoning-task"]').first().innerText()
  check(body.includes('💭') || body.includes('playbook'), 'the reasoning timeline shows the agent\'s own reason for each step')
} else {
  log('  (no agent task in this run — the fast path placed every order; timeline shows its empty state)')
  check(await page.locator('text=No agent has been asked to think yet').isVisible(), 'the reasoning timeline explains when a task appears')
}
await page.screenshot({ path: `${out}/13-agent-reasoning.png` })
await page.click('[data-testid="tab-plans"]')

// 8. Reload: state persists
await page.goto(base + '/'); await page.waitForSelector('text=Technician timeline'); await page.waitForTimeout(2500)
await dismissAttention()
const after = await get('/api/schedules/current')
check(after.kpis.cancelled >= 1 && after.version.id === (await get('/api/schedules/current')).version.id, `after reload: schedule v${after.version.id}, ${after.kpis.assigned} assigned, ${after.kpis.cancelled} cancelled (DB-backed)`)
await page.screenshot({ path: `${out}/10-final.png`, fullPage: true })
log('console/page errors:', errors.length ? errors : 'none')
await browser.close()
if (errors.length) process.exit(2)
