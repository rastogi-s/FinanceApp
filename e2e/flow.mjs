// End-to-end test of a real (not demo) install, the way a person uses it on a phone: first-run setup with the
// code from the server log, an authenticator app (TOTP), a passkey (Chrome's virtual authenticator standing in for
// Face ID), uploading a brokerage positions file and two bank files, checking the numbers on Home, fixing a
// category in two taps, uploading a pay stub PDF, inviting a partner, and signing back in with the passkey and
// then with password + code.
// Every step is screenshotted and checked for JavaScript and console errors (which include anything the
// Content-Security-Policy blocked) and serious accessibility problems. Runs in CI on every pull request.
//
//   docker compose up -d   (DEMO_MODE=false, ENCRYPTION_KEY set)
//   SETUP_CODE=$(docker compose logs app | grep -o '[A-Z0-9]\{4\}-[A-Z0-9]\{4\}-[A-Z0-9]\{4\}' | tail -1)
//   cd e2e && SETUP_CODE=$SETUP_CODE node flow.mjs
import AxeBuilder from "@axe-core/playwright";
import { chromium } from "playwright";
import { createHmac } from "node:crypto";
import { mkdirSync } from "node:fs";
import { join } from "node:path";

const BASE = process.env.APP_URL || "http://localhost:8080";
const OUT = process.env.OUT_DIR || "flow-results";
const FIXTURES = new URL("../backend/tests/fixtures/", import.meta.url).pathname;
const CODE = process.env.SETUP_CODE;
const EMAIL = "shubham@example.com";
const PASSWORD = "a long and memorable passphrase";
if (!CODE) {
  console.error("Set SETUP_CODE (from the app's log).");
  process.exit(2);
}

function totp(secret, at = Date.now()) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const c of secret.replace(/\s|=/g, "").toUpperCase()) bits += alphabet.indexOf(c).toString(2).padStart(5, "0");
  const key = Buffer.from(bits.match(/.{8}/g).map((b) => parseInt(b, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(at / 30000)));
  const h = createHmac("sha1", key).update(counter).digest();
  const o = h[h.length - 1] & 15;
  return String((h.readUInt32BE(o) & 0x7fffffff) % 1_000_000).padStart(6, "0");
}

const failures = [];
let step = 0;
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
const page = await context.newPage();
let errors = [];
page.on("pageerror", (e) => errors.push("JavaScript error: " + e.message));
page.on("console", (m) => {
  // A 401 while signed out is how the app learns it has to sign in; anything else is a real error.
  if (m.type() === "error" && !/status of 401/.test(m.text())) errors.push("console error: " + m.text());
});
page.on("dialog", (d) => {
  errors.push("unexpected dialog: " + d.message());
  d.dismiss().catch(() => {});
});

// A platform authenticator with user verification, like Face ID.
const cdp = await context.newCDPSession(page);
await cdp.send("WebAuthn.enable");
const { authenticatorId } = await cdp.send("WebAuthn.addVirtualAuthenticator", {
  options: { protocol: "ctap2", transport: "internal", hasResidentKey: true, hasUserVerification: true, isUserVerified: true },
});

async function check(name, fn) {
  step += 1;
  const label = `${String(step).padStart(2, "0")}-${name}`;
  errors = [];
  const problems = [];
  try {
    await fn();
    await page.waitForFunction(() => !document.querySelector("[aria-busy=true]"), null, { timeout: 10000 });
    await page.waitForTimeout(250);
    const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
    for (const v of axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical")) {
      problems.push(`accessibility: ${v.help} (${v.nodes[0]?.target.join(" ")})`);
    }
    const width = await page.evaluate(() => document.documentElement.scrollWidth);
    if (width > 391) problems.push(`page is ${width}px wide (sideways scrolling)`);
  } catch (e) {
    problems.push(e.message.split("\n")[0]);
  }
  await page.screenshot({ path: join(OUT, `${label}.png`), fullPage: true }).catch(() => {});
  problems.push(...errors);
  if (problems.length) {
    failures.push(label);
    console.log(`FAIL ${label}\n  - ${problems.join("\n  - ")}`);
  } else console.log(`ok   ${label}`);
}

