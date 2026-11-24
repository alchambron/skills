#!/usr/bin/env node
/**
 * Live Playwright Web Vitals, JS heap, and network/API timing benchmarker.
 * Compares baseline vs PR preview URLs across multiple runs and reports medians
 * and percentage deltas. Reuses existing frontend/node_modules/playwright.
 */

import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

function parseArgs(argv) {
  const args = {
    repo: process.cwd(),
    baseUrl: null,
    prUrl: null,
    routes: ["/"],
    runs: 3,
    timeoutMs: 25_000,
    login: false,
  };
  for (let i = 2; i < argv.length; i += 1) {
    const k = argv[i];
    if (k === "--repo") args.repo = argv[++i];
    else if (k === "--base-url") args.baseUrl = argv[++i];
    else if (k === "--pr-url") args.prUrl = argv[++i];
    else if (k === "--routes") args.routes = argv[++i].split(",").map((r) => r.trim()).filter(Boolean);
    else if (k === "--runs") args.runs = Math.max(1, Number(argv[++i]) || 3);
    else if (k === "--timeout-ms") args.timeoutMs = Math.max(5000, Number(argv[++i]) || 25_000);
    else if (k === "--login") args.login = true;
  }
  return args;
}

function findPlaywrightEntry(repo) {
  const candidates = [path.join(repo, "frontend", "node_modules", "playwright", "index.mjs")];
  try {
    const common = execFileSync("git", ["rev-parse", "--git-common-dir"], {
      cwd: repo,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    }).trim();
    const mainRepo = path.dirname(path.resolve(repo, common));
    candidates.push(path.join(mainRepo, "frontend", "node_modules", "playwright", "index.mjs"));
    candidates.push(path.join(mainRepo, "frontend", "node_modules", "playwright", "index.js"));
  } catch {
    // Ignore git resolution errors.
  }
  candidates.push(path.join(repo, "frontend", "node_modules", "playwright", "index.js"));
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  return null;
}

