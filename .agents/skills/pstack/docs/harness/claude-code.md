# Claude Code adapter (pointer)

This bundle is installed for **DeepSeek Harness (DSH)**, not Claude Code. The
upstream file this path used to hold is not part of the installed bundle; the
authoritative mapping lives in [`../../DSH.md`](../../DSH.md).

Read it once per session before following any skill. The short version:

| pstack (Cursor / Claude Code) | this harness |
|---|---|
| `Task` with `subagent_type` | `subagent` / `subagent_fork`, no type or model override |
| `AskQuestion` | `ask_user` (with `options` for a tap-to-answer card). Was `ask_user_question` here until 2026-10-21, the name of the unattached `dsh-tool-ask-user` plugin; only `ask_user` is in a Bot session's tool list. Verified on this machine. |
| `TodoWrite` | `todo_write` |
| `Read` / `Edit` / `Write` / `Bash` | `read` / `edit` / `write` / `pwsh` |
| `WebSearch` / `WebFetch` | `web_search` / `web_fetch` |
| `/architect` and other slash commands | read `.agents/skills/pstack/<name>/SKILL.md` |
| `run_in_background: true` | `pwsh` with `run_in_background: true` returns a job id; `job_output` reads it, `job_kill` stops it. Was "not available" here until 2026-10-21, which conflated Cursor's `Task(run_in_background)` with the shell. `subagent` is async on its own. |
| Graphite / Bugbot / `gh` stack | none needed. Six playbooks forbid requiring `gt`, in three wordings: "Never require Graphite (`gt`)" at `shipping:5`, `babysit:7`, `autopilot-full:8`; "Do not require Graphite (`gt`)" at `opening-a-pr:24`; "Never require `gt`" at `autopilot-stack:6` and `multi-phase-plan:56`. See `../../DSH.md`. `gt`, Bugbot, and `gh stack` are absent and no playbook invokes them. Do not skip a step for any of them. |

The four skills the mode names but this bundle lacks are `deslop`, `create-skill`,
`control-cli`, and `control-ui`. Those steps get `skip: skill not installed in DSH`.

Everything else (playbooks, principles, verification skills) is read from the
bundle unchanged. When a skill mentions a Cursor-only tool, map it here instead
of skipping the step.
