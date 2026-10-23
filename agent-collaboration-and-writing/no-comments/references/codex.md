# Codex runtime

Repository instructions and the caller's scope take precedence. Audit/proposal requests are read-only: return proposed deletions and refactor targets, and report zero actual deletions. Apply repository approval gates before implementing a proposal.

Use a general Codex subagent that reads `references/comment-sicko.md` in full instead of Cursor's `Task` / `comment-sicko` subagent type. Give it the explicit scope and current read/write boundary. Use the current configured model; scope independent partitions when needed. Review each report and any diff yourself.

The upstream poteto-mode Subagents reference informs delegation only; this install does not activate poteto-mode's autonomy, model defaults, or playbooks.

For `/how` and `/why`, use direct source, dependency documentation, and git-history investigation if those skills are unavailable or require separate explicit invocation. Label this as equivalent investigation, not execution of the missing skill. For `/architect`, produce a design sketch and obtain any repository-required approval before implementation. Report an unresolved dependency when evidence cannot settle a claimed exception.

Use the repository's actual base branch for default diff scope. An explicitly requested whole-project audit overrides the diff default. Preserve file-format directives, shebangs, generated-file metadata, and tool-required pragmas; removing a directive changes behavior. Classify these separately from prose. Keep public API contracts and legal headers under the upstream exceptions.
