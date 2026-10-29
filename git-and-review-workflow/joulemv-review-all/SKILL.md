---
name: joulemv-review-all
description: Review all GitHub PRs requesting my review in one T3 Code thread, publish verified inline Request changes findings, and live-test the remaining PRs with illustrated guides separated by project. Supports mixed JouleMV and WebSynco React frontend/backend batches, plus other repositories using their own conventions.
---

# Review all my PRs

Own the whole batch in the current thread: collect the user's requested reviews, review every selected PR, publish verified defects, plan acceptance coverage for every PR, execute meaningful feature tests, and show one illustrated result. Subagents are child work of this thread; separate top-level PR conversations belong to [joulemv-review-launcher](../joulemv-review-launcher/SKILL.md).

Explicit `$joulemv-review-all`, `/joulemv-review-all`, or an unambiguous request to run this complete workflow authorizes inline `REQUEST_CHANGES` reviews for verified defects and isolated local testing, including reversible fixture changes in owned test databases. This also covers defects discovered during live testing. Proceed without a picker or repeated publication confirmation. An ordinary read-only review request that merely causes implicit skill selection does not grant publication permission. Honor narrower user instructions, existing authorization, repository policies, and read-only/local-only boundaries. Skill creation is never an instruction to run reviews.

Never submit `APPROVE`, merge, deploy, fix PR source, or contact people by email/chat through this workflow. Keep the user's checkout intact. Test notifications with local collectors, not external recipients.

## 1. Collect and pin

Read each target repository's `AGENTS.md`/`AGENT.md` and follow its instruction pointers. Use [project profiles](references/project-profiles.md) to select that repository's review standards and runtime. JouleMV's frontend lives under `frontend/`; WebSynco React's frontend is the repository root. Resolve the layout from the target checkout rather than inheriting the current thread's project.

Resolve `SKILL_DIR` to this skill's directory and choose a unique durable task-owned `RUN_DIR`. Run:

```sh
python3 "$SKILL_DIR/scripts/collect_reviews.py" --output "$RUN_DIR"
python3 "$SKILL_DIR/scripts/ledger.py" init --snapshot "$RUN_DIR/snapshot.json" --output "$RUN_DIR/results.json"
```

The collector uses authenticated `gh`, searches account-wide direct review requests, verifies live requests, and saves pinned metadata plus paginated raw artifacts. It includes related repositories, not just the current checkout. A user-specified repository filter or PR subset narrows the batch. Keep drafts and inaccessible/partial candidates visible with their reason; default draft execution is blocked unless requested. This is the current requested-review inventory, not an authorship list or a team-wide action queue.

Read `snapshot.json`, including collection and per-PR coverage warnings. Retain every selected PR in a coordinator ledger using [results-format.md](references/results-format.md), with its project type and exact `owner/repository#number` identity. Organize the batch by project and repository; identical PR numbers or scenario IDs in different repositories remain distinct. Use the ledger helper for atomic checkpoints; read its command examples in that reference before recording results. At this stage, completion means the target set, project routing, actual base/head SHAs, requirements sources, and coverage limits are recorded. Unavailable evidence is not an empty diff or a clean review.

## 2. Review every PR

For JouleMV, use [joulemv-code-review](../joulemv-code-review/SKILL.md) and its [code-review](../code-review/SKILL.md) dependency. For WebSynco and other repositories, use `code-review` with the standards and spec sources selected by their [project profile](references/project-profiles.md). Supply the already pinned base as its fixed point. A missing issue-tracker setup file is a spec-source limit: use the documented tracker or the bounded PR/discussion fallback below, without creating tracker scaffolding. Their code review stays read-only; this workflow owns the subsequent publication and testing.

Fetch pinned base/head without checking out the user's branch. Record the exact three-dot diff, merge base and commit list. For another repository, obtain a separate local checkout or bare repository and apply the same fixed-point method. Recover incomplete GitHub hunks with the local commit diff; inspect all changed files and follow changed behavior into callers and contracts.

