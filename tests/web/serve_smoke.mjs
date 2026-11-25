// node serve_smoke.mjs <siteURL> <chapterId> <node> <out.json>: in a real browser, Generate a node from the content panel,
// wait for the log to finish, read the page that appears, then Export PDF (the API behind the app, bookgraph serve).
import { chromium } from 'playwright'
import fs from 'node:fs'
const [base, chapter, node, outFile] = process.argv.slice(2)
const browser = await chromium.launch()
const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } })
const page = await ctx.newPage()
const out = { errors: [] }
page.on('pageerror', e => out.errors.push(String(e)))
await page.goto(`${base}#/domain/${chapter}`, { waitUntil: 'networkidle' })
const frame = await (await page.waitForSelector('iframe.cb-graph-viewer__frame')).contentFrame()
await frame.waitForSelector('canvas')
await page.fill('.cb-graph-topbar__input', node)
await page.press('.cb-graph-topbar__input', 'Enter')
await page.waitForTimeout(1500)
out.before = page.frames().filter(f => f !== page.mainFrame() && f !== frame).map(f => f.url())
await page.click('.cb-ide-gen-btn')
await page.waitForFunction(() => /✓ Done|✗/.test(document.querySelector('.cb-ide-log')?.textContent || ''), null, { timeout: 120000 })
out.log = await page.locator('.cb-ide-log').innerText()
await page.waitForTimeout(1500)
out.content = []
for (const f of page.frames().filter(f => f !== page.mainFrame() && f !== frame)) {
  try { out.content.push({ url: f.url().replace(/^https?:\/\/[^/]+/, ''), text: (await f.locator('body').innerText()).slice(0, 300) }) } catch {}
}
const pdfReq = ctx.waitForEvent('request', { predicate: r => /\.pdf$/.test(r.url()), timeout: 120000 })
await page.click('.cb-ide-pdf-btn')
const url = (await pdfReq).url()                                  // what the app opened (window.open, noopener)
out.pdfUrl = url.replace(/^https?:\/\/[^/]+/, '')
const res = await page.request.get(url)
out.pdfType = res.headers()['content-type']
out.pdfMagic = (await res.body()).subarray(0, 5).toString()
fs.writeFileSync(outFile, JSON.stringify(out, null, 1))
await browser.close()
