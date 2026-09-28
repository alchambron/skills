import assert from 'node:assert/strict';
import { test } from 'node:test';

import { launchReview } from './launch_review.mjs';

const sha = 'a'.repeat(40);
const baseSha = 'b'.repeat(40);
const selected = {
  number: 1586,
  title: 'Example',
  url: 'https://github.com/EnerZam/JouleMV/pull/1586',
  headSha: sha,
  kind: 'Review',
};
const projectCwd = '/test/JouleMV';
const project = {
  id: 'project-1', workspaceRoot: projectCwd,
  defaultModelSelection: { instanceId: 'codex', model: 'gpt-6-astra' },
};

function dependencies(overrides = {}) {
  const calls = { posts: [], revoked: 0 };
  let thread = null;
  const deps = {
    readPending: async () => ({ reviews: [selected] }),
    freshPullRequest: async () => ({
      number: selected.number, url: selected.url, headRefOid: sha,
      headRefName: 'JMV-B-199', baseRefName: 'work', state: 'OPEN', isDraft: false,
    }),
    originWorkCommit: async () => ({ branch: 'work', sha: baseSha }),
    ensurePrCommit: async () => ({ local: true, fetched: false }),
    discoverRuntime: async () => ({ origin: 'http://127.0.0.1:3773', environmentId: 'env-1' }),
    issueSession: async () => ({ sessionId: 'session-1', token: 'SECRET_TOKEN' }),
    revokeSession: async () => { calls.revoked += 1; },
    verifyWorktree: async (_projectCwd, _path, expectedSha, expectedBranch) =>
      expectedSha === baseSha && expectedBranch?.startsWith('t3-review-pr-1586-'),
    dispatchBootstrap: async (runtime, token, command) => {
      return deps.apiRequest(runtime, token, 'POST', '/ws', command);
    },
    apiRequest: async (_runtime, _token, method, requestPath, payload) => {
      if (method === 'GET' && requestPath === '/api/orchestration/shell') {
        return { projects: [project], threads: [] };
      }
      if (method === 'POST') {
        calls.posts.push(payload);
        if (payload.type === 'thread.turn.start') {
          thread = {
            id: payload.threadId, projectId: project.id,
            branch: payload.bootstrap.prepareWorktree.branch,
            worktreePath: '/test/worktree', latestTurn: { state: 'running' },
            messages: [{ role: 'user', text: payload.message.text }],
            activities: [{ kind: 'worktree-setup', payload: {
              phase: 'done', baseRef: payload.bootstrap.prepareWorktree.baseBranch,
              worktreePath: '/test/worktree',
            } }],
            pullRequests: [],
          };
        }
        if (payload.type === 'thread.pull-request.link') {
          thread.pullRequests.push(payload);
        }
        return { sequence: calls.posts.length };
      }
      if (method === 'GET' && requestPath.startsWith('/api/orchestration/threads/')) {
        return { thread };
      }
      throw new Error(`Unexpected request ${method} ${requestPath}`);
    },
    ...overrides,
  };
  return { deps, calls };
}

test('dry run pins the fetched origin/work snapshot without dispatching a thread', async () => {
  const { deps, calls } = dependencies();
  const result = await launchReview({ pendingJson: 'unused', prNumber: 1586, dryRun: true, projectCwd }, deps);
  assert.equal(result.status, 'dry_run');
  assert.equal(result.baseRef, baseSha);
  assert.equal(result.bootstrap.baseBranch, baseSha);
  assert.equal(result.bootstrap.requireWorktree, true);
  assert.equal(result.bootstrap.startFromOrigin, false);
  assert.match(result.bootstrap.branch, /^t3-review-pr-1586-/);
  assert.deepEqual(result.modelSelection, { instanceId: 'codex', model: 'gpt-6-sol' });
  assert.match(result.prompt, /\$joulemv-code-review/);
  assert.match(result.prompt, /If it reports any verified actionable defect, invoke \$github-inline-review with no arguments/);
  assert.match(result.prompt, /publish only REQUEST_CHANGES, verify its review and inline comments, then STOP this thread/);
  assert.match(result.prompt, /In this finding branch, do not invoke \$manual-test-guide, start the app, or open a browser/);
  assert.match(result.prompt, /Only when there are no verified actionable findings, invoke \$manual-test-guide/);
  assert.match(result.prompt, /agent_run\.py doctor, then python3 \/Users\/alchambron\/\.codex\/skills\/joulemv-review-launcher\/scripts\/agent_run\.py start/);
  assert.match(result.prompt, /This worktree must start at origin\/work commit b{40}/);
  assert.match(result.prompt, /Merge the pinned PR head a{40}/);
  assert.match(result.prompt, /If it conflicts, run git merge --abort, mark live scenarios Blocked/);
  assert.match(result.prompt, /returned commit equals the verified integration commit/);
  assert.match(result.prompt, /do not open the browser or capture a blank frame/);
  assert.match(result.prompt, /use preview_status first, then preview_open with the returned frontendUrl/);
  assert.match(result.prompt, /After any start attempt, including failure or early exit, run/);
  assert.match(result.prompt, /agent_run\.py stop before your final reply/);
  assert.match(result.prompt, /Verify databaseDropped true or already_stopped/);
  assert.match(result.prompt, /save preview_snapshot\(\{save:true\}\) at its observed outcome/);
  assert.match(result.prompt, /inspect the saved image/);
  assert.match(result.prompt, /never embed an empty frame/);
  assert.match(result.prompt, /Never submit APPROVE or mark the PR approved/);
  assert.deepEqual(calls.posts, []);
  assert.equal(calls.revoked, 1);
  assert.doesNotMatch(JSON.stringify(result), /SECRET_TOKEN/);
});

