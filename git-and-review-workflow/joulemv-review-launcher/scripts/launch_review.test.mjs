import assert from 'node:assert/strict';
import { test } from 'node:test';

import { launchReview } from './launch_review.mjs';

const sha = 'a'.repeat(40);
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
      headRefName: 'JMV-B-199', state: 'OPEN', isDraft: false,
    }),
    baseBranch: async () => 'work',
    ensurePrCommit: async () => ({ local: true, fetched: false }),
    discoverRuntime: async () => ({ origin: 'http://127.0.0.1:3773', environmentId: 'env-1' }),
    issueSession: async () => ({ sessionId: 'session-1', token: 'SECRET_TOKEN' }),
    revokeSession: async () => { calls.revoked += 1; },
    verifyWorktree: async () => true,
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
            worktreePath: '/test/worktree', latestTurn: { state: 'running' },
            messages: [{ role: 'user', text: payload.message.text }],
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

test('dry run pins PR head without dispatching a thread', async () => {
  const { deps, calls } = dependencies();
  const result = await launchReview({ pendingJson: 'unused', prNumber: 1586, dryRun: true, projectCwd }, deps);
  assert.equal(result.status, 'dry_run');
  assert.equal(result.baseRef, sha);
  assert.equal(result.bootstrap.requireWorktree, true);
  assert.equal(result.bootstrap.startFromOrigin, false);
  assert.match(result.prompt, /\$joulemv-code-review/);
  assert.match(result.prompt, /If and only if that completed review has at least one verified actionable finding that warrants Request changes/);
  assert.match(result.prompt, /invoke \$github-inline-review with no arguments in this thread to publish those findings as REQUEST_CHANGES/);
  assert.match(result.prompt, /If no finding survives validation, or the remaining observations are uncertain, publish no GitHub review or comment/);
  assert.match(result.prompt, /Never submit APPROVE or mark the PR approved/);
  assert.match(result.prompt, /A clean result is "no actionable findings"/);
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

test('live launch bootstraps at PR SHA, links PR, and verifies readback', async () => {
  const { deps, calls } = dependencies();
  const result = await launchReview({ pendingJson: 'unused', prNumber: 1586, projectCwd }, deps);
  assert.equal(result.status, 'launched');
  assert.equal(result.prLinked, true);
  assert.equal(result.worktreeVerified, true);
  assert.deepEqual(calls.posts.map((command) => command.type), [
    'thread.turn.start', 'thread.pull-request.link',
  ]);
  assert.equal(calls.posts[0].bootstrap.prepareWorktree.baseBranch, sha);
  assert.equal(calls.posts[0].bootstrap.runSetupScript, false);
  assert.equal(calls.posts[1].repository, 'enerzam/joulemv');
  assert.equal(calls.revoked, 1);
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