function readEnvCredentials(repo) {
  const envPaths = [path.join(repo, ".env"), path.join(repo, "frontend", ".env")];
  const creds = {
    user: process.env.VITE_TEST_USERNAME || null,
    pass: process.env.VITE_TEST_PASSWORD || null,
  };
  if (creds.user && creds.pass) return creds;
  for (const ep of envPaths) {
    if (!fs.existsSync(ep)) continue;
    const text = fs.readFileSync(ep, "utf8");
    for (const line of text.split(/\r?\n/)) {
      const m = line.match(/^\s*(VITE_TEST_USERNAME|VITE_TEST_PASSWORD)\s*=\s*(.+?)\s*$/);
      if (!m) continue;
      const val = m[2].replace(/^['"]|['"]$/g, "");
      if (m[1] === "VITE_TEST_USERNAME" && !creds.user) creds.user = val;
      if (m[1] === "VITE_TEST_PASSWORD" && !creds.pass) creds.pass = val;
    }
  }
  return creds;
}

function median(values) {
  const nums = values.filter((v) => typeof v === "number" && Number.isFinite(v)).sort((a, b) => a - b);
  if (!nums.length) return null;
  const mid = Math.floor(nums.length / 2);
  return nums.length % 2 === 1 ? nums[mid] : (nums[mid - 1] + nums[mid]) / 2;
}

function improvementPct(baseVal, prVal) {
  // Lower is better for latency, LCP, FCP, TBT, heap, bytes.
  if (baseVal == null || prVal == null || baseVal <= 0) return null;
  return Number((((baseVal - prVal) / baseVal) * 100).toFixed(2));
}

async function tryLogin(page, origin, creds, timeoutMs) {
  if (!creds.user || !creds.pass) return false;
  await page.goto(origin, { waitUntil: "domcontentloaded", timeout: timeoutMs });
  const passInput = page.locator('input[type="password"]').first();
  if ((await passInput.count()) === 0) return false;
  const userInput = page.locator('input[type="email"], input[name*="user" i], input[type="text"]').first();
  if ((await userInput.count()) === 0) return false;
  await userInput.fill(creds.user);
  await passInput.fill(creds.pass);
  const submit = page.locator('button[type="submit"]').first();
  if ((await submit.count()) > 0) {
    await Promise.all([
      page.waitForLoadState("networkidle", { timeout: timeoutMs }).catch(() => {}),
      submit.click(),
    ]);
  }
  return true;
}

async function measureRouteOnce(context, origin, route, timeoutMs) {
  const page = await context.newPage();
  const cdp = await context.newCDPSession(page);
  await cdp.send("Performance.enable");

  await page.addInitScript(() => {
    window.__perfMetrics = { fcp: null, lcp: null, cls: 0, tbt: 0, longTasks: 0 };
    try {
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          if (entry.name === "first-contentful-paint") {
            window.__perfMetrics.fcp = entry.startTime;
          }
        }
      }).observe({ type: "paint", buffered: true });
    } catch {}
    try {
      new PerformanceObserver((list) => {
        const entries = list.getEntries();
        if (entries.length) {
          window.__perfMetrics.lcp = entries[entries.length - 1].startTime;
        }
      }).observe({ type: "largest-contentful-paint", buffered: true });
    } catch {}
    try {
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          if (!entry.hadRecentInput) window.__perfMetrics.cls += entry.value;
        }
      }).observe({ type: "layout-shift", buffered: true });
    } catch {}
    try {
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          window.__perfMetrics.longTasks += 1;
          if (entry.duration > 50) window.__perfMetrics.tbt += entry.duration - 50;
        }
      }).observe({ type: "longtask", buffered: true });
    } catch {}
  });

  const apiTimings = [];
  const reqStart = new Map();
  let totalTransferBytes = 0;

  page.on("request", (req) => {
    const rt = req.resourceType();
    if (rt === "fetch" || rt === "xhr") {
      reqStart.set(req, performance.now());
    }
  });
  page.on("response", async (res) => {
    const req = res.request();
    const rt = req.resourceType();
    const cl = Number(res.headers()["content-length"] || 0);
    if (Number.isFinite(cl) && cl > 0) totalTransferBytes += cl;
    if ((rt === "fetch" || rt === "xhr") && reqStart.has(req)) {
      apiTimings.push(performance.now() - reqStart.get(req));
      reqStart.delete(req);
    }
  });

  const targetUrl = origin.replace(/\/$/, "") + (route.startsWith("/") ? route : `/${route}`);
  await page.goto(targetUrl, { waitUntil: "networkidle", timeout: timeoutMs }).catch(() =>
    page.waitForLoadState("load", { timeout: timeoutMs }).catch(() => {}),
  );
  await page.waitForTimeout(400);

  const webVitals = await page.evaluate(() => {
    const nav = performance.getEntriesByType("navigation")[0];
    const m = window.__perfMetrics || {};
    return {
      ttfb_ms: nav ? Math.max(0, nav.responseStart - nav.requestStart) : null,
      dom_interactive_ms: nav ? nav.domInteractive : null,
      load_ms: nav ? nav.loadEventEnd : null,
      fcp_ms: m.fcp,
      lcp_ms: m.lcp,
      cls: Number((m.cls || 0).toFixed(4)),
      tbt_ms: m.tbt || 0,
      long_tasks: m.longTasks || 0,
    };
  });

  const cdpMetrics = await cdp.send("Performance.getMetrics");
  const metricMap = Object.fromEntries(cdpMetrics.metrics.map((m) => [m.name, m.value]));
  const jsHeapMb = metricMap.JSHeapUsedSize ? metricMap.JSHeapUsedSize / (1024 * 1024) : null;

  await page.close();

  return {
    ...webVitals,
    api_requests: apiTimings.length,
    api_median_ms: median(apiTimings),
    transfer_kb: Number((totalTransferBytes / 1024).toFixed(1)),
    js_heap_mb: jsHeapMb != null ? Number(jsHeapMb.toFixed(2)) : null,
  };
}

