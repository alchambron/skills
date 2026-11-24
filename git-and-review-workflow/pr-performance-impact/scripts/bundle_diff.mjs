#!/usr/bin/env node
/**
 * Scientific frontend bundle diff + zero-jitter localhost Playwright benchmark.
 *
 * 1. Builds PR base & head with Vite inside ephemeral detached git worktrees
 *    (reusing existing node_modules; auto-cleaned on exit).
 * 2. Measures exact raw, gzip, brotli, initial entry chunk, and top chunk diffs.
 * 3. Serves both built dist/ directories locally on 127.0.0.1 with gzip and runs
 *    interleaved A/B Playwright Chromium measurements (2 warmups + N pairs) for
 *    V8 ScriptDuration, TaskDuration, JSHeapUsedSize, FCP, DOMInteractive, TBT,
 *    with Mann-Whitney U p-values and Cliff's delta effect size.
 */

import { execFileSync } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import zlib from "node:zlib";

function parseArgs(argv) {
  const args = { repo: process.cwd(), base: null, head: null, runs: 7, skipLocalBrowser: false };
  for (let i = 2; i < argv.length; i += 1) {
    const k = argv[i];
    if (k === "--repo") args.repo = argv[++i];
    else if (k === "--base") args.base = argv[++i];
    else if (k === "--head") args.head = argv[++i];
    else if (k === "--runs") args.runs = Math.max(3, Number(argv[++i]) || 7);
    else if (k === "--no-local-browser") args.skipLocalBrowser = true;
  }
  return args;
}

function git(repo, gitArgs) {
  return execFileSync("git", gitArgs, {
    cwd: repo,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  }).trim();
}

function detectFrontendLayout(repo) {
  const subdir = fs.existsSync(path.join(repo, "frontend", "package.json")) ? "frontend" : "";
  const candidates = [];
  if (subdir) candidates.push(path.join(repo, "frontend", "node_modules"));
  else candidates.push(path.join(repo, "node_modules"));
  try {
    const common = path.resolve(repo, git(repo, ["rev-parse", "--git-common-dir"]));
    const mainRepo = path.dirname(common);
    candidates.push(subdir ? path.join(mainRepo, "frontend", "node_modules") : path.join(mainRepo, "node_modules"));
  } catch {
    // Ignore git common dir errors.
  }
  const nodeModules = candidates.find((c) => fs.existsSync(path.join(c, ".bin", "vite"))) || null;
  return { subdir, nodeModules };
}

function normalizeAssetName(relPath) {
  return relPath.replace(/-[A-Za-z0-9_-]{6,12}(\.[a-z0-9]+)$/i, "$1");
}

function walkFiles(dir, baseDir = dir) {
  const out = [];
  if (!fs.existsSync(dir)) return out;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walkFiles(full, baseDir));
    else if (entry.isFile()) out.push(path.relative(baseDir, full));
  }
  return out;
}

