// Smoke test: opens every page of a demo-mode copy of the app the way you would, on a laptop, a wide screen and a
// phone (light and dark), and fails on a JavaScript error, a console error (which includes anything the
// Content-Security-Policy blocked), a failed API call, a page without a heading, an empty page, sideways
// scrolling on the phone, or a serious accessibility problem. Every page is screenshotted to OUT_DIR, so a pull
// request shows how it looks. Runs in CI on every pull request.
//
//   docker compose up -d   (with DEMO_MODE=true)    or    DEMO_MODE=true uvicorn financeapp.main:app
//   cd e2e && npm ci && APP_URL=http://localhost:8080 node smoke.mjs
import AxeBuilder from "@axe-core/playwright";
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
import { join } from "node:path";

const BASE = process.env.APP_URL || "http://localhost:8080";
const OUT = process.env.OUT_DIR || "smoke-results";
const SIZES = {
  "laptop-light": { viewport: { width: 1366, height: 768 }, colorScheme: "light" },
  // A big laptop or external screen, where pages lay out in two columns and use the width.
  "wide-dark": { viewport: { width: 1920, height: 1080 }, colorScheme: "dark" },
  "phone-light": { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2, colorScheme: "light" },
  "phone-dark": { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2, colorScheme: "dark" },
};
// [page, path, how to get there]: "tab" is in the tab bar or sidebar, "header" is the button at the top right,
// "direct" is only opened by its address (it's a row inside Settings).
const PLACES = [
  ["home", "/", "tab"],
  ["spending", "/spending", "tab"],
  ["budget", "/budget", "tab"],
  ["portfolio", "/portfolio", "tab"],
  ["plan", "/plan", "tab"],
  ["goals", "/goals", "direct"],
  ["review", "/review", "direct"],
  ["cards", "/cards", "direct"],
  ["taxes", "/taxes", "direct"],
  ["earnings", "/earnings", "direct"],
  ["settings", "/settings", "header"],
  ["accounts", "/settings/accounts", "direct"],
  ["security", "/settings/security", "direct"],
  ["people", "/settings/people", "direct"],
  ["ai", "/settings/ai", "direct"],
  ["agents", "/settings/agents", "direct"],
  ["alerts", "/settings/alerts", "direct"],
  ["start", "/start", "direct"],
  ["holding-stock", "/portfolio/holding?s=AAPL", "direct"],
  ["holding-fund", "/portfolio/holding?s=VTI", "direct"],
  ["income", "/portfolio/income", "direct"],
  ["moves", "/portfolio/moves", "direct"],
];
const REQUIRED_HEADERS = {
  "content-security-policy": /default-src 'none'.*frame-ancestors 'none'/,
  "x-frame-options": /^DENY$/,
  "x-content-type-options": /^nosniff$/,
  "referrer-policy": /^no-referrer$/,
};

const failures = [];
const fail = (where, problems) => {
  failures.push({ where, problems });
  console.log(`FAIL ${where}\n  - ${problems.join("\n  - ")}`);
};

async function check(page, size, name) {
  const screen = SIZES[size].viewport.width;
  const problems = [];
  const heading = page.locator("main h1").first();
  try {
    await heading.waitFor({ state: "visible", timeout: 10000 });
  } catch {
    problems.push("the page never showed a heading");
  }
  // Wait for the skeletons to be replaced by real content.
  await page.waitForFunction(() => !document.querySelector("[aria-busy=true]"), null, { timeout: 10000 }).catch(() => {
    problems.push("still loading after 10 seconds");
  });
  await page.waitForLoadState("networkidle").catch(() => {});
  // A phone zooms out to fit content that's too wide, so compare with the screen, not with innerWidth.
  const state = await page.evaluate(() => ({
    text: document.querySelector("main")?.innerText.trim().length ?? 0,
    width: Math.max(document.documentElement.scrollWidth, window.innerWidth),
  }));
  if (state.text < 40) problems.push("the page is (nearly) empty");
  if (state.width > screen + 1) {
    problems.push(`page is ${state.width}px wide on a ${screen}px screen (sideways scrolling)`);
  }
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  for (const v of axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical")) {
    const n = v.nodes[0];
    problems.push(`accessibility: ${v.help} (${v.nodes.length}x, e.g. ${n?.target.join(" ")}: ${n?.any[0]?.message ?? ""})`);
  }
  await page.screenshot({ path: join(OUT, `${size}-${name}.png`), fullPage: true }).catch(() => {});
  return problems;
}

mkdirSync(OUT, { recursive: true });

// Security headers on the page, the API and the health check.
for (const path of ["/", "/spending", "/api/household", "/health"]) {
  const r = await fetch(BASE + path);
  const problems = [];
  if (!r.ok) problems.push(`returned ${r.status}`);
  for (const [header, pattern] of Object.entries(REQUIRED_HEADERS)) {
    if (!pattern.test(r.headers.get(header) || "")) problems.push(`${header} is "${r.headers.get(header)}"`);
  }
  if (path.startsWith("/api") && r.headers.get("cache-control") !== "no-store") problems.push("API response may be cached");
  if (problems.length) fail(`headers ${path}`, problems);
  else console.log(`ok   headers ${path}`);
}