async function benchOrigin(chromium, origin, routes, runs, timeoutMs, login, creds) {
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    if (login) {
      const p = await context.newPage();
      await tryLogin(p, origin, creds, timeoutMs).catch(() => {});
      await p.close();
    }

    const perRoute = {};
    for (const route of routes) {
      // Warmup run (discarded).
      await measureRouteOnce(context, origin, route, timeoutMs);
      const samples = [];
      for (let i = 0; i < runs; i += 1) {
        samples.push(await measureRouteOnce(context, origin, route, timeoutMs));
      }
      perRoute[route] = {
        ttfb_ms: median(samples.map((s) => s.ttfb_ms)),
        fcp_ms: median(samples.map((s) => s.fcp_ms)),
        lcp_ms: median(samples.map((s) => s.lcp_ms)),
        tbt_ms: median(samples.map((s) => s.tbt_ms)),
        load_ms: median(samples.map((s) => s.load_ms)),
        cls: median(samples.map((s) => s.cls)),
        api_requests: median(samples.map((s) => s.api_requests)),
        api_median_ms: median(samples.map((s) => s.api_median_ms)),
        js_heap_mb: median(samples.map((s) => s.js_heap_mb)),
      };
    }
    return { ok: true, routes: perRoute };
  } catch (err) {
    return { ok: false, error: String(err).slice(0, 500) };
  } finally {
    await browser.close();
  }
}

async function main() {
  const args = parseArgs(process.argv);
  if (!args.baseUrl || !args.prUrl) {
    console.log(JSON.stringify({ status: "skipped", reason: "No --base-url and --pr-url provided" }));
    return;
  }

  const pwEntry = findPlaywrightEntry(args.repo);
  if (!pwEntry) {
    console.log(JSON.stringify({ status: "skipped", reason: "Playwright not found in frontend/node_modules" }));
    return;
  }

  let playwright;
  try {
    playwright = await import(pathToFileURL(pwEntry).href);
  } catch (err) {
    console.log(JSON.stringify({ status: "skipped", reason: `Failed to import Playwright: ${err}` }));
    return;
  }
  const chromium = playwright.chromium || playwright.default?.chromium;
  if (!chromium) {
    console.log(JSON.stringify({ status: "skipped", reason: "Playwright chromium launcher unavailable" }));
    return;
  }

  const creds = args.login ? readEnvCredentials(args.repo) : { user: null, pass: null };
  const baseRes = await benchOrigin(chromium, args.baseUrl, args.routes, args.runs, args.timeoutMs, args.login, creds);
  if (!baseRes.ok) {
    console.log(JSON.stringify({ status: "skipped", reason: `Baseline browser bench failed: ${baseRes.error}` }));
    return;
  }
  const prRes = await benchOrigin(chromium, args.prUrl, args.routes, args.runs, args.timeoutMs, args.login, creds);
  if (!prRes.ok) {
    console.log(JSON.stringify({ status: "skipped", reason: `PR browser bench failed: ${prRes.error}` }));
    return;
  }

  const routeDiffs = {};
  const compositeSamples = [];
  for (const route of args.routes) {
    const b = baseRes.routes[route];
    const p = prRes.routes[route];
    const lcpPct = improvementPct(b.lcp_ms, p.lcp_ms);
    const fcpPct = improvementPct(b.fcp_ms, p.fcp_ms);
    const loadPct = improvementPct(b.load_ms, p.load_ms);
    const apiPct = improvementPct(b.api_median_ms, p.api_median_ms);
    const tbtPct = improvementPct(b.tbt_ms, p.tbt_ms);
    const heapPct = improvementPct(b.js_heap_mb, p.js_heap_mb);

    const routeScoreParts = [lcpPct, fcpPct, loadPct, apiPct].filter((v) => v != null);
    if (routeScoreParts.length) {
      compositeSamples.push(median(routeScoreParts));
    }

    routeDiffs[route] = {
      base: b,
      pr: p,
      improvement_pct: {
        lcp: lcpPct,
        fcp: fcpPct,
        load: loadPct,
        tbt: tbtPct,
        api_median: apiPct,
        js_heap: heapPct,
      },
      api_requests_delta:
        b.api_requests != null && p.api_requests != null ? Number((p.api_requests - b.api_requests).toFixed(1)) : null,
    };
  }

  console.log(
    JSON.stringify(
      {
        status: "ok",
        runs_per_route: args.runs,
        base_url: args.baseUrl,
        pr_url: args.prUrl,
        median_runtime_improvement_pct: median(compositeSamples),
        routes: routeDiffs,
      },
      null,
      2,
    ),
  );
}

main();
