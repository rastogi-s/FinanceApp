// Going back puts you where you were: opens a holding from far down Portfolio and comes back (with the page's own
// back link and with the browser's), on a laptop and a phone, and checks the scroll position, the holdings search
// and the sort are as they were left, that tapping the tab opens it at the top with the same search and sort, and
// that Home, Budget and Settings come back scrolled where they were too. Runs in CI on every pull request, after the smoke test.
//
//   DEMO_MODE=true uvicorn financeapp.main:app    then    cd e2e && APP_URL=http://localhost:8080 node back.mjs
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = process.env.APP_URL || "http://localhost:8080";
const OUT = process.env.OUT_DIR || "back-results";
mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
const sizes = {
  laptop: { viewport: { width: 1366, height: 768 } },
  phone: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 },
};
let fail = 0;
const check = (ok, msg) => { console.log((ok ? "PASS " : "FAIL ") + msg); if (!ok) fail++; };
for (const [name, opts] of Object.entries(sizes)) {
  const page = await (await browser.newContext(opts)).newPage();
  page.on("pageerror", (e) => check(false, `${name} page error ${e.message}`));
  await page.goto(BASE + "/portfolio");
  const search = page.getByLabel("Search holdings");
  await search.waitFor();
  // 1. scroll to holdings, open a holding, back link
  await search.scrollIntoViewIfNeeded();
  await page.mouse.wheel(0, 300); await page.waitForTimeout(400);
  let row = page.locator('a[href^="/portfolio/holding"]').filter({ visible: true }).last();
  await row.scrollIntoViewIfNeeded();
  const y0 = await page.evaluate(() => scrollY);
  await row.click();
  await page.waitForURL(/holding/); await page.getByRole("heading", { level: 1 }).waitFor();
  await page.waitForTimeout(500);
  await page.locator('main a[href="/portfolio"]').first().click();
  await page.waitForURL(/\/portfolio$/); await page.waitForTimeout(1500);
  let y1 = await page.evaluate(() => scrollY);
  check(Math.abs(y1 - y0) < 5 && y0 > 300, `${name} back link restores scroll ${y0} -> ${y1}`);
  await page.screenshot({ path: `${OUT}/${name}-portfolio-after-back.png` });
  // 2. search + browser back
  await search.fill("v");
  await page.getByLabel("Sort holdings").selectOption("name");
  await page.waitForTimeout(300);
  await page.mouse.wheel(0, -50); await page.waitForTimeout(400);
  row = page.locator('a[href^="/portfolio/holding"]').filter({ visible: true }).last();
  await row.scrollIntoViewIfNeeded();
  const y2 = await page.evaluate(() => scrollY);
  await row.click();
  await page.waitForURL(/holding/); await page.waitForTimeout(800);
  await page.goBack(); await page.waitForURL(/\/portfolio$/); await page.waitForTimeout(1500);
  check((await search.inputValue()) === "v", `${name} search kept after browser back`);
  check((await page.getByLabel("Sort holdings").inputValue()) === "name", `${name} sort kept after browser back`);
  const y3 = await page.evaluate(() => scrollY);
  check(Math.abs(y3 - y2) < 5, `${name} browser back restores scroll ${y2} -> ${y3}`);
  // 3. fresh tab tap starts clean
  await page.goto(BASE + "/"); await page.waitForTimeout(800);
  await page.locator('[data-tab="portfolio"]').filter({ visible: true }).click();
  await search.waitFor();
  check((await page.evaluate(() => scrollY)) < 5, `${name} tab tap opens portfolio at the top`);
  check(
    (await search.inputValue()) === "v" && (await page.getByLabel("Sort holdings").inputValue()) === "name",
    `${name} tab tap keeps the search and sort`,
  );
  await search.fill("");
  // 4. Settings -> Accounts -> the "Settings" back link goes back rather than to a fresh page
  await page.goto(BASE + "/settings"); await page.waitForTimeout(800);
  await page.locator('main a[href="/settings/accounts"]').first().click();
  await page.waitForURL(/accounts/); await page.waitForTimeout(500);
  const depth = await page.evaluate(() => history.length);
  await page.locator('main a[href="/settings"]').first().click();
  await page.waitForURL(/settings$/); await page.waitForTimeout(500);
  check((await page.evaluate(() => history.length)) === depth, `${name} Settings back link goes back`);
  // 5. Home -> Budget -> back, Budget -> Spending -> back
  // Home lays out in two columns on a laptop and fits a 768px-tall window, so make it short enough to scroll.
  const size = page.viewportSize();
  await page.setViewportSize({ width: size.width, height: Math.min(size.height, 480) });
  await page.goto(BASE + "/"); await page.waitForTimeout(1200);
  await page.mouse.wheel(0, 500); await page.waitForTimeout(400);
  const h0 = await page.evaluate(() => scrollY);
  await page.locator('a[href="/budget"]').filter({ visible: true }).last().click();
  await page.waitForURL(/budget/); await page.waitForTimeout(800);
  await page.goBack(); await page.waitForTimeout(1500);
  const h1 = await page.evaluate(() => scrollY);
  check(Math.abs(h1 - h0) < 5 && h0 > 0, `${name} home scroll after back from budget ${h0} -> ${h1}`);
  await page.setViewportSize(size);
  await page.goto(BASE + "/budget"); await page.waitForTimeout(1200);
  await page.mouse.wheel(0, 2000); await page.waitForTimeout(400);
  const link = page.locator('a[href^="/spending"]').filter({ visible: true }).first();
  if (await link.count()) {
    await link.scrollIntoViewIfNeeded();
    const b0 = await page.evaluate(() => scrollY);
    await link.click(); await page.waitForURL(/spending/); await page.waitForTimeout(800);
    await page.goBack(); await page.waitForTimeout(1500);
    const b1 = await page.evaluate(() => scrollY);
    check(Math.abs(b1 - b0) < 5, `${name} budget scroll after back from spending ${b0} -> ${b1}`);
  }
}
await browser.close();
process.exit(fail ? 1 : 0);
