#!/usr/bin/env node
/**
 * Scientific live Playwright Web Vitals, V8 CPU, JS heap & API benchmarker.
 *
 * Uses interleaved A/B runs (2 warmups + N alternating pairs) across baseline
 * and PR preview URLs, reporting medians, IQR, Cliff's delta, and two-sided
 * Mann-Whitney U p-values to distinguish real changes from noise.
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
    runs: 7,
    timeoutMs: 25_000,
    login: false,
  };
  for (let i = 2; i < argv.length; i += 1) {
    const k = argv[i];
    if (k === "--repo") args.repo = argv[++i];
    else if (k === "--base-url") args.baseUrl = argv[++i];
    else if (k === "--pr-url") args.prUrl = argv[++i];
    else if (k === "--routes") args.routes = argv[++i].split(",").map((r) => r.trim()).filter(Boolean);
    else if (k === "--runs") args.runs = Math.max(3, Number(argv[++i]) || 7);
    else if (k === "--timeout-ms") args.timeoutMs = Math.max(5000, Number(argv[++i]) || 25_000);
    else if (k === "--login") args.login = true;
  }
  return args;
}

function findPlaywrightEntry(repo) {
  const candidates = [
    path.join(repo, "frontend", "node_modules", "playwright", "index.mjs"),
    path.join(repo, "frontend", "node_modules", "playwright", "index.js"),
    path.join(repo, "node_modules", "playwright", "index.mjs"),
    path.join(repo, "node_modules", "playwright", "index.js"),
  ];
  try {
    const common = execFileSync("git", ["rev-parse", "--git-common-dir"], {
      cwd: repo,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    }).trim();
    const mainRepo = path.dirname(path.resolve(repo, common));
    candidates.push(
      path.join(mainRepo, "frontend", "node_modules", "playwright", "index.mjs"),
      path.join(mainRepo, "frontend", "node_modules", "playwright", "index.js"),
      path.join(mainRepo, "node_modules", "playwright", "index.mjs"),
      path.join(mainRepo, "node_modules", "playwright", "index.js"),
    );
  } catch {}
  return candidates.find((p) => fs.existsSync(p)) || null;
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

function erfc(x) {
  const z = Math.abs(x);
  const t = 1 / (1 + 0.5 * z);
  const r =
    t *
    Math.exp(
      -z * z -
        1.26551223 +
        t *
          (1.00002368 +
            t *
              (0.37409196 +
                t *
                  (0.09678418 +
                    t *
                      (-0.18628806 +
                        t *
                          (0.27886807 +
                            t * (-1.13520398 + t * (1.48851587 + t * (-0.82215223 + t * 0.17087277)))))))),
    );
  return x >= 0 ? r : 2 - r;
}

function percentile(values, pct) {
  if (!values.length) return 0;
  const s = [...values].sort((a, b) => a - b);
  const pos = (pct / 100) * (s.length - 1);
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  return lo === hi ? s[lo] : s[lo] * (1 - (pos - lo)) + s[hi] * (pos - lo);
}

function summarize(values) {
  const nums = values.filter((v) => typeof v === "number" && Number.isFinite(v));
  if (!nums.length) return { n: 0, median: null, mean: null, iqr: null, p95: null };
  const mean = nums.reduce((a, b) => a + b, 0) / nums.length;
  return {
    n: nums.length,
    median: Number(percentile(nums, 50).toFixed(3)),
    mean: Number(mean.toFixed(3)),
    iqr: Number((percentile(nums, 75) - percentile(nums, 25)).toFixed(3)),
    p95: Number(percentile(nums, 95).toFixed(3)),
  };
}

function mannWhitneyP(a, b) {
  const n1 = a.length;
  const n2 = b.length;
  if (n1 < 2 || n2 < 2) return 1.0;
  const combined = [...a.map((v) => [v, 0]), ...b.map((v) => [v, 1])].sort((x, y) => x[0] - y[0]);
  let ranksA = 0;
  let tieTerm = 0;
  const n = n1 + n2;
  let i = 0;
  while (i < n) {
    let j = i + 1;
    while (j < n && combined[j][0] === combined[i][0]) j += 1;
    const avgRank = (i + 1 + j) / 2;
    const tc = j - i;
    if (tc > 1) tieTerm += tc ** 3 - tc;
    for (let k = i; k < j; k += 1) {
      if (combined[k][1] === 0) ranksA += avgRank;
    }
    i = j;
  }
  const u1 = ranksA - (n1 * (n1 + 1)) / 2;
  const u = Math.min(u1, n1 * n2 - u1);
  const meanU = (n1 * n2) / 2;
  const varU = ((n1 * n2) / 12) * (n + 1 - tieTerm / (n * (n - 1)));
  if (varU <= 0) return 1.0;
  const z = (Math.abs(u - meanU) - 0.5) / Math.sqrt(varU);
  if (z <= 0) return 1.0;
  return Math.max(0, Math.min(1, erfc(z / Math.SQRT2)));
}

function cliffsDelta(base, pr) {
  let g = 0;
  let l = 0;
  for (const x of base) {
    for (const y of pr) {
      if (x > y) g += 1;
      else if (x < y) l += 1;
    }
  }
  return (g - l) / (base.length * pr.length);
}

function compareAB(baseVals, prVals) {
  const b = baseVals.filter((v) => typeof v === "number" && Number.isFinite(v));
  const p = prVals.filter((v) => typeof v === "number" && Number.isFinite(v));
  const bSum = summarize(b);
  const pSum = summarize(p);
  if (b.length < 3 || p.length < 3) {
    return { base: bSum, pr: pSum, improvement_pct: null, p_value: null, cliffs_delta: null, verdict: "insufficient_samples", significant: false };
  }
  const imp = bSum.median === 0 ? (pSum.median === 0 ? 0 : -100) : ((bSum.median - pSum.median) / bSum.median) * 100;
  const pVal = mannWhitneyP(b, p);
  const effect = cliffsDelta(b, p);
  const significant = pVal < 0.05 && Math.abs(imp) >= 2.0 && Math.abs(effect) >= 0.33;
  const verdict = significant ? (imp > 0 ? "significant_improvement" : "significant_regression") : "no_significant_change";
  return {
    base: bSum,
    pr: pSum,
    improvement_pct: Number(imp.toFixed(2)),
    p_value: Number(pVal.toFixed(4)),
    cliffs_delta: Number(effect.toFixed(3)),
    verdict,
    significant,
  };
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
    window.__perfMetrics = { fcp: null, lcp: null, cls: 0, tbt: 0 };
    try {
      new PerformanceObserver((list) => {
        for (const e of list.getEntries()) {
          if (e.name === "first-contentful-paint") window.__perfMetrics.fcp = e.startTime;
        }
      }).observe({ type: "paint", buffered: true });
    } catch {}
    try {
      new PerformanceObserver((list) => {
        const entries = list.getEntries();
        if (entries.length) window.__perfMetrics.lcp = entries[entries.length - 1].startTime;
      }).observe({ type: "largest-contentful-paint", buffered: true });
    } catch {}
    try {
      new PerformanceObserver((list) => {
        for (const e of list.getEntries()) {
          if (!e.hadRecentInput) window.__perfMetrics.cls += e.value;
        }
      }).observe({ type: "layout-shift", buffered: true });
    } catch {}
    try {
      new PerformanceObserver((list) => {
        for (const e of list.getEntries()) {
          if (e.duration > 50) window.__perfMetrics.tbt += e.duration - 50;
        }
      }).observe({ type: "longtask", buffered: true });
    } catch {}
  });

  const apiTimings = [];
  const reqStart = new Map();
  page.on("request", (req) => {
    const rt = req.resourceType();
    if (rt === "fetch" || rt === "xhr") reqStart.set(req, performance.now());
  });
  page.on("response", (res) => {
    const req = res.request();
    const rt = req.resourceType();
    if ((rt === "fetch" || rt === "xhr") && reqStart.has(req)) {
      apiTimings.push(performance.now() - reqStart.get(req));
      reqStart.delete(req);
    }
  });

  const targetUrl = origin.replace(/\/$/, "") + (route.startsWith("/") ? route : `/${route}`);
  await page.goto(targetUrl, { waitUntil: "networkidle", timeout: timeoutMs }).catch(() =>
    page.waitForLoadState("load", { timeout: timeoutMs }).catch(() => {}),
  );
  await page.waitForTimeout(350);

  const webVitals = await page.evaluate(() => {
    const nav = performance.getEntriesByType("navigation")[0];
    const m = window.__perfMetrics || {};
    return {
      ttfb_ms: nav ? Math.max(0, nav.responseStart - nav.requestStart) : null,
      load_ms: nav ? nav.loadEventEnd : null,
      fcp_ms: m.fcp,
      lcp_ms: m.lcp,
      cls: Number((m.cls || 0).toFixed(4)),
      tbt_ms: m.tbt || 0,
    };
  });

  const cdpMetrics = await cdp.send("Performance.getMetrics");
  const mMap = Object.fromEntries(cdpMetrics.metrics.map((m) => [m.name, m.value]));
  await page.close();

  return {
    ...webVitals,
    script_duration_ms: mMap.ScriptDuration != null ? mMap.ScriptDuration * 1000 : null,
    js_heap_mb: mMap.JSHeapUsedSize != null ? mMap.JSHeapUsedSize / (1024 * 1024) : null,
    api_requests: apiTimings.length,
    api_median_ms: apiTimings.length ? percentile(apiTimings, 50) : null,
  };
}

async function main() {
  const args = parseArgs(process.argv);
  if (!args.baseUrl || !args.prUrl) {
    console.log(JSON.stringify({ status: "skipped", reason: "No --base-url and --pr-url provided" }));
    return;
  }

  const pwEntry = findPlaywrightEntry(args.repo);
  if (!pwEntry) {
    console.log(JSON.stringify({ status: "skipped", reason: "Playwright not found in node_modules" }));
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
    console.log(JSON.stringify({ status: "skipped", reason: "Playwright chromium unavailable" }));
    return;
  }

  const creds = args.login ? readEnvCredentials(args.repo) : { user: null, pass: null };
  const browser = await chromium.launch({ headless: true });
  try {
    const baseCtx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const prCtx = await browser.newContext({ viewport: { width: 1440, height: 900 } });

    if (args.login) {
      const bp = await baseCtx.newPage();
      await tryLogin(bp, args.baseUrl, creds, args.timeoutMs).catch(() => {});
      await bp.close();
      const pp = await prCtx.newPage();
      await tryLogin(pp, args.prUrl, creds, args.timeoutMs).catch(() => {});
      await pp.close();
    }

    const routesOut = {};
    for (const route of args.routes) {
      // 2 interleaved warmups.
      for (let w = 0; w < 2; w += 1) {
        await measureRouteOnce(baseCtx, args.baseUrl, route, args.timeoutMs);
        await measureRouteOnce(prCtx, args.prUrl, route, args.timeoutMs);
      }
      const bSamples = [];
      const pSamples = [];
      for (let i = 0; i < args.runs; i += 1) {
        if (i % 2 === 0) {
          bSamples.push(await measureRouteOnce(baseCtx, args.baseUrl, route, args.timeoutMs));
          pSamples.push(await measureRouteOnce(prCtx, args.prUrl, route, args.timeoutMs));
        } else {
          pSamples.push(await measureRouteOnce(prCtx, args.prUrl, route, args.timeoutMs));
          bSamples.push(await measureRouteOnce(baseCtx, args.baseUrl, route, args.timeoutMs));
        }
      }
      const metrics = {};
      for (const key of ["ttfb_ms", "fcp_ms", "lcp_ms", "tbt_ms", "load_ms", "script_duration_ms", "js_heap_mb", "api_median_ms", "api_requests"]) {
        metrics[key] = compareAB(
          bSamples.map((s) => s[key]),
          pSamples.map((s) => s[key]),
        );
      }
      routesOut[route] = { runs: args.runs, metrics };
    }

    console.log(
      JSON.stringify(
        {
          status: "ok",
          design: `interleaved A/B live Playwright (2 warmups + ${args.runs} measured pairs)`,
          base_url: args.baseUrl,
          pr_url: args.prUrl,
          runs_per_route: args.runs,
          routes: routesOut,
        },
        null,
        2,
      ),
    );
  } catch (err) {
    console.log(JSON.stringify({ status: "skipped", reason: `Live browser bench failed: ${String(err).slice(0, 400)}` }));
  } finally {
    await browser.close();
  }
}

main();
