// Run one scripted acceptance scenario in headless Chromium and save a structured result.
// Invoked by scenario_run.py with a JSON options argument; not meant to be called directly.
//
// A scenario script is an ES module whose default export receives `t`:
//   t.page, t.context, t.request   Playwright page, browser context and API client (shares cookies)
//   t.baseUrl, t.params, t.outDir  runtime URL, scenario parameters, evidence directory
//   await t.step(name, fn)         run an action; a throw stops the scenario as an error, with evidence
//   t.check(name, ok, detail)      record an expectation without stopping
//   await t.waitFor(name, fn, {timeout})  poll fn until truthy, then record it as a check
//   await t.shot(name)             save a screenshot and return its path
//   t.note(key, value)             record an observation for the report
import { createRequire } from "node:module";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const options = JSON.parse(process.argv[2]);
const require = createRequire(path.join(options.toolDir, "package.json"));
const { chromium } = require("playwright");
const LIMIT = 50;

const result = {
  script: options.script, baseUrl: options.baseUrl || null, startedAt: new Date().toISOString(),
  status: "pass", error: null, steps: [], checks: [], screenshots: [], observations: {},
  consoleErrors: [], failedRequests: [], trace: null, failureEvidence: [], durationMs: 0,
};
const started = Date.now();
let shots = 0;

const slug = (text) => String(text).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60) || "shot";
const message = (error) => (error && error.message ? error.message.split("\n").slice(0, 6).join("\n") : String(error));
const remember = (list, value) => { if (list.length < LIMIT) list.push(value); };
const bare = (url) => url.split("?")[0];

await fs.mkdir(options.out, { recursive: true });
const browser = await chromium.launch({ headless: !options.headed });
const context = await browser.newContext({
  baseURL: options.baseUrl || undefined,
  viewport: { width: 1280, height: 900 },
  ...(options.storageState ? { storageState: options.storageState } : {}),
  ...(options.context || {}),
});
context.setDefaultTimeout(options.actionTimeoutMs);
context.setDefaultNavigationTimeout(30000);
await context.tracing.start({ screenshots: true, snapshots: true });
context.on("console", (msg) => { if (msg.type() === "error") remember(result.consoleErrors, msg.text().slice(0, 300)); });
context.on("weberror", (error) => remember(result.consoleErrors, `pageerror: ${message(error.error())}`.slice(0, 300)));
context.on("requestfailed", (request) => {
  remember(result.failedRequests, { url: bare(request.url()), method: request.method(), failure: request.failure()?.errorText || "failed" });
});
context.on("response", (response) => {
  if (response.status() >= 400) remember(result.failedRequests, { url: bare(response.url()), method: response.request().method(), status: response.status() });
});
const page = await context.newPage();

async function shot(name, settings = {}) {
  shots += 1;
  const file = path.join(options.out, `${String(shots).padStart(2, "0")}-${slug(name)}.png`);
  await page.screenshot({ path: file, fullPage: Boolean(settings.fullPage) });
  result.screenshots.push(file);
  return file;
}

async function captureFailure(name) {
  // Evidence for deciding between a product defect and a script defect without rerunning.
  const base = path.join(options.out, `failed-${slug(name)}`);
  const evidence = { step: name, url: page.url() };
  try { evidence.screenshot = await shot(`failed ${name}`); } catch (error) { evidence.screenshotError = message(error); }
  try {
    await fs.writeFile(`${base}.aria.yml`, await page.locator("body").ariaSnapshot({ timeout: 5000 }));
    evidence.aria = `${base}.aria.yml`;
  } catch (error) { evidence.ariaError = message(error); }
  result.failureEvidence.push(evidence);
}

const t = {
  page, context, request: context.request, baseUrl: options.baseUrl, params: options.params || {}, outDir: options.out,
  async step(name, fn) {
    const begin = Date.now();
    try {
      const value = await fn();
      result.steps.push({ name, ok: true, ms: Date.now() - begin });
      return value;
    } catch (error) {
      result.steps.push({ name, ok: false, ms: Date.now() - begin, error: message(error) });
      await captureFailure(name);
      error.recordedStep = name;
      throw error;
    }
  },
  check(name, ok, detail) {
    result.checks.push({ name, ok: Boolean(ok), ...(detail === undefined ? {} : { detail }) });
    return Boolean(ok);
  },
  async waitFor(name, fn, settings = {}) {
    const deadline = Date.now() + (settings.timeout ?? options.actionTimeoutMs);
    let value;
    let lastError = null;
    do {
      try { value = await fn(); lastError = null; } catch (error) { value = undefined; lastError = message(error); }
      if (value) break;
      await page.waitForTimeout(250);
    } while (Date.now() < deadline);
    return t.check(name, value, value ? undefined : { last: value ?? null, error: lastError, timeoutMs: settings.timeout ?? options.actionTimeoutMs });
  },
  shot,
  note(key, value) { result.observations[key] = value; },
};

let timer;
try {
  const module = await import(pathToFileURL(path.resolve(options.script)).href);
  if (typeof module.default !== "function") throw new Error("Scenario script must export a default async function(t)");
  const run = module.default(t);
  run.catch(() => {}); // After a timeout the script keeps running until the browser closes.
  await Promise.race([
    run,
    new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`Scenario timed out after ${options.timeoutMs / 1000}s`)), options.timeoutMs); }),
  ]);
  if (result.checks.length === 0) throw new Error("Scenario recorded no checks; add t.check or t.waitFor for each expected outcome");
  result.status = result.checks.every((check) => check.ok) ? "pass" : "fail";
} catch (error) {
  result.status = "error";
  result.error = message(error);
  if (!error.recordedStep) await captureFailure("unhandled").catch(() => {});
} finally {
  clearTimeout(timer);
  if (result.status === "pass" && options.saveSession) {
    // Parallel scenarios share this one session: a new login can revoke the user's earlier tokens.
    await context.storageState({ path: options.saveSession });
    await fs.chmod(options.saveSession, 0o600);
    result.savedSession = options.saveSession;
  }
  if (result.status === "pass") {
    await context.tracing.stop().catch(() => {});
  } else {
    result.trace = path.join(options.out, "trace.zip");
    await context.tracing.stop({ path: result.trace }).catch(() => { result.trace = null; });
  }
  await browser.close().catch(() => {});
  result.durationMs = Date.now() - started;
  await fs.writeFile(path.join(options.out, "result.json"), JSON.stringify(result, null, 2) + "\n");
}
