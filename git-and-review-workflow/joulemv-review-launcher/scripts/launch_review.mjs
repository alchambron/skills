#!/usr/bin/env node

import { randomUUID } from 'node:crypto';
import { spawn } from 'node:child_process';
import { readFile, realpath, stat } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const REPOSITORY = 'EnerZam/JouleMV';
const REPOSITORY_KEY = REPOSITORY.toLowerCase();
const PROJECT_CWD = '/Users/alchambron/Documents/Projects/Joule_MV/JouleMV';
const EXPECTED_BASE_BRANCH = 'work';
const SHA_PATTERN = /^[a-f0-9]{40}$/i;
const REVIEW_KINDS = new Set(['Review', 'Re-review']);

class LaunchError extends Error {
  constructor(code, message, details = {}) {
    super(message);
    this.name = 'LaunchError';
    this.code = code;
    this.details = details;
  }
}

function parseArguments(argv) {
  const options = { dryRun: false };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === '--dry-run') {
      options.dryRun = true;
    } else if (argument === '--pending-json' || argument === '--pr') {
      const value = argv[++index];
      if (!value || value.startsWith('--')) {
        throw new LaunchError('INVALID_ARGUMENTS', `${argument} requires a value.`);
      }
      options[argument === '--pr' ? 'prNumber' : 'pendingJson'] = value;
    } else {
      throw new LaunchError('INVALID_ARGUMENTS', `Unknown argument: ${argument}`);
    }
  }
  if (!options.pendingJson || !/^[1-9]\d*$/.test(options.prNumber ?? '')) {
    throw new LaunchError('INVALID_ARGUMENTS', 'Usage: launch_review.mjs --pending-json PATH --pr NUMBER [--dry-run]');
  }
  options.prNumber = Number(options.prNumber);
  return options;
}

function canonicalPrUrl(number) {
  return `https://github.com/${REPOSITORY}/pull/${number}`;
}

function reviewTarget(number, sha) {
  return `Review-Target: ${REPOSITORY}#${number}@${sha.toLowerCase()}`;
}

function selectedReview(pending, number) {
  if (!pending || !Array.isArray(pending.reviews)) {
    throw new LaunchError('INVALID_PENDING_LIST', 'Pending review JSON must contain a reviews array.');
  }
  const matches = pending.reviews.filter((entry) => entry?.number === number);
  if (matches.length !== 1) {
    throw new LaunchError('PR_NOT_SELECTED', `PR #${number} must appear exactly once in the pending review list.`);
  }
  const selected = matches[0];
  if (!REVIEW_KINDS.has(selected.kind) || !SHA_PATTERN.test(selected.headSha ?? '')) {
    throw new LaunchError('INVALID_PENDING_ENTRY', `PR #${number} has no valid review kind and head SHA.`);
  }
  if (selected.url !== canonicalPrUrl(number)) {
    throw new LaunchError('INVALID_PENDING_ENTRY', `PR #${number} has an unexpected URL.`);
  }
  return selected;
}

async function runProcess(command, args, { timeoutMs = 30_000 } = {}) {
  return await new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '';
    let stderr = '';
    const timeout = setTimeout(() => child.kill(), timeoutMs);
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stdout.on('data', (chunk) => { stdout += chunk; });
    child.stderr.on('data', (chunk) => { stderr += chunk; });
    child.on('error', () => {
      clearTimeout(timeout);
      reject(new LaunchError('PROCESS_FAILED', `${command} could not start.`));
    });
    child.on('close', (code) => {
      clearTimeout(timeout);
      if (code !== 0) {
        reject(new LaunchError('PROCESS_FAILED', `${command} exited unsuccessfully.`, {
          command,
          // Never echo stdout or stderr: the auth command returns a bearer token.
          exitCode: code,
        }));
      } else {
        resolve(stdout);
      }
    });
  });
}