const main = () => page.locator("main");
const button = (name) => page.getByRole("button", { name, exact: false });

let secret = "";
try {
  await check("fresh-install-goes-to-setup", async () => {
    await page.goto(BASE + "/");
    await page.waitForURL((u) => u.pathname === "/setup");
    await main().getByRole("heading", { name: "Set up Finance" }).waitFor();
  });

  await check("setup-details", async () => {
    await page.getByLabel("Setup code").fill(CODE);
    await page.getByLabel("Your name").fill("Shubham");
    await page.getByLabel("Email").fill(EMAIL);
    await page.getByLabel("Password").fill(PASSWORD);
    await button("Continue").click();
    await main().getByRole("heading", { name: "Add a code app" }).waitFor();
    secret = (await page.getByLabel("Setup key").innerText()).replace(/\s/g, "");
  });

  await check("setup-code-app-done", async () => {
    await page.getByLabel("Then type the 6-digit code it shows").fill(totp(secret));
    await main().getByRole("heading", { name: "Sign in with a glance" }).waitFor();
  });

  await check("setup-passkey-added", async () => {
    await button("Add a passkey").click();
    // The setup guide comes next: the passkey step is ticked, linking accounts is the open one.
    await page.waitForURL((u) => u.pathname === "/start");
    await main().getByRole("heading", { name: "Get set up" }).waitFor();
    await main().getByRole("link", { name: "Add an account" }).click();
    await page.waitForURL((u) => u.pathname === "/settings/accounts");
    await page.getByText("Now add your first account").waitFor();
    const { credentials } = await cdp.send("WebAuthn.getCredentials", { authenticatorId });
    if (credentials.length !== 1) throw new Error(`expected 1 passkey, found ${credentials.length}`);
  });

  async function uploadFile(file, fill) {
    await button("Add account").click();
    await button("Upload a file").click();
    await page.locator('input[type="file"]').setInputFiles(join(FIXTURES, file));
    await page.getByRole("heading", { name: "Check and save" }).waitFor();
    if (fill) await fill();
  }

  await check("upload-fidelity-preview", () => uploadFile("fidelity_positions.csv"));
  await check("upload-fidelity-saved", async () => {
    await button("Save").click();
    await page.getByText("Saved 4 holdings").waitFor();
    await main().getByText("Fidelity").first().waitFor();
  });

  await check("upload-checking", async () => {
    await uploadFile("chase_checking_4417.csv", () => page.getByLabel("Institution").fill("Chase"));
    await button("Save").click();
    await page.getByText("Saved 6 new transactions").waitFor();
  });

  await check("upload-card-preview", async () => {
    await uploadFile("amex_1008.csv", async () => {
      await page.getByLabel("Institution").fill("American Express");
      await page.getByLabel("Kind of account").selectOption("credit");
    });
    await page.getByText("Money in and out look backwards").waitFor();
  });
  await check("upload-card-saved", async () => {
    await button("Save").click();
    await page.getByText("Saved 5 new transactions").waitFor();
  });

  // A statement PDF isn't a layout the built-in readers know, so the app offers AI, and says it isn't set up yet.
  await check("upload-pdf-offers-ai", async () => {
    await button("Add account").click();
    await button("Upload a file").click();
    await page.locator('input[type="file"]').setInputFiles(join(FIXTURES, "statement.pdf"));
    await page.getByText(/AI isn't set up for reading uploads/i).waitFor();
    await page.getByRole("link", { name: "Settings, AI" }).waitFor();
    await button("Back").click();
    await page.keyboard.press("Escape");
  });

  await check("statement-check", async () => {
    await main().getByRole("button", { name: /Individual/ }).first().click();
    await page.getByLabel("Statement balance").fill("29834.56");
    await button("Check").click();
    await page.getByText(/Within 0% of/).waitFor();
    await page.keyboard.press("Escape");
  });

  await check("home-with-real-numbers", async () => {
    await page.locator("nav:visible [data-tab=home]").click();
    await page.waitForURL((u) => u.pathname === "/");
    await page.getByText("Net worth").first().waitFor();
    const text = await main().innerText();
    if (!text.includes("$32,335"))
      throw new Error("net worth isn't the uploaded holdings ($29,834.56 + $2,500.00): " + text.slice(0, 200));
  });

  await check("spending", async () => {
    await page.locator("nav:visible [data-tab=spending]").click();
    await main().getByText("Trader Joe's San Francisco").waitFor();
  });

  await check("recategorize-two-taps", async () => {
    await main().getByRole("button", { name: /Blue Bottle Coffee/ }).first().click();
    await page.getByRole("dialog").getByRole("button", { name: "Groceries" }).click();
    await page.getByText(/Moved to Groceries, with 1 other/).waitFor();
  });

  await check("pay-stub-upload", async () => {
    await page.locator("nav:visible [data-tab=budget]").click();
    await page.waitForURL((u) => u.pathname === "/budget");
    await button("Add a pay stub").click();
    await page.locator("#stub-file").setInputFiles(join(FIXTURES, "paystub_adp.pdf"));
    await page.getByText("Filled in from paystub_adp.pdf").waitFor();
    if ((await page.getByLabel("Gross pay").inputValue()) !== "4500") throw new Error("gross pay wasn't filled in from the stub");
    if ((await page.getByRole("button", { name: "Every 2 weeks" }).getAttribute("aria-pressed")) !== "true")
      throw new Error("biweekly pay wasn't picked up from the stub");
  });
  await check("pay-stub-saved", async () => {
    await page.getByRole("dialog").getByRole("button", { name: "Save pay stub" }).click();
    await page.getByText(/Every 2 weeks · next payday/).waitFor();
  });

  await check("taxes-first-questions", async () => {
    await page.goto(BASE + "/taxes");
    await main().getByText("Two questions first").waitFor();
    await main().getByRole("button", { name: "Married filing jointly" }).click();
    await main().getByRole("button", { name: "Save", exact: true }).click();
    await page.getByText("Saved. The estimate uses your answers now.").waitFor();
    if (!(await main().getByText(/latest stub/i).count())) throw new Error("the pay stub isn't counted on Taxes");
  });
  await check("taxes-type-a-number", async () => {
    await main().getByRole("button", { name: /Long-term gains/ }).click();
    await page.getByRole("dialog").getByLabel("For the whole year").fill("5000");
    await page.getByRole("dialog").getByRole("button", { name: "Save" }).click();
    await main().getByRole("button", { name: /Long-term gains.*Typed in/ }).waitFor();
  });

  await check("security", async () => {
    await page.goto(BASE + "/settings/security");
    await page.getByText("Last used").or(page.getByText("Added")).first().waitFor();
    await main().getByText("Account created").waitFor();
  });

  await check("ai-settings", async () => {
    await page.goto(BASE + "/settings/ai");
    await main().getByText("No AI yet").waitFor();
  });

  await check("ai-add-provider-guided", async () => {
    await button("Add a provider").click();
    await page.getByRole("dialog").getByRole("button", { name: /Local or other/ }).click();
    // Nothing listens there, so the check fails and says why, and nothing is saved.
    await page.getByLabel("Server address").fill("http://localhost:9/v1");
    await page.getByRole("dialog").getByRole("button", { name: "Check and save" }).click();
    await page.getByRole("dialog").getByText("Couldn't reach your server").waitFor();
    errors = errors.filter((e) => !/status of 422/.test(e)); // the refusal the browser logs is the expected answer
    await page.keyboard.press("Escape");
    await main().getByText("No AI yet").waitFor();
  });

  let agentKey = "";
  await check("agents-new-key", async () => {
    await page.goto(BASE + "/settings/agents");
    await main().getByText("No agents yet").waitFor();
    await button("New key").click();
    await page.getByLabel("Name").fill("Flow test");
    await page.getByRole("dialog").getByRole("button", { name: "Make the key" }).click();
    agentKey = (await page.getByTestId("agent-token").innerText()).trim();
    if (!agentKey.startsWith("fa_agent_")) throw new Error("no key shown");
    await page.getByRole("dialog").getByRole("button", { name: "Done" }).click();
    await main().getByText("Flow test").waitFor();
  });

  // An agent asks over MCP and gets the same net worth as Home ($29,834.56 + $2,500.00).
  await check("agents-mcp-answers", async () => {
    const ask = (body, key = agentKey) =>
      fetch(BASE + "/mcp", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json, text/event-stream", Authorization: `Bearer ${key}` },
        body: JSON.stringify({ jsonrpc: "2.0", id: 1, ...body }),
      });
    if ((await ask({ method: "tools/list" }, "fa_agent_nope")).status !== 401) throw new Error("a wrong key got in");
    const init = await (await ask({ method: "initialize", params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "flow", version: "1" } } })).json();
    if (init.result?.serverInfo?.name !== "financeapp") throw new Error("initialize: " + JSON.stringify(init));
    const r = await (await ask({ method: "tools/call", params: { name: "overview", arguments: {} } })).json();
    const net = r.result?.structuredContent?.net_worth;
    if (Math.round(net) !== 32335) throw new Error("overview net worth: " + JSON.stringify(r).slice(0, 300));
  });

  await check("brief-write-now", async () => {
    await page.goto(BASE + "/settings/alerts");
    await main().getByText("Morning brief", { exact: true }).first().waitFor();
    await button("Write today's now").click();
    await main().getByText(/^Latest, /).waitFor();
    await main().getByText(/Net worth is \$32,335/).waitFor();
    await page.locator("nav:visible [data-tab=home]").click();
    await page.waitForURL((u) => u.pathname === "/");
    await main().getByText("Morning brief", { exact: true }).first().waitFor();
  });

  await check("invite-partner", async () => {
    await page.goto(BASE + "/settings/people");
    await button("Invite someone").click();
    await page.getByLabel("Their name").fill("Priya");
    await button("Create invitation").click();
    await page.getByText("Invitation link").waitFor();
    await page.keyboard.press("Escape");
  });

  await check("sign-out", async () => {
    await page.goto(BASE + "/settings");
    await button("Sign out").click();
    await page.waitForURL((u) => u.pathname === "/signin");
    await main().getByRole("heading", { name: "Welcome back" }).waitFor();
  });

  await check("private-pages-need-sign-in", async () => {
    const r = await page.request.get(BASE + "/api/household");
    if (r.status() !== 401) throw new Error(`/api/household answered ${r.status()} while signed out`);
    // As if tapping a price alert's push while signed out. Opening the app asks for the passkey by itself; here
    // Face ID fails (or the sheet is cancelled), so the button stays and it doesn't ask again.
    await cdp.send("WebAuthn.setUserVerified", { authenticatorId, isUserVerified: false });
    let asks = 0;
    const count = (req) => req.url().endsWith("/api/auth/passkey/options") && (asks += 1);
    page.on("request", count);
    await page.goto(BASE + "/portfolio/holding?s=VTI");
    await page.waitForURL((u) => u.pathname === "/signin");
    await page.waitForFunction(() => [...document.querySelectorAll("button")].some((b) => /passkey/.test(b.textContent) && !b.disabled));
    await page.waitForTimeout(1500);
    page.off("request", count);
    if (asks !== 1) throw new Error(`asked for the passkey ${asks} times on opening, expected once`);
    if (new URL(page.url()).pathname !== "/signin") throw new Error("signed in without Face ID");
    await cdp.send("WebAuthn.setUserVerified", { authenticatorId, isUserVerified: true });
  });

  await check("sign-in-with-passkey-opens-the-tapped-page", async () => {
    await button("Sign in with passkey").click();
    await page.waitForURL((u) => u.pathname === "/portfolio/holding" && u.searchParams.get("s") === "VTI");
    await page.goto(BASE + "/");
    await page.getByText("Net worth").first().waitFor();
  });

  await check("opening-the-app-asks-for-the-passkey", async () => {
    // Session gone (timed out on the server) and the app opened again: Face ID comes up with no tap.
    await context.clearCookies();
    await page.goto(BASE + "/portfolio/holding?s=VTI");
    await page.waitForURL((u) => u.pathname === "/portfolio/holding" && u.searchParams.get("s") === "VTI");
    await page.goto(BASE + "/");
    await page.getByText("Net worth").first().waitFor();
  });

  await check("leaving-the-app-locks-it", async () => {
    // Coming back from another app: under a minute stays in; over a minute (or swiping the app away) ends the
    // session and asks for Face ID straight away, then carries on where you were.
    let asks = 0;
    const count = (req) => req.url().endsWith("/api/auth/passkey/options") && (asks += 1);
    page.on("request", count);
    const comeBack = (secondsAway) =>
      page.evaluate((s) => {
        localStorage.setItem("left-at", String(Date.now() - s * 1000));
        document.dispatchEvent(new Event("visibilitychange"));
      }, secondsAway);
    await comeBack(5);
    await page.waitForTimeout(1000);
    if (asks !== 0 || new URL(page.url()).pathname !== "/") throw new Error("a quick switch away locked the app");
    const before = (await context.cookies()).map((c) => c.value).join();
    await comeBack(120);
    await page.waitForResponse((r) => r.url().endsWith("/api/auth/passkey/verify") && r.ok());
    await page.waitForURL((u) => u.pathname === "/");
    await page.getByText("Net worth").first().waitFor();
    page.off("request", count);
    if (asks !== 1) throw new Error(`asked for the passkey ${asks} times after being away`);
    if ((await context.cookies()).map((c) => c.value).join() === before) throw new Error("the session wasn't renewed");
  });

  await check("sign-in-with-password-and-code", async () => {
    await page.goto(BASE + "/settings");
    await button("Sign out").click();
    await page.waitForURL((u) => u.pathname === "/signin");
    await button("Use password and code").click();
    await page.getByLabel("Email").fill(EMAIL);
    await page.getByLabel("Password").fill(PASSWORD);
    await button("Continue").click();
    await page.getByRole("heading", { name: "Enter your code" }).waitFor();
    // A code works once: wait for the next one after the last code used.
    const wait = 30000 - (Date.now() % 30000) + 500;
    await page.waitForTimeout(wait);
    await page.locator("#code").fill(totp(secret));
    await page.waitForURL((u) => u.pathname === "/");
  });

  await check("password-device-isnt-asked-for-a-passkey", async () => {
    // This device last signed in with password and code, so opening the app shows the buttons without a passkey sheet.
    await context.clearCookies();
    let asks = 0;
    const count = (req) => req.url().endsWith("/api/auth/passkey/options") && (asks += 1);
    page.on("request", count);
    await page.goto(BASE + "/");
    await page.waitForURL((u) => u.pathname === "/signin");
    await button("Sign in with passkey").waitFor();
    await page.waitForTimeout(1500);
    page.off("request", count);
    if (asks !== 0) throw new Error(`asked for a passkey ${asks} times on a password device`);
  });
} finally {
  await browser.close();
}

if (failures.length) {
  console.log(`\n${failures.length} step(s) failed. Screenshots are in ${OUT}/`);
  process.exit(1);
}
console.log(`\nThe whole flow works. Screenshots are in ${OUT}/`);