function extractEntryAssets(distDir) {
  const htmlPath = path.join(distDir, "index.html");
  if (!fs.existsSync(htmlPath)) return new Set();
  const html = fs.readFileSync(htmlPath, "utf8");
  const assets = new Set();
  const re = /(?:src|href)=["']\/?([^"']+\.(?:js|css))["']/gi;
  let m;
  while ((m = re.exec(html)) !== null) {
    assets.add(m[1].replace(/^\.\//, ""));
  }
  return assets;
}

function measureDist(distDir) {
  const files = walkFiles(distDir);
  const entryAssets = extractEntryAssets(distDir);
  let totalJsRaw = 0;
  let totalJsGzip = 0;
  let totalJsBrotli = 0;
  let totalCssRaw = 0;
  let totalCssGzip = 0;
  let entryRaw = 0;
  let entryGzip = 0;
  let jsChunks = 0;
  const chunks = {};

  for (const rel of files) {
    const ext = path.extname(rel).toLowerCase();
    if (ext !== ".js" && ext !== ".css") continue;
    const buf = fs.readFileSync(path.join(distDir, rel));
    const raw = buf.length;
    const gz = zlib.gzipSync(buf, { level: 9 }).length;
    const br = ext === ".js" ? zlib.brotliCompressSync(buf).length : 0;

    if (ext === ".js") {
      totalJsRaw += raw;
      totalJsGzip += gz;
      totalJsBrotli += br;
      jsChunks += 1;
    } else {
      totalCssRaw += raw;
      totalCssGzip += gz;
    }
    if (entryAssets.has(rel)) {
      entryRaw += raw;
      entryGzip += gz;
    }
    const norm = normalizeAssetName(rel);
    if (!chunks[norm]) chunks[norm] = { raw: 0, gzip: 0 };
    chunks[norm].raw += raw;
    chunks[norm].gzip += gz;
  }

  return {
    js_chunks: jsChunks,
    total_js_raw: totalJsRaw,
    total_js_gzip: totalJsGzip,
    total_js_brotli: totalJsBrotli,
    total_css_raw: totalCssRaw,
    total_css_gzip: totalCssGzip,
    entry_raw: entryRaw,
    entry_gzip: entryGzip,
    chunks,
  };
}

function pctDelta(before, after) {
  if (!before) return 0;
  return Number((((after - before) / before) * 100).toFixed(2));
}

// --- Statistics helpers (matching stats_util.py) ---

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

// --- Static gzip server on 127.0.0.1 for zero-jitter localhost Playwright benchmarking ---

const MIME_MAP = {
  ".html": "text/html; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".mjs": "application/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".woff2": "font/woff2",
  ".ico": "image/x-icon",
};

function startDistServer(distDir) {
  return new Promise((resolve, reject) => {
    const cache = new Map();
    const server = http.createServer((req, res) => {
      const urlPath = (req.url || "/").split("?")[0];
      const safeRel = path.normalize(urlPath).replace(/^(\.\.[/\\])+/, "").replace(/^[/\\]+/, "");
      let target = path.join(distDir, safeRel || "index.html");
      if (!fs.existsSync(target) || fs.statSync(target).isDirectory()) {
        target = path.join(distDir, "index.html");
      }
      if (!fs.existsSync(target)) {
        res.writeHead(404);
        res.end();
        return;
      }
      const ext = path.extname(target).toLowerCase();
      let entry = cache.get(target);
      if (!entry) {
        const raw = fs.readFileSync(target);
        const gz = zlib.gzipSync(raw, { level: 6 });
        entry = { gz, mime: MIME_MAP[ext] || "application/octet-stream" };
        cache.set(target, entry);
      }
      res.writeHead(200, {
        "Content-Type": entry.mime,
        "Content-Encoding": "gzip",
        "Cache-Control": "no-store",
      });
      res.end(entry.gz);
    });
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const addr = server.address();
      resolve({ server, url: `http://127.0.0.1:${addr.port}` });
    });
  });
}

async function measureLocalBootOnce(browser, origin) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const cdp = await context.newCDPSession(page);
  await cdp.send("Performance.enable");

  // Abort external or backend API requests immediately so measurements isolate
  // pure frontend bundle parse, compile, hydrate, and initial render cost.
  await page.route("**/*", (route) => {
    const reqUrl = route.request().url();
    if (!reqUrl.startsWith(origin) || reqUrl.includes("/api/")) {
      return route.abort();
    }
    return route.continue();
  });

  await page.addInitScript(() => {
    window.__bootPerf = { fcp: null, tbt: 0 };
    try {
      new PerformanceObserver((list) => {
        for (const e of list.getEntries()) {
          if (e.name === "first-contentful-paint") window.__bootPerf.fcp = e.startTime;
        }
      }).observe({ type: "paint", buffered: true });
    } catch {}
    try {
      new PerformanceObserver((list) => {
        for (const e of list.getEntries()) {
          if (e.duration > 50) window.__bootPerf.tbt += e.duration - 50;
        }
      }).observe({ type: "longtask", buffered: true });
    } catch {}
  });

  await page.goto(origin, { waitUntil: "load", timeout: 20_000 });
  await page.waitForTimeout(250);

  const navMetrics = await page.evaluate(() => {
    const nav = performance.getEntriesByType("navigation")[0];
    const m = window.__bootPerf || {};
    return {
      fcp_ms: m.fcp,
      dom_interactive_ms: nav ? nav.domInteractive : null,
      tbt_ms: m.tbt || 0,
    };
  });

  const cdpRes = await cdp.send("Performance.getMetrics");
  const mMap = Object.fromEntries(cdpRes.metrics.map((m) => [m.name, m.value]));

  await context.close();

  return {
    script_duration_ms: mMap.ScriptDuration != null ? mMap.ScriptDuration * 1000 : null,
    task_duration_ms: mMap.TaskDuration != null ? mMap.TaskDuration * 1000 : null,
    js_heap_mb: mMap.JSHeapUsedSize != null ? mMap.JSHeapUsedSize / (1024 * 1024) : null,
    fcp_ms: navMetrics.fcp_ms,
    dom_interactive_ms: navMetrics.dom_interactive_ms,
    tbt_ms: navMetrics.tbt_ms,
  };
}

