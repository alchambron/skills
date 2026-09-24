---
name: parallel-investigation
description: Investigate a substantial bug or open question with independent hypotheses, sources, or code paths. Delegate read-only work in parallel and synthesize evidence. Skip short or sequential investigations and PR reviews.
---

# Parallel investigation

Use subagents when at least two lines of inquiry can produce useful evidence without waiting on each other. If the work is dependent or small, continue in the main agent.

1. Define each question and the evidence that would answer it. Give each subagent only the relevant context, scope, and a concise report contract: findings, source locations, uncertainty, and unresolved questions.
2. Dispatch the independent inquiries concurrently. Keep the main agent on shared context, integration points, or another useful line of inquiry while they run.
3. Compare the reports, resolve contradictions against primary evidence, and verify decisive claims before acting or answering. Distinguish what was reproduced from what was inferred.

Keep investigation agents read-only. Apply any more specific project or review workflow when it governs the task. The investigation is complete when the main agent can explain the supported conclusion and its remaining uncertainty.