async function freshPullRequest(number) {
  const output = await runProcess('gh', [
    'pr', 'view', String(number), '--repo', REPOSITORY,
    '--json', 'number,url,headRefOid,headRefName,state,isDraft',
  ]);
  let pr;
  try {
    pr = JSON.parse(output);
  } catch {
    throw new LaunchError('INVALID_GITHUB_RESPONSE', 'GitHub returned invalid PR data.');
  }
  if (pr.number !== number || pr.url !== canonicalPrUrl(number)
    || !SHA_PATTERN.test(pr.headRefOid ?? '') || !pr.headRefName) {
    throw new LaunchError('INVALID_GITHUB_RESPONSE', `GitHub returned unexpected data for PR #${number}.`);
  }
  return pr;
}

async function baseBranch(projectCwd) {
  const branch = (await runProcess('git', ['-C', projectCwd, 'branch', '--show-current'])).trim();
  if (branch !== EXPECTED_BASE_BRANCH) {
    throw new LaunchError('WRONG_BASE_BRANCH', `Primary checkout must be on ${EXPECTED_BASE_BRANCH}; found ${branch || 'detached HEAD'}.`);
  }
  return branch;
}

async function ensurePrCommit(projectCwd, selected, { dryRun = false } = {}) {
  const ref = `refs/pull/${selected.number}/head`;
  const remote = (await runProcess('git', ['-C', projectCwd, 'remote', 'get-url', 'origin'])).trim();
  if (!/^(?:git@github\.com:|https:\/\/github\.com\/|ssh:\/\/git@github\.com\/)(?:EnerZam\/JouleMV)(?:\.git)?$/i.test(remote)) {
    throw new LaunchError('WRONG_GIT_REMOTE', 'The primary checkout origin is not EnerZam/JouleMV.');
  }
  const remoteOutput = (await runProcess('git', ['-C', projectCwd, 'ls-remote', 'origin', ref], {
    timeoutMs: 60_000,
  })).trim();
  const remoteSha = remoteOutput.split(/\s+/)[0]?.toLowerCase();
  if (remoteSha !== selected.headSha.toLowerCase()) {
    throw new LaunchError('STALE_SELECTION', `PR #${selected.number} changed during launch. Refresh the pending list.`, {
      selectedSha: selected.headSha, remoteSha: SHA_PATTERN.test(remoteSha ?? '') ? remoteSha : null,
    });
  }
  const locallyAvailable = async () => {
    try {
      await runProcess('git', ['-C', projectCwd, 'cat-file', '-e', `${selected.headSha}^{commit}`]);
      return true;
    } catch {
      return false;
    }
  };
  if (await locallyAvailable()) return { local: true, fetched: false };
  if (dryRun) return { local: false, fetched: false };
  await runProcess('git', ['-C', projectCwd, 'fetch', '--no-tags', 'origin', ref], { timeoutMs: 180_000 });
  const fetchedSha = (await runProcess('git', ['-C', projectCwd, 'rev-parse', 'FETCH_HEAD'])).trim();
  if (fetchedSha.toLowerCase() !== selected.headSha.toLowerCase() || !(await locallyAvailable())) {
    throw new LaunchError('STALE_SELECTION', `PR #${selected.number} changed while fetching its head.`);
  }
  return { local: true, fetched: true };
}

async function discoverRuntime(t3Home = path.join(os.homedir(), '.t3')) {
  for (const stateDirectory of ['userdata', 'dev']) {
    let state;
    try {
      state = JSON.parse(await readFile(path.join(t3Home, stateDirectory, 'server-runtime.json'), 'utf8'));
    } catch {
      continue;
    }
    let origin;
    try {
      origin = new URL(state.origin);
    } catch {
      continue;
    }
    if (origin.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(origin.hostname)) continue;
    try {
      const response = await fetch(new URL('/.well-known/t3/environment', origin), {
        signal: AbortSignal.timeout(2_500),
      });
      if (!response.ok) continue;
      const descriptor = await response.json();
      if (typeof descriptor.environmentId === 'string' && /^[0-9A-Za-z.+-]+$/.test(descriptor.serverVersion ?? '')) {
        return { origin: origin.origin, t3Home, ...descriptor };
      }
    } catch {
      // A stale runtime file is common after an app restart.
    }
  }
  throw new LaunchError('T3_UNAVAILABLE', 'No running local T3 Code server was found.');
}

