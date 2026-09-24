---
name: delegated-implementation
description: Implement a substantial code change with separable tasks or components. Delegate bounded work to subagents, coordinate ownership and integration, and verify the result. Skip small or tightly coupled edits.
---

# Delegated implementation

Use this workflow when the requested implementation has bounded tasks that benefit from separate context or concurrent work. Honor the user's scope and any project requirement for an approved plan before implementation.

1. Identify task dependencies, shared interfaces, and file ownership. If the work is tightly coupled, keep implementation in the main agent or sequence bounded tasks. For concurrent edits, assign disjoint files or isolated worktrees and name who integrates shared files.
2. Give each implementer a concrete brief: intended behavior, relevant context and project instructions, owned files or worktree, acceptance criteria, and the report expected back. Keep the main agent responsible for interface decisions and integration.
3. Review each result against the request and neighboring code. Resolve overlap and concerns, integrate the changes, and run checks that address the actual risks. Use an independent reviewer when the change's complexity or risk warrants one; avoid duplicate review loops.

Subagents report changed files, checks run, and unresolved concerns. The main agent completes the task only after the integrated change is verified and any limitations are stated. Delegation does not authorize commits, pushes, deployments, publication, or other external changes beyond the user's request.