test('stale selection stops before T3 authentication', async () => {
  const { deps, calls } = dependencies({
    freshPullRequest: async () => ({ headRefOid: 'b'.repeat(40), state: 'OPEN', isDraft: false }),
    issueSession: async () => { throw new Error('must not issue session'); },
  });
  await assert.rejects(
    launchReview({ pendingJson: 'unused', prNumber: 1586, projectCwd }, deps),
    { code: 'STALE_SELECTION' },
  );
  assert.equal(calls.revoked, 0);
});

test('PRs targeting another base stop before T3 authentication', async () => {
  const { deps, calls } = dependencies({
    freshPullRequest: async () => ({ headRefOid: sha, state: 'OPEN', isDraft: false, baseRefName: 'main' }),
    issueSession: async () => { throw new Error('must not issue session'); },
  });
  await assert.rejects(
    launchReview({ pendingJson: 'unused', prNumber: 1586, projectCwd }, deps),
    { code: 'WRONG_PR_BASE' },
  );
  assert.equal(calls.revoked, 0);
});

test('live launch branches from fetched origin/work SHA, links PR, and verifies readback', async () => {
  const { deps, calls } = dependencies();
  const result = await launchReview({ pendingJson: 'unused', prNumber: 1586, projectCwd }, deps);
  assert.equal(result.status, 'launched');
  assert.equal(result.prLinked, true);
  assert.equal(result.worktreeVerified, true);
  assert.equal(result.setupVerified, true);
  assert.deepEqual(calls.posts.map((command) => command.type), [
    'thread.turn.start', 'thread.pull-request.link',
  ]);
  assert.equal(calls.posts[0].bootstrap.prepareWorktree.baseBranch, baseSha);
  assert.equal(calls.posts[0].bootstrap.prepareWorktree.startFromOrigin, false);
  assert.equal(calls.posts[0].bootstrap.prepareWorktree.branch,
    calls.posts[0].bootstrap.createThread.branch);
  assert.deepEqual(calls.posts[0].modelSelection, { instanceId: 'codex', model: 'gpt-6-sol' });
  assert.deepEqual(calls.posts[0].bootstrap.createThread.modelSelection, { instanceId: 'codex', model: 'gpt-6-sol' });
  assert.equal(calls.posts[0].bootstrap.runSetupScript, false);
  assert.equal(calls.posts[1].repository, 'enerzam/joulemv');
  assert.equal(calls.revoked, 1);
});

test('a worktree at the wrong revision fails launch verification', async () => {
  const { deps } = dependencies({ verifyWorktree: async () => false });
  const result = await launchReview({ pendingJson: 'unused', prNumber: 1586, projectCwd }, deps);
  assert.equal(result.status, 'launch_incomplete');
  assert.equal(result.ok, false);
  assert.equal(result.worktreeVerified, false);
});

test('T3 setup base mismatch fails launch verification', async () => {
  const { deps } = dependencies();
  const originalApi = deps.apiRequest;
  deps.apiRequest = async (...args) => {
    const response = await originalApi(...args);
    if (args[2] === 'GET' && args[3].startsWith('/api/orchestration/threads/')) {
      response.thread.activities[0].payload.baseRef = sha;
    }
    return response;
  };
  const result = await launchReview({ pendingJson: 'unused', prNumber: 1586, projectCwd }, deps);
  assert.equal(result.status, 'launch_incomplete');
  assert.equal(result.setupVerified, false);
});

test('lost bootstrap response reuses the created thread without a second bootstrap', async () => {
  const { deps, calls } = dependencies();
  const originalDispatch = deps.dispatchBootstrap;
  deps.dispatchBootstrap = async (...args) => {
    await originalDispatch(...args);
    throw new Error('response lost');
  };
  const result = await launchReview({ pendingJson: 'unused', prNumber: 1586, projectCwd }, deps);
  assert.equal(result.status, 'launched');
  assert.equal(calls.posts.filter((command) => command.type === 'thread.turn.start').length, 1);
});

test('a failed PR link reports the created thread and failed link', async () => {
  const { deps, calls } = dependencies();
  const originalApi = deps.apiRequest;
  deps.apiRequest = async (...args) => {
    if (args[2] === 'POST' && args[4]?.type === 'thread.pull-request.link') {
      throw new Error('link failed');
    }
    return originalApi(...args);
  };
  const result = await launchReview({ pendingJson: 'unused', prNumber: 1586, projectCwd }, deps);
  assert.equal(result.status, 'thread_created_link_failed');
  assert.equal(result.ok, false);
  assert.equal(result.prLinked, false);
  assert.equal(calls.posts.filter((command) => command.type === 'thread.turn.start').length, 1);
});