function officialT3Args(version, operation, t3Home, sessionId) {
  const prefix = ['exec', '--yes', `--package=t3@${version}`, '--', 't3', 'auth', 'session'];
  if (operation === 'issue') {
    return [...prefix, 'issue', '--json', '--ttl', '10m', '--label', 'joulemv-review-launcher',
      '--subject', 'joulemv-review-launcher', '--base-dir', t3Home];
  }
  return [...prefix, 'revoke', sessionId, '--base-dir', t3Home];
}

async function issueSession(runtime) {
  const output = await runProcess('npm', officialT3Args(runtime.serverVersion, 'issue', runtime.t3Home), {
    timeoutMs: 90_000,
  });
  let session;
  try {
    session = JSON.parse(output);
  } catch {
    throw new LaunchError('T3_AUTH_FAILED', 'T3 returned an invalid session response.');
  }
  if (typeof session.sessionId !== 'string' || typeof session.token !== 'string') {
    throw new LaunchError('T3_AUTH_FAILED', 'T3 did not issue a usable session.');
  }
  return session;
}

async function revokeSession(runtime, session) {
  await runProcess('npm', officialT3Args(runtime.serverVersion, 'revoke', runtime.t3Home, session.sessionId), {
    timeoutMs: 90_000,
  });
}

async function apiRequest(runtime, token, method, requestPath, payload) {
  let response;
  try {
    response = await fetch(new URL(requestPath, runtime.origin), {
      method,
      headers: {
        authorization: `Bearer ${token}`,
        ...(payload === undefined ? {} : { 'content-type': 'application/json' }),
      },
      ...(payload === undefined ? {} : { body: JSON.stringify(payload) }),
      signal: AbortSignal.timeout(method === 'POST' ? 360_000 : 30_000),
    });
  } catch {
    throw new LaunchError('T3_REQUEST_UNCERTAIN', `T3 ${method} ${requestPath} did not return. The command may have completed.`);
  }
  if (!response.ok) {
    throw new LaunchError('T3_API_ERROR', `T3 ${method} ${requestPath} returned HTTP ${response.status}.`, {
      status: response.status,
    });
  }
  return await response.json();
}

/** T3 processes atomic thread/worktree bootstrap only on its WebSocket RPC path. */
async function dispatchBootstrap(runtime, token, command) {
  const issued = await apiRequest(runtime, token, 'POST', '/api/auth/websocket-ticket');
  if (typeof issued.ticket !== 'string' || !issued.ticket) {
    throw new LaunchError('T3_AUTH_FAILED', 'T3 did not issue a WebSocket ticket.');
  }
  const url = new URL('/ws', runtime.origin);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  url.searchParams.set('wsTicket', issued.ticket);
  return await new Promise((resolve, reject) => {
    const socket = new WebSocket(url);
    socket.binaryType = 'arraybuffer';
    let settled = false;
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      socket.close();
      if (error) reject(error);
      else resolve(value);
    };
    const timer = setTimeout(() => finish(new LaunchError('T3_RPC_TIMEOUT',
      'T3 did not confirm the thread bootstrap within 10 minutes.')), 600_000);
    socket.addEventListener('open', () => {
      socket.send(JSON.stringify({
        _tag: 'Request', id: '1', tag: 'orchestration.dispatchCommand', payload: command, headers: [],
      }));
    });
    socket.addEventListener('message', (event) => {
      let response;
      try {
        const data = event.data;
        const text = typeof data === 'string' ? data : new TextDecoder().decode(data);
        response = JSON.parse(text);
      } catch {
        finish(new LaunchError('T3_RPC_PROTOCOL_ERROR', 'T3 returned an unreadable bootstrap response.'));
        return;
      }
      for (const message of Array.isArray(response) ? response : [response]) {
        if (message?._tag === 'Exit' && String(message.requestId) === '1') {
          const exit = message.exit ?? {};
          if (exit._tag === 'Success') {
            finish(null, exit.value);
          } else {
            const failure = Array.isArray(exit.cause)
              ? exit.cause.find((reason) => reason?._tag === 'Fail')?.error : null;
            finish(new LaunchError('T3_RPC_FAILED', 'T3 rejected the thread bootstrap.', {
              errorTag: typeof failure?._tag === 'string' ? failure._tag : null,
              bootstrapThreadDisposition: typeof failure?.bootstrapThreadDisposition === 'string'
                ? failure.bootstrapThreadDisposition : null,
            }));
          }
          return;
        }
        if (message?._tag === 'Defect' || message?._tag === 'ClientProtocolError') {
          finish(new LaunchError('T3_RPC_PROTOCOL_ERROR', 'T3 reported a bootstrap protocol error.'));
          return;
        }
      }
    });
    socket.addEventListener('error', () => {
      finish(new LaunchError('T3_REQUEST_UNCERTAIN', 'T3 WebSocket bootstrap connection failed.'));
    });
    socket.addEventListener('close', () => {
      finish(new LaunchError('T3_REQUEST_UNCERTAIN', 'T3 closed the bootstrap connection before confirming it.'));
    });
  });
}

