#!/usr/bin/env node
/**
 * Precision frontend bundle diff (raw, gzip, brotli, initial entry chunk, chunk deltas).
 * Builds base and head inside ephemeral detached git worktrees without touching
 * the user's checkout or requiring a fresh npm install.
 */

import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import zlib from "node:zlib";

function parseArgs(argv) {
  const args = { repo: process.cwd(), base: null, head: null };
  for (let i = 2; i < argv.length; i += 1) {
    const key = argv[i];
    if (key === "--repo") args.repo = argv[++i];
    else if (key === "--base") args.base = argv[++i];
    else if (key === "--head") args.head = argv[++i];
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

function findFrontendNodeModules(repo) {
  const local = path.join(repo, "frontend", "node_modules");
  if (fs.existsSync(path.join(local, ".bin", "vite"))) {
    return local;
  }
  try {
    const commonGitDir = path.resolve(repo, git(repo, ["rev-parse", "--git-common-dir"]));
    const mainRepo = path.dirname(commonGitDir);
    const shared = path.join(mainRepo, "frontend", "node_modules");
    if (fs.existsSync(path.join(shared, ".bin", "vite"))) {
      return shared;
    }
  } catch {
    // Fall through to null.
  }
  return null;
}

function normalizeAssetName(relPath) {
  // Strip Vite content hashes (e.g. assets/index-Ab12Cd34.js -> assets/index.js).
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
    if (!chunks[norm]) {
      chunks[norm] = { raw: 0, gzip: 0 };
    }
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

function buildRef(repo, ref, nodeModulesPath, label) {
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), `joulemv-bundle-${label}-`));
  const wtDir = path.join(tmpRoot, "wt");
  const outDir = path.join(tmpRoot, "dist");
  let worktreeAdded = false;

  try {
    git(repo, ["worktree", "add", "--detach", "--no-checkout", wtDir, ref]);
    worktreeAdded = true;
    // Sparse checkout only frontend/ + root config files so setup takes <1s.
    git(wtDir, ["sparse-checkout", "init", "--cone"]);
    git(wtDir, ["sparse-checkout", "set", "frontend"]);
    git(wtDir, ["checkout", "--force", ref]);

    const feDir = path.join(wtDir, "frontend");
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
    return { ok: true, build_ms: buildMs, metrics: measureDist(outDir) };
  } catch (err) {
    return { ok: false, error: (err && err.stderr ? String(err.stderr) : String(err)).slice(0, 600) };
  } finally {
    if (worktreeAdded) {
      try {
        git(repo, ["worktree", "remove", "--force", wtDir]);
      } catch {
        // Fallback cleanup below.
      }
    }
    fs.rmSync(tmpRoot, { recursive: true, force: true });
  }
}

function main() {
  const args = parseArgs(process.argv);
  if (!args.base || !args.head) {
    console.log(JSON.stringify({ status: "error", reason: "Missing --base or --head" }));
    process.exit(1);
  }

  const nodeModulesPath = findFrontendNodeModules(args.repo);
  if (!nodeModulesPath) {
    console.log(
      JSON.stringify({
        status: "skipped",
        reason: "frontend/node_modules (vite) not found in worktree or main checkout",
      }),
    );
    return;
  }

  // Check if lockfile changed (symlinked node_modules may not reflect newly added deps).
  let lockfileChanged = false;
  try {
    const diffNames = git(args.repo, ["diff", "--name-only", `${args.base}...${args.head}`, "--", "frontend/package-lock.json", "frontend/package.json"]);
    lockfileChanged = diffNames.trim().length > 0;
  } catch {
    lockfileChanged = false;
  }

  const baseRes = buildRef(args.repo, args.base, nodeModulesPath, "base");
  if (!baseRes.ok) {
    console.log(JSON.stringify({ status: "skipped", reason: `Base vite build failed: ${baseRes.error}` }));
    return;
  }

  const headRes = buildRef(args.repo, args.head, nodeModulesPath, "head");
  if (!headRes.ok) {
    console.log(JSON.stringify({ status: "skipped", reason: `Head vite build failed: ${headRes.error}` }));
    return;
  }

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

  // Positive improvement_pct means smaller bundle (better performance).
  const entryReductionPct = b.entry_gzip ? Number((((b.entry_gzip - h.entry_gzip) / b.entry_gzip) * 100).toFixed(2)) : 0;
  const totalJsGzipReductionPct = b.total_js_gzip
    ? Number((((b.total_js_gzip - h.total_js_gzip) / b.total_js_gzip) * 100).toFixed(2))
    : 0;

  const output = {
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
  };

  console.log(JSON.stringify(output, null, 2));
}

main();
