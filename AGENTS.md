# Agent Memory

Use `/home/yung/Obsidian/AI-Memory-Storage` as the memory vault.

Copy this file into a project repo as `AGENTS.md` for Codex or `CLAUDE.md` for Claude.

## Rules

- Read `/home/yung/Obsidian/AI-Memory-Storage/00-Index.md` and `/home/yung/Obsidian/AI-Memory-Storage/AGENTS.md` before using the vault.
- Use Obsidian MCP as the primary path for vault search/read/write.
- Fall back to filesystem access only if MCP is unavailable, and state that fallback was used.
- Do not write noisy memory directly into the vault.
- Do not include secrets, tokens, private keys, recovery phrases, or raw environment values.
- Prefer durable facts over transcripts.
- Project memory belongs in the matching project capsule under the vault.

## Definition of Done

Memory update is part of Definition of Done after meaningful implementation:

- update `state.md` with current truth;
- append one dated entry to `lifecycle.md`;
- update `runbook.md` only for commands verified in this session;
- update `decisions.md` only for future-impacting choices;
- update `lessons.md` only for reusable learning;
- do not include secrets, raw environment values, or raw transcript.

If vault write is unavailable, output a concise packet for `_Inbox/YYYY-MM-DD-HHMM-project-context.md`.

## Promotion Rules

- Keep project-specific facts in the project capsule.
- Promote reusable technical patterns to `03-Knowledge`.
- Promote personal learning, reflection, and agent workflow lessons to `04-Self-Explore`.
- If unsure, keep the note in project `lessons.md` first.

## First-Time Integration

Inspect the project non-destructively and generate a Markdown context packet.

Include:

- project purpose;
- repo path;
- tech stack;
- architecture overview;
- key files and directories;
- setup, build, test, run, package, and release commands;
- current branch and git status;
- known issues, blockers, and next actions;
- testing approach and latest test result;
- release/package notes;
- important decisions visible from code or docs.

Save or provide the packet for import into:

`_Inbox/YYYY-MM-DD-project-name-context.md`

After import, promote stable facts into the project capsule:

- `README.md` - purpose, repo path, tech stack, links;
- `state.md` - current truth, blockers, next actions;
- `runbook.md` - verified commands only;
- `decisions.md` - future-impacting decisions;
- `lifecycle.md` - dated development/testing/release summaries;
- `lessons.md` - project-specific lessons.

## Flow Documentation

When documenting an important flow, make it easy to query through Obsidian MCP.
Use addressable headings/subheadings instead of one long bullet list.
For each major flow, prefer subheadings: `Trigger`, `Queues / Events`, `Main Path`, `Database Behavior`, `Statuses`, `Failure Behavior`, `Source Classes`.
If a project has many flows or `flows.md` becomes hard to scan, keep `flows.md` as an index and split details into `flows/<flow-name>.md` files without changing meaning.

## Link Rules

- The project `README.md` must include full-path Obsidian links to its own capsule notes.
- Use full paths for generic note names.
- Keep `_Inbox` packet links only as source links from lifecycle entries.

## Branch Memory

- Use `branches.md` when branch feature differences matter.
- Refresh branch memory from git before relying on it.
- Never assume `main` and feature/hotfix branches contain the same behavior.
- Track only relevant branches and respect human exclusions.
- `state.md` records the current checkout; `branches.md` records branch differences.
- Do not switch, stash, or create branches unless the human explicitly asks.

## Continuous Update

Before work:

- Read `00-Index.md`, `AGENTS.md`, then the matching project capsule.
- Start from `state.md`, `runbook.md`, and `decisions.md`.

After meaningful work:

- Update `state.md` with current truth.
- Append one dated summary to `lifecycle.md`.
- Update `runbook.md` only with commands verified in this session.
- Update `decisions.md` only for choices that affect future work.
- Update `lessons.md` only for reusable learning.
- Keep durable notes short; do not paste raw transcripts.

## Rollover Policy

- Do not split `state.md`; overwrite stale current truth.
- Split only history-like files.
- Keep recent history in `lifecycle.md`.
- Move older lifecycle entries to `lifecycle/YYYY.md` when the file reaches roughly 300-500 lines, 50-100 KB, or becomes hard to scan.
- Use `lifecycle/YYYY-MM.md` only if a yearly file becomes too large.
- Use timestamps only for raw `_Inbox/YYYY-MM-DD-HHMM-project-context.md` packets.

## End-Session Prompt

```text
Update the AI-Memory brain for this project.

Summarize this session into:
- current status
- files/areas changed
- tests/builds run and results
- blockers
- decisions made
- next actions

Update the matching project capsule. Keep state.md short. Append only one dated lifecycle entry. Do not include secrets, raw environment values, or raw transcript.
```