function threadUrl(runtime, threadId) {
  return new URL(`/${encodeURIComponent(runtime.environmentId)}/${encodeURIComponent(threadId)}`, runtime.origin).toString();
}

function hasPrLink(thread, number) {
  return thread.pullRequests?.some((link) => link.host.toLowerCase() === 'github.com'
    && link.repository.toLowerCase() === REPOSITORY_KEY && link.number === number
    && link.source !== 'stack-dismissed') ?? false;
}

function hasTargetMarker(thread, marker) {
  return thread.messages?.some((message) => message.role === 'user' && message.text?.includes(marker)) ?? false;
}

async function findExistingReview(api, shell, projectId, number, sha) {
  const marker = reviewTarget(number, sha);
  const candidates = shell.threads.filter((thread) => thread.projectId === projectId
    && thread.deletedAt == null
    && (hasPrLink(thread, number) || thread.title.includes(`PR #${number}`)));
  for (const candidate of candidates) {
    const detail = await api('GET', `/api/orchestration/threads/${encodeURIComponent(candidate.id)}?turnLimit=100`);
    if (hasTargetMarker(detail.thread, marker)) return detail.thread;
  }
  return null;
}

async function verifyWorktree(projectCwd, worktreePath) {
  if (!worktreePath) return false;
  try {
    if (!(await stat(worktreePath)).isDirectory()) return false;
    const target = await realpath(worktreePath);
    const porcelain = await runProcess('git', ['-C', projectCwd, 'worktree', 'list', '--porcelain']);
    return porcelain.split('\n').some((line) => line.startsWith('worktree ')
      && path.resolve(line.slice('worktree '.length)) === target);
  } catch {
    return false;
  }
}

async function waitForThread(api, threadId, { timeoutMs = 60_000, requireReady = false } = {}) {
  const deadline = Date.now() + timeoutMs;
  let lastThread = null;
  while (Date.now() < deadline) {
    try {
      const detail = await api('GET', `/api/orchestration/threads/${encodeURIComponent(threadId)}?turnLimit=100`);
      if (detail.thread) {
        lastThread = detail.thread;
        if (!requireReady || (detail.thread.worktreePath && detail.thread.latestTurn)) return detail.thread;
      }
    } catch {
      // The bootstrap may still be creating the thread.
    }
    await new Promise((resolve) => setTimeout(resolve, 1_000));
  }
  return lastThread;
}