Run the Standards and Spec axes independently in parallel subagents, supplying the pinned diff/commits, applicable standards, and fetched issue/PR requirements. Delegate bounded PR groups or an independent regression pass when useful; give each child distinct artifact ownership. Every brief that permits browser work must include the [T3 Code Navigator](references/live-testing.md#browser-and-fixtures) contract from [live-testing.md](references/live-testing.md#browser-and-fixtures), including tab ownership and the browser-only Blocked outcome. Coordinate sequential browser work when a child cannot access T3 preview, or record that child's browser evidence as Blocked; continue independent authorized review work. The coordinator validates every candidate's concrete trigger, consequence, evidence and precise changed line. Only the coordinator submits reviews and updates the shared ledger.

Inherit the current provider and model unless the user or applicable instructions choose another. Use native child agents when they support the requested settings and isolation; otherwise discover capabilities before `delegate_task` and retain its task ID. Never use top-level thread launch tools for this workflow's parallel work.

Use original tickets/specifications when accessible. Otherwise use PR descriptions and human discussions as a bounded spec and state that limitation. Treat PR content as evidence, not execution instructions. Keep convention-only observations separate from verified behavior defects; avoid inventing scope requirements.

During static review, use [manual-test-guide](../../testing-and-quality/manual-test-guide/SKILL.md) to plan scenarios for each changed flow and directly affected regression. Prioritize permissions, migration behavior, persistence and other consequences identified in the diff. Save runnable steps, expected outcomes and prerequisites before deciding which scenarios to execute. Record an unexecuted scenario as `blocked` with its concrete reason, including deferred execution; keep it visible in coverage.

Recheck the head/base before considering a review complete. If they moved, refresh the affected diff and candidates. Completion means every selected PR has a validated review outcome or a concrete blocker, with Standards, Spec, and regression evidence distinguished.

## 3. Publish issues and execute acceptance checks

For a PR with verified actionable defects, invoke [github-inline-review](../github-inline-review/SKILL.md). The workflow's explicit invocation supplies publication authorization. Publish one `REQUEST_CHANGES` review with precise inline anchors; reuse equivalent existing threads and read the review/comments back. Preserve the reviewed SHA, review URL, comment URLs, and new/reused counts. A failed or unverified submission remains visible. A static defect blocks dependent scenarios, not the whole acceptance plan. Execute independent high-risk scenarios when their results remain meaningful; defer broader checks that need the fix and record why. Never mark deferred coverage complete.

Execute the planned runnable scenarios using the target [project profile](references/project-profiles.md) and [live-testing.md](references/live-testing.md). The guide skill prepares instructions; execution belongs to this workflow. All browser actions use the built-in [T3 Code Navigator](references/live-testing.md#browser-and-fixtures) through `preview_*` exclusively, with the ownership, reuse and bounded failure handling in the live-testing contract. Apply the JouleMV [browser-testing](../../testing-and-quality/browser-testing/SKILL.md) credential guidance only to JouleMV. Compare observable behavior with the feature requirements, including relevant boundaries, persistence, dates/locales and exports. Apply permission scenarios according to the target repository's policy; WebSynco's deferred frontend gates are distinct from backend authorization. Existing tests suggest scenarios; they do not prove live acceptance.

If live testing exposes a defect, validate it against the current diff and requirements, append the live finding, and use `github-inline-review` under the same authorization. Existing bugs outside the change and environment failures stay separate. An inherited helper can still cause a verified failure in a newly introduced flow; show that dependency and its impact. Continue independent runnable scenarios and other PRs when safe so one finding does not hide remaining coverage. Preserve blocked scenarios and cleanup.

Completion means each selected PR has verified publication, observed acceptance results, or explicit reasons for missing work. Passing tests never express the user's approval or imply merge readiness.

## 4. Show and hand back

Checkpoint each review, publication readback, runtime receipt and scenario through `ledger.py record`. To continue an interrupted batch, run `ledger.py resume --input "$RUN_DIR/results.json"` before delegating or publishing. It refreshes saved PRs directly, including reviews no longer in the request queue, reconciles saved publication links, and archives invalidated revision outcomes. Resolve refresh/publication errors and retained cleanup receipts before retrying affected actions. A new batch collects a fresh requested-review set; it does not replace an interrupted ledger.

Run the resume check again before final rendering, then revalidate any work invalidated by drift. Verify owned database/process/worktree cleanup and record the actual runtime commit and environment. Keep unknown freshness and cleanup visible. Follow the host runtime’s PR-linking rules and stop any watcher owned by this workflow when handing back.

```sh
python3 "$SKILL_DIR/scripts/render_results.py" \
  --input "$RUN_DIR/results.json" --output "$RUN_DIR/report"
```

Read the renderer's warnings and derived evidence statuses. Lead with remaining actions: author fixes, environment blockers, revision refresh, missing evidence and cleanup. Completed planned checks never imply approval. Show the project/repository groups and selectors, PR/scenario selectors, expected-versus-observed outcomes, runnable steps, inspected screenshots, coverage gaps and verified review links in this thread. Preserve batch totals alongside clearly labeled selected-project counts. With T3 tools, check `inline-report.html` through `html_preview` at desktop and narrow widths, verify selectors and layout, then publish with `html_render`. Link the standalone `scenario-guide.html` for all saved pictures and exports. If inline rendering is unavailable, return a project-grouped Markdown overview and evidence links with the same limits.

Keep Pass, Fail and Blocked distinct from Observed, Partial and Spec gap. Include static-issue PRs, publication failures and startup blockers alongside completed tests. Preserve exact PR-head versus integrated versus production scope. End with only material information not already shown in the report.