async function runLocalhostBrowserBench(nodeModulesPath, baseDist, headDist, runs) {
  const pwCandidates = [
    path.join(nodeModulesPath, "playwright", "index.mjs"),
    path.join(nodeModulesPath, "playwright", "index.js"),
  ];
  const pwEntry = pwCandidates.find((p) => fs.existsSync(p));
  if (!pwEntry) {
    return { status: "skipped", reason: "Playwright not installed in node_modules" };
  }

  let playwright;
  try {
    playwright = await import(pathToFileURL(pwEntry).href);
  } catch (err) {
    return { status: "skipped", reason: `Failed to import Playwright: ${err}` };
  }
  const chromium = playwright.chromium || playwright.default?.chromium;
  if (!chromium) {
    return { status: "skipped", reason: "Playwright chromium unavailable" };
  }

  const baseSrv = await startDistServer(baseDist);
  const headSrv = await startDistServer(headDist);
  let browser;
  try {
    browser = await chromium.launch({ headless: true });

    // 2 interleaved warmup pairs (discarded).
    for (let w = 0; w < 2; w += 1) {
      await measureLocalBootOnce(browser, baseSrv.url);
      await measureLocalBootOnce(browser, headSrv.url);
    }

    const baseSamples = [];
    const headSamples = [];
    for (let i = 0; i < runs; i += 1) {
      if (i % 2 === 0) {
        baseSamples.push(await measureLocalBootOnce(browser, baseSrv.url));
        headSamples.push(await measureLocalBootOnce(browser, headSrv.url));
      } else {
        headSamples.push(await measureLocalBootOnce(browser, headSrv.url));
        baseSamples.push(await measureLocalBootOnce(browser, baseSrv.url));
      }
    }

    const metrics = {};
    for (const key of ["script_duration_ms", "task_duration_ms", "js_heap_mb", "fcp_ms", "dom_interactive_ms", "tbt_ms"]) {
      metrics[key] = compareAB(
        baseSamples.map((s) => s[key]),
        headSamples.map((s) => s[key]),
      );
    }
    return {
      status: "ok",
      design: `interleaved A/B on 127.0.0.1 (2 warmups + ${runs} measured pairs, fresh context per run)`,
      runs,
      metrics,
    };
  } catch (err) {
    return { status: "skipped", reason: `Localhost browser bench failed: ${String(err).slice(0, 300)}` };
  } finally {
    if (browser) await browser.close().catch(() => {});
    baseSrv.server.close();
    headSrv.server.close();
  }
}

function buildRef(repo, ref, subdir, nodeModulesPath, label) {
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), `pr-perf-bundle-${label}-`));
  const wtDir = path.join(tmpRoot, "wt");
  const outDir = path.join(tmpRoot, "dist");
  let worktreeAdded = false;

  try {
    git(repo, ["worktree", "add", "--detach", "--no-checkout", wtDir, ref]);
    worktreeAdded = true;
    if (subdir) {
      git(wtDir, ["sparse-checkout", "init", "--cone"]);
      git(wtDir, ["sparse-checkout", "set", subdir]);
    }
    git(wtDir, ["checkout", "--force", ref]);

    const feDir = subdir ? path.join(wtDir, subdir) : wtDir;
    const targetNm = path.join(feDir, "node_modules");
    if (!fs.existsSync(targetNm)) {
      fs.symlinkSync(nodeModulesPath, targetNm, "dir");
    }

    const viteBin = path.join(nodeModulesPath, ".bin", "vite");
    const t0 = performance.now();
    execFileSync(viteBin, ["build", "--outDir", outDir, "--emptyOutDir", "--logLevel", "error"], {
      cwd: feDir,
      env: { ...process.env, VITE_DISABLE_STORYBOOK: "1", NODE_ENV: "production" },
      stdio: ["ignore", "pipe", "pipe"],
      timeout: 180_000,
    });
    const buildMs = Math.round(performance.now() - t0);
    return { ok: true, tmpRoot, wtDir, outDir, build_ms: buildMs, metrics: measureDist(outDir) };
  } catch (err) {
    if (worktreeAdded) {
      try {
        git(repo, ["worktree", "remove", "--force", wtDir]);
      } catch {}
    }
    fs.rmSync(tmpRoot, { recursive: true, force: true });
    return { ok: false, error: (err && err.stderr ? String(err.stderr) : String(err)).slice(0, 600) };
  }
}

function cleanupBuild(repo, buildRes) {
  if (!buildRes || !buildRes.tmpRoot) return;
  try {
    git(repo, ["worktree", "remove", "--force", buildRes.wtDir]);
  } catch {}
  fs.rmSync(buildRes.tmpRoot, { recursive: true, force: true });
}