function buildPrompt(selected) {
  return `$joulemv-code-review ${selected.url}\n\n${reviewTarget(selected.number, selected.headSha)}\n\n` +
    `Review this PR at the selected head SHA ${selected.headSha}. Confirm the live PR head before reviewing. ` +
    'The worktree starts at this PR head; inspect the complete PR diff against its current base. ' +
    'Complete and report the read-only $joulemv-code-review first; do not modify files. ' +
    'If and only if that completed review has at least one verified actionable finding that warrants Request changes, ' +
    'then invoke $github-inline-review with no arguments in this thread to publish those findings as REQUEST_CHANGES and verify the result. ' +
    'If no finding survives validation, or the remaining observations are uncertain, publish no GitHub review or comment. ' +
    'Never submit APPROVE or mark the PR approved. A clean result is "no actionable findings".';
}

function buildBootstrapCommand({ project, projectCwd, branch, selected, runtimeMode = 'full-access',
  interactionMode = 'default', modelSelection, ids = {} }) {
  const threadId = ids.threadId ?? randomUUID();
  const commandId = ids.commandId ?? randomUUID();
  const messageId = ids.messageId ?? randomUUID();
  const createdAt = new Date().toISOString();
  const title = `Review PR #${selected.number} @${selected.headSha.slice(0, 12)}`;
  return {
    type: 'thread.turn.start', commandId, threadId,
    message: { messageId, role: 'user', text: buildPrompt(selected), attachments: [] },
    modelSelection, titleSeed: title, runtimeMode, interactionMode,
    bootstrap: {
      createThread: {
        projectId: project.id, title, modelSelection, runtimeMode, interactionMode,
        branch: selected.headRefName, worktreePath: null, createdAt,
      },
      prepareWorktree: {
        projectCwd, baseBranch: selected.headSha, startFromOrigin: false, requireWorktree: true,
      },
      runSetupScript: false,
    },
    createdAt,
  };
}

function linkCommand(threadId, selected) {
  return {
    type: 'thread.pull-request.link', commandId: randomUUID(), threadId,
    host: 'github.com', repository: REPOSITORY_KEY,
    number: selected.number, url: selected.url, source: 'manual',
  };
}