const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
try {
  for (const [size, options] of Object.entries(SIZES)) {
    const context = await browser.newContext(options);
    const page = await context.newPage();
    let errors = [];
    page.on("pageerror", (e) => errors.push("JavaScript error: " + e.message));
    page.on("console", (m) => {
      if (m.type() === "error") errors.push("console error: " + m.text());
    });
    page.on("response", (r) => {
      const path = new URL(r.url()).pathname;
      if ((path.startsWith("/api/") || path.startsWith("/_next/")) && r.status() >= 400) {
        errors.push(`${r.request().method()} ${path} returned ${r.status()}`);
      }
    });
    page.on("dialog", (d) => {
      errors.push("unexpected dialog: " + d.message());
      d.dismiss().catch(() => {});
    });

    // 1. Open each page directly (a fresh load, so each page's own CSP is exercised).
    for (const [name, path] of PLACES) {
      errors = [];
      let problems;
      try {
        await page.goto(BASE + path);
        problems = await check(page, size, name);
      } catch (e) {
        problems = ["couldn't open it: " + e.message.split("\n")[0]];
      }
      problems.push(...errors);
      if (problems.length) fail(`${size} ${name}`, problems);
      else console.log(`ok   ${size} ${name}`);
    }

    // 2. Move between pages with the navigation, as you would.
    await page.goto(BASE + "/");
    for (const [name, path, how] of PLACES.filter((p) => p[2] !== "direct")) {
      errors = [];
      const problems = [];
      try {
        const link =
          how === "header"
            ? page.locator(`header [data-tab=${name}]`)
            : page.locator(`nav:visible [data-tab=${name}]`);
        await link.first().click();
        await page.waitForURL((u) => u.pathname === path);
        await page.locator("main h1").first().waitFor({ state: "visible", timeout: 10000 });
        const current = await page.locator(`nav:visible [aria-current=page]`).first().getAttribute("data-tab").catch(() => null);
        if (how === "tab" && current !== name) problems.push(`the tab bar highlights "${current}"`);
      } catch (e) {
        problems.push("couldn't get there: " + e.message.split("\n")[0]);
      }
      problems.push(...errors);
      if (problems.length) fail(`${size} tap to ${name}`, problems);
      else console.log(`ok   ${size} tap to ${name}`);
    }

    // 3. The privacy button blurs every amount.
    await page.goto(BASE + "/");
    await page.locator("main .money").first().waitFor();
    await page.getByRole("button", { name: "Hide amounts" }).click();
    const blurred = await page.evaluate(() =>
      [...document.querySelectorAll("main .money")].every((el) => getComputedStyle(el).filter.includes("blur")),
    );
    await page.screenshot({ path: join(OUT, `${size}-home-private.png`) });
    if (!blurred) fail(`${size} privacy`, ["some amounts stayed visible with privacy on"]);
    else console.log(`ok   ${size} privacy`);
    await page.getByRole("button", { name: "Show amounts" }).click();

    // 4. Net worth opens its sheet: the math, then what moved it.
    errors = [];
    try {
      const problems = [];
      await page.getByRole("button", { name: /^Net worth: see how/ }).click();
      const sheet = page.getByRole("dialog");
      await sheet.getByText("What you own").waitFor();
      await page.screenshot({ path: join(OUT, `${size}-net-worth.png`) });
      await sheet.getByRole("radio", { name: "What moved it" }).click();
      await sheet.getByText("By account").waitFor();
      await page.screenshot({ path: join(OUT, `${size}-net-worth-moved.png`) });
      const axe = await new AxeBuilder({ page }).include("[role=dialog]").withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
      for (const v of axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical")) problems.push(`${v.id}: ${v.help}`);
      problems.push(...errors);
      if (problems.length) fail(`${size} net worth sheet`, problems);
      else console.log(`ok   ${size} net worth sheet`);
    } catch (e) {
      fail(`${size} net worth sheet`, ["couldn't open it: " + e.message.split("\n")[0], ...errors]);
    }

    // 5. The bell in the top bar: big enough to tap, shows the unread count, opens the alerts, and an alert opens
    // the page it's about.
    await page.goto(BASE + "/");
    errors = [];
    try {
      const problems = [];
      const bell = page.getByRole("button", { name: /^Alerts, \d+ unread$/ });
      await bell.waitFor();
      const minimum = SIZES[size].isMobile ? 44 : 40;
      for (const name of [/^Alerts/, /amounts$/]) {
        const box = await page.locator("header").getByRole("button", { name }).boundingBox();
        if (!box || box.width < minimum || box.height < minimum) problems.push(`${name} is ${box?.width}x${box?.height}px`);
      }
      await bell.click();
      const sheet = page.getByRole("dialog", { name: "Alerts" });
      await sheet.getByRole("list", { name: "Recent alerts" }).getByRole("link").first().waitFor();
      await page.screenshot({ path: join(OUT, `${size}-alerts-bell.png`) });
      const axe = await new AxeBuilder({ page }).include("[role=dialog]").withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
      for (const v of axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical")) problems.push(`${v.id}: ${v.help}`);
      await sheet.getByRole("link", { name: /AAPL is up/ }).click();
      await page.waitForURL(/\/portfolio\/holding\?s=AAPL/);
      problems.push(...errors);
      if (problems.length) fail(`${size} alerts bell`, problems);
      else console.log(`ok   ${size} alerts bell`);
    } catch (e) {
      fail(`${size} alerts bell`, ["couldn't use it: " + e.message.split("\n")[0], ...errors]);
    }

    await context.close();
  }
} finally {
  await browser.close();
}

if (failures.length) {
  console.log(`\n${failures.length} problem(s). Screenshots are in ${OUT}/`);
  process.exit(1);
}
console.log(`\nAll pages fine. Screenshots are in ${OUT}/`);