async function main() {
  const args = parseArgs(process.argv);
  if (!args.base || !args.head) {
    console.log(JSON.stringify({ status: "error", reason: "Missing --base or --head" }));
    process.exit(1);
  }

  const { subdir, nodeModules } = detectFrontendLayout(args.repo);
  if (!nodeModules) {
    console.log(JSON.stringify({ status: "skipped", reason: "Vite node_modules not found in worktree or main checkout" }));
    return;
  }

  let lockfileChanged = false;
  try {
    const pkgFiles = subdir ? [`${subdir}/package-lock.json`, `${subdir}/package.json`] : ["package-lock.json", "package.json"];
    const diffNames = git(args.repo, ["diff", "--name-only", `${args.base}...${args.head}`, "--", ...pkgFiles]);
    lockfileChanged = diffNames.trim().length > 0;
  } catch {}

  const baseRes = buildRef(args.repo, args.base, subdir, nodeModules, "base");
  if (!baseRes.ok) {
    console.log(JSON.stringify({ status: "skipped", reason: `Base vite build failed: ${baseRes.error}` }));
    return;
  }

  const headRes = buildRef(args.repo, args.head, subdir, nodeModules, "head");
  if (!headRes.ok) {
    cleanupBuild(args.repo, baseRes);
    console.log(JSON.stringify({ status: "skipped", reason: `Head vite build failed: ${headRes.error}` }));
    return;
  }

  try {
    const b = baseRes.metrics;
    const h = headRes.metrics;

    const allChunkNames = new Set([...Object.keys(b.chunks), ...Object.keys(h.chunks)]);
    const chunkDiffs = [];
    for (const name of allChunkNames) {
      const bGz = b.chunks[name]?.gzip || 0;
      const hGz = h.chunks[name]?.gzip || 0;
      const delta = hGz - bGz;
      if (Math.abs(delta) >= 256) {
        chunkDiffs.push({
          chunk: name,
          base_gzip_kb: Number((bGz / 1024).toFixed(2)),
          head_gzip_kb: Number((hGz / 1024).toFixed(2)),
          delta_gzip_kb: Number((delta / 1024).toFixed(2)),
          delta_pct: bGz ? pctDelta(bGz, hGz) : null,
        });
      }
    }
    chunkDiffs.sort((x, y) => Math.abs(y.delta_gzip_kb) - Math.abs(x.delta_gzip_kb));

    const entryReductionPct = b.entry_gzip ? Number((((b.entry_gzip - h.entry_gzip) / b.entry_gzip) * 100).toFixed(2)) : 0;
    const totalJsGzipReductionPct = b.total_js_gzip
      ? Number((((b.total_js_gzip - h.total_js_gzip) / b.total_js_gzip) * 100).toFixed(2))
      : 0;

    const localBrowser = args.skipLocalBrowser
      ? { status: "skipped", reason: "Disabled via --no-local-browser" }
      : await runLocalhostBrowserBench(nodeModules, baseRes.outDir, headRes.outDir, args.runs);

    console.log(
      JSON.stringify(
        {
          status: "ok",
          lockfile_changed_caveat: lockfileChanged,
          build_ms: { base: baseRes.build_ms, head: headRes.build_ms },
          entry_gzip_kb: {
            base: Number((b.entry_gzip / 1024).toFixed(2)),
            head: Number((h.entry_gzip / 1024).toFixed(2)),
            delta_kb: Number(((h.entry_gzip - b.entry_gzip) / 1024).toFixed(2)),
            delta_pct: pctDelta(b.entry_gzip, h.entry_gzip),
            reduction_pct: entryReductionPct,
          },
          total_js_gzip_kb: {
            base: Number((b.total_js_gzip / 1024).toFixed(2)),
            head: Number((h.total_js_gzip / 1024).toFixed(2)),
            delta_kb: Number(((h.total_js_gzip - b.total_js_gzip) / 1024).toFixed(2)),
            delta_pct: pctDelta(b.total_js_gzip, h.total_js_gzip),
            reduction_pct: totalJsGzipReductionPct,
          },
          total_js_brotli_kb: {
            base: Number((b.total_js_brotli / 1024).toFixed(2)),
            head: Number((h.total_js_brotli / 1024).toFixed(2)),
            delta_kb: Number(((h.total_js_brotli - b.total_js_brotli) / 1024).toFixed(2)),
          },
          total_css_gzip_kb: {
            base: Number((b.total_css_gzip / 1024).toFixed(2)),
            head: Number((h.total_css_gzip / 1024).toFixed(2)),
            delta_kb: Number(((h.total_css_gzip - b.total_css_gzip) / 1024).toFixed(2)),
          },
          js_chunks: { base: b.js_chunks, head: h.js_chunks, delta: h.js_chunks - b.js_chunks },
          top_chunk_diffs: chunkDiffs.slice(0, 5),
          local_browser: localBrowser,
        },
        null,
        2,
      ),
    );
  } finally {
    cleanupBuild(args.repo, baseRes);
    cleanupBuild(args.repo, headRes);
  }
}

main();