export async function launchReview(options, dependencies = {}) {
  const deps = {
    readPending: async (file) => JSON.parse(await readFile(file, 'utf8')),
    freshPullRequest,
    baseBranch,
    ensurePrCommit,
    discoverRuntime,
    issueSession,
    revokeSession,
    apiRequest,
    dispatchBootstrap,
    verifyWorktree,
    ...dependencies,
  };
  const projectCwd = options.projectCwd ?? PROJECT_CWD;
  const selected = selectedReview(await deps.readPending(options.pendingJson), options.prNumber);
  const fresh = await deps.freshPullRequest(selected.number);
  if (fresh.headRefOid.toLowerCase() !== selected.headSha.toLowerCase()) {
    throw new LaunchError('STALE_SELECTION', `PR #${selected.number} has a new head. Refresh the pending list before launching.`, {
      selectedSha: selected.headSha, currentSha: fresh.headRefOid,
    });
  }
  if (fresh.state !== 'OPEN' || fresh.isDraft) {
    throw new LaunchError('PR_NOT_REVIEWABLE', `PR #${selected.number} is no longer an open, non-draft PR.`);
  }
  const branch = await deps.baseBranch(projectCwd);
  const runtime = await deps.discoverRuntime();
  const session = await deps.issueSession(runtime);
  let result;
  let revokeFailure = false;
  try {
    const api = (method, requestPath, payload) => deps.apiRequest(runtime, session.token, method, requestPath, payload);
    const shell = await api('GET', '/api/orchestration/shell');
    const project = shell.projects.find((entry) => entry.workspaceRoot === projectCwd);
    if (!project) {
      throw new LaunchError('T3_PROJECT_NOT_FOUND', `T3 has no project for ${projectCwd}.`);
    }
    const existing = await findExistingReview(api, shell, project.id, selected.number, selected.headSha);
    if (existing) {
      result = {
        ok: true, status: 'already_exists', pr: selected.number, headSha: selected.headSha,
        threadId: existing.id, threadUrl: threadUrl(runtime, existing.id),
        worktreePath: existing.worktreePath, prLinked: hasPrLink(existing, selected.number),
      };
    } else {
      const modelSelection = project.defaultModelSelection;
      if (!modelSelection?.instanceId || !modelSelection?.model) {
        throw new LaunchError('T3_MODEL_UNSET', 'The T3 project has no default model selection.');
      }
      const selectedWithHead = { ...selected, headRefName: fresh.headRefName };
      const commit = await deps.ensurePrCommit(projectCwd, selected, { dryRun: options.dryRun });
      const command = buildBootstrapCommand({ project, projectCwd, branch, selected: selectedWithHead, modelSelection });
      const summary = {
        pr: selected.number, headSha: selected.headSha, projectId: project.id,
        baseBranch: branch, baseRef: selected.headSha, modelSelection, threadId: command.threadId,
        threadUrl: threadUrl(runtime, command.threadId),
      };
      if (options.dryRun) {
        result = { ok: true, status: 'dry_run', ...summary,
          commitLocal: commit.local, bootstrap: command.bootstrap.prepareWorktree,
          prompt: command.message.text };
      } else {
        let thread;
        try {
          await deps.dispatchBootstrap(runtime, session.token, command);
          thread = await waitForThread(api, command.threadId, { requireReady: true });
        } catch (error) {
          if (error.code === 'T3_RPC_FAILED'
            && ['deleted', 'not-created'].includes(error.details?.bootstrapThreadDisposition)) {
            throw new LaunchError('T3_BOOTSTRAP_FAILED', 'T3 rejected the worktree bootstrap and removed its thread.', {
              threadId: command.threadId,
              bootstrapThreadDisposition: error.details.bootstrapThreadDisposition,
            });
          }
          thread = await waitForThread(api, command.threadId, { timeoutMs: 360_000, requireReady: true });
          if (!thread) {
            throw new LaunchError('LAUNCH_UNCERTAIN', 'T3 did not confirm the new thread. Check the listed thread ID before retrying.', {
              threadId: command.threadId, commandId: command.commandId,
              causeCode: error.code ?? 'UNKNOWN',
            });
          }
        }
        if (!thread) {
          throw new LaunchError('LAUNCH_UNCERTAIN', 'T3 accepted the command but its thread is not yet visible.', {
            threadId: command.threadId, commandId: command.commandId,
          });
        }
        let linkError = null;
        if (!hasPrLink(thread, selected.number)) {
          try {
            await api('POST', '/api/orchestration/dispatch', linkCommand(command.threadId, selected));
          } catch (error) {
            linkError = error.code ?? 'T3_LINK_FAILED';
          }
        }
        const linkedThread = await waitForThread(api, command.threadId);
        const prLinked = linkedThread ? hasPrLink(linkedThread, selected.number) : false;
        const worktreeVerified = await deps.verifyWorktree(projectCwd, thread.worktreePath);
        const promptVerified = hasTargetMarker(thread, reviewTarget(selected.number, selected.headSha));
        if (!worktreeVerified || !promptVerified) {
          result = { ok: false, status: 'launch_incomplete', ...summary,
            worktreePath: thread.worktreePath, worktreeVerified, promptVerified, prLinked };
        } else {
          result = { ok: prLinked, status: prLinked ? 'launched' : 'thread_created_link_failed',
            ...summary, worktreePath: thread.worktreePath, worktreeVerified, promptVerified, prLinked,
            ...(prLinked || !linkError ? {} : { linkError }),
          };
        }
      }
    }
  } finally {
    try {
      await deps.revokeSession(runtime, session);
    } catch {
      revokeFailure = true;
    }
  }
  if (revokeFailure) result.authSessionRevokeFailed = true;
  return result;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const result = await launchReview(parseArguments(process.argv.slice(2)));
    process.stdout.write(`${JSON.stringify(result)}\n`);
    if (!result.ok) process.exitCode = 2;
  } catch (error) {
    const safeError = error instanceof LaunchError ? error : new LaunchError('LAUNCH_FAILED', 'Review launch failed.');
    process.stdout.write(`${JSON.stringify({ ok: false, status: 'error', code: safeError.code,
      message: safeError.message, details: safeError.details })}\n`);
    process.exitCode = 1;
  }
}
