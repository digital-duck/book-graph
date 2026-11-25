// node smoke.mjs <baseURL> <out.json>: what a reader sees on the home page, one chapter page (+ navigator), and its content panel.
import { chromium } from 'playwright'
import fs from 'node:fs'
const [base, outFile] = process.argv.slice(2)
const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } })
const errors = []
page.on('pageerror', e => errors.push(String(e)))
page.on('console', m => { if (m.type() === 'error') errors.push(m.text()) })
const out = { errors }
await page.goto(base, { waitUntil: 'networkidle' })
await page.waitForSelector('.cb-card', { timeout: 15000 })
out.title = await page.title()
out.home = await page.locator('#app').innerText()
out.cards = await page.locator('.cb-card__title').allInnerTexts()
const first = await page.evaluate(async () => (await (await fetch('domains/catalog.json')).json())[0].id)
await page.goto(`${base}#/domain/${first}`, { waitUntil: 'networkidle' })
const frameEl = await page.waitForSelector('iframe.cb-graph-viewer__frame', { timeout: 15000 })
const frame = await frameEl.contentFrame()
await frame.waitForSelector('canvas', { timeout: 15000 })
out.graphTitle = await frame.title()
out.graphNodes = await frame.evaluate(() => (typeof RAW !== 'undefined' ? RAW.nodes.length : -1))
out.domain = await page.locator('#app').innerText()
// select the chapter's capstone, as clicking it in the graph does, and read the content panel
const cap = await page.evaluate(async (id) => (await (await fetch('domains/catalog.json')).json()).find(d => d.id === id).capstone, first)
await page.fill('.cb-graph-topbar__input', cap)                      // the app's own search: selectNode in the navigator
await page.press('.cb-graph-topbar__input', 'Enter')
await page.waitForTimeout(3000)
out.afterSelect = await page.locator('#app').innerText()
const iframes = page.frames().filter(f => f !== page.mainFrame() && f !== frame)
out.contentFrames = []
for (const f of iframes) { try { out.contentFrames.push({ url: f.url().replace(/^https?:\/\/[^/]+/, ''), text: (await f.locator('body').innerText()).slice(0, 400) }) } catch {} }
fs.writeFileSync(outFile, JSON.stringify(out, null, 1))
await browser.close()
