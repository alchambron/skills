---
name: joulemv-pr-sensitivity
description: Map every changed file in a JouleMV pull request to a sensitivity level and identify the correctness, performance, and scalability hotspots. Use when asked for PR change sensitivity, risk hotspots, or review priorities across a large diff.
---

# JouleMV PR sensitivity

Produce a read-only review map of **every changed file**. Sensitivity means the consequence and reach of an error in the changed behavior, and therefore the attention it deserves during review. It is not a claim that the code is defective, a probability of failure, or a substitute for a full code review.

## Establish the exact change

1. Read the repository `AGENT.md`. If the PR changes `frontend/`, also read `frontend/AGENT.md` and `frontend/.claude/skills/INDEX.md`; load only conventions relevant to the changed code.
2. Use the PR the user names. Otherwise resolve the PR for the current branch with `gh pr view`. If neither gives a unique target, ask for the PR or fixed point. Record PR number/URL, base, head SHA, and the time inspected.
3. Fetch the PR refs and compare the base/head at their merge base. Get the **complete** changed-file inventory, including additions, deletions, renames, binary files, and generated files. Cross-check the inventory against GitHub's changed-file count; use pagination when necessary. Record the total before analyzing files. Do not silently analyze only the first page or a truncated diff.
4. Read each changed hunk. For behavior-bearing files, trace the nearest callers, data flow, and affected tests/configuration far enough to identify who or what depends on the change. Read the PR description and linked issue when they explain intended behavior. Recheck the PR head before reporting; refresh changed analysis if it moved.

## Rate the change

Give each file a rating on **correctness (C)**, **performance (P)**, and **scalability (S)**. Use `—` when an axis has no plausible impact from the changed hunk. Consider security, privacy, tenant isolation, data integrity, availability, and deployment/rollback in the **Other** column when applicable. Base ratings on changed behavior and its reachable effects, not file extension, diff size, or the presence of tests alone.

| Level | Meaning |
| --- | --- |
| Critical | A plausible mistake could cause broad or irreversible data loss/corruption, cross-tenant exposure, a security breach, a production-wide outage, or unbounded load on a central path. |
| High | A plausible mistake could break an important workflow for many users/tenants, materially change business results, or cause severe latency/resource growth at normal or expected scale. |
| Moderate | A plausible mistake affects a bounded feature, dataset, or workload and is recoverable without broad impact. |
| Low | The changed behavior has a narrow, readily reversible effect with little impact on results or load. |

Use the highest **supported** axis or Other concern as the file's overall level. If no axis applies, use Low overall and explain the file's limited effect. State the concrete failure mode that makes a file High or Critical; do not elevate it merely because it lives in a controller, migration, shared component, or dependency file. Mark an uncertain rating `provisional` and say what evidence is missing. A proven defect belongs in a separate findings section with its own severity; sensitivity can be high even when the implementation is correct.

Assess the axes against the actual execution path:

- **C:** calculations, units, rounding, null/empty semantics, authorization, tenant boundaries, API contracts, state transitions, schema/data migration, and compatibility with existing persisted data.
- **P:** query plans and indexes, N+1 calls, repeated rendering or recomputation, network payloads, blocking work, allocation, and time or memory on normal requests.
- **S:** how work, storage, or fan-out grows with rows, tenants, users, requests, history, or concurrency; pagination, batching, limits, locking, and queue/backpressure behavior.

Distinguish an observed issue from a plausible failure condition. For performance or scalability, give the expected growth mechanism or workload assumption; do not invent timings or production impact. Follow a cross-file behavior to its entry point, but list each changed file once and explain its own role. Mark tests, translations, docs, lockfiles, and generated files individually; use their actual effect on behavior or release risk rather than automatically calling them Low. If content cannot be inspected (for example a binary), mark the evidence gap and rate provisionally.

## Report in the repository's review style

Lead with verified, actionable defects if any were established, citing changed lines and separating static reasoning from local tests, browser/staging checks, and production evidence. Do not manufacture defect findings merely to populate that section. Then provide:

1. **Scope:** PR link, base and head, number of changed files, and whether the inventory is complete.
2. **Hotspots:** the highest-sensitivity paths first, with changed-line links, the affected behavior, failure condition, relevant axis/Other concern, and the focused review or validation question. Group files only when they form one behavior; keep their individual ratings visible below.
3. **Full file map:** one row for **each** changed file, sorted Critical → High → Moderate → Low, with columns `File`, `Overall`, `C`, `P`, `S`, `Other`, and `Reason / review focus`. Use the level names above (or `—`) in the axis columns; write the Other concern and its level when one applies. Link to the changed file or line; identify old/new paths for renames and old-side lines for deletions. The row count must equal the complete changed-file count.
4. **Coverage and limits:** identify uninspected content, unavailable runtime evidence, and assumptions that could change ratings. Suggest a small set of targeted checks for the highest-risk behavior; label them as proposed unless actually run.

Keep the full map scannable even for 45 or more files: one concise reason per row, with deeper explanation in Hotspots. This is an assessment, so do not edit code, publish a GitHub review, or post comments unless separately requested.
