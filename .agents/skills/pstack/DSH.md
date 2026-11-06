# pstack on DeepSeek Harness

Source: `cursor/plugins` at `101b274`, path `harness/pstack/claude-code/skills`. Installed 2026-10-17.

## Layout

- Visible entry: `.agents/skills/poteto-mode/SKILL.md`.
- Upstream bundle: `.agents/skills/pstack/<skill>/SKILL.md`.
- Upstream skills keep `disable-model-invocation: true`, so DeepSeek Harness does not list them. Read them by path.
- Treat the bundle as read-only. Put local changes in this file or in a separate skill.

## Tool map

| pstack (Cursor) | DeepSeek Harness |
|---|---|
| `Task` with `subagent_type` | `subagent` or `subagent_fork`. No type, no model override. |
| `AskQuestion` | In a Bot session, `ask_user`, with `options` for a tap-to-answer card; `show_view` for richer cards. Both names exist in `resources/app.asar`, `ask_user` from the bot layer and `ask_user_question` from the `dsh-tool-ask-user` plugin, but only `ask_user` is in a Bot session's tool list, so `ask_user_question` is not callable there. Verified on this machine 2026-10-21. |
| `TodoWrite` / todolist | `todo_write` |
| `Read` / `Edit` / `Write` / `Bash` | `read` / `edit` / `write` / `pwsh` |
| `WebSearch` / `WebFetch` | `web_search` / `web_fetch` |
| Slash command such as `/architect` | Read `.agents/skills/pstack/architect/SKILL.md` |
| `run_in_background: true` | `pwsh` with `run_in_background: true` returns a job id; collect it with `job_output`, stop it with `job_kill`. Was written "Not available" here until 2026-10-21, which conflated Cursor's `Task(run_in_background)` with the shell's own background mode. Verified on this machine. `subagent` runs asynchronously on its own. |
| `poteto-agent` subagent type | Not available. Write a self-contained `subagent` prompt. |
| Graphite, Bugbot, `gh` stack commands | None are needed. `gt` is absent and six playbooks already forbid requiring it, in three wordings; Bugbot and `gh stack` are absent and no playbook invokes either. Do not skip a step for any of them, see Deltas below. |

## Environment

- Windows, `pwsh`, workspace `D:\project\neurosama`.
- Node 22.23.1, Python in `.venv`.
- No per-subagent model selection. Do not name the grok or claude models in pstack.
- `.agents/` and `tmp/` are gitignored. Local decision trails go under `tmp/`.
- Read pstack by absolute path, `D:\project\neurosama\.agents\skills\pstack\...`. `.agents/` is not in a worktree, so a relative path from `tmp/wt-*` resolves to nothing.

## Deltas from upstream

- `docs/harness/claude-code.md` exists at `.agents/skills/pstack/docs/harness/claude-code.md`, a short pointer back to this file. It carried the same stale tool rows until 2026-10-21, now corrected. Read it as the short version, this file as the authority. Its own relative link is `../../DSH.md`, which from `pstack/docs/harness/` resolves to `pstack/DSH.md` and is live. No byte count is written here on purpose, since editing the adapter would make it stale; run `Get-Item` on the path when the size matters.
- All 21 leaf `SKILL.md` files that say "read the adapter first" reach the adapter with two `../` hops. From `pstack/<leaf>/` that lands in `.agents/skills/docs/harness/`, one level too high, so all 21 are dead; one hop would be correct. Left unfixed because every fix touches the bundle layout, which the Layout section above keeps read-only. This file replaces the adapter, so the dead links block nothing. Grep `claude-code.md` to find them; this entry deliberately spells no path, so the count stays 21 leaves plus the one live self-link inside the adapter.
- Graphite is **not** a blocker, checked 2026-10-21. Six playbooks already forbid requiring it, in three wordings. Never require Graphite (`gt`) at `shipping.md:5`, `babysit.md:7`, `autopilot-full.md:8`. Do not require Graphite (`gt`) at `opening-a-pr.md:24`. Never require `gt` at `autopilot-stack.md:6` and `multi-phase-plan.md:56`. `gt` is absent from this machine and no playbook runs a `gt` command. The only `gt` tokens in the bundle are these six prose mentions, three shell `-gt` comparisons in `worktree-audit.sh`, and one negative test in `orch.test.ts:680` that asserts `--forge gt` is rejected. `gh stack` is an uninstalled official `gh` extension, and no playbook invokes it either. So a step is never skipped for Graphite, and the old `skip: no Graphite in DSH` reason in this file was wrong. The real blockers are the ones under Scripts and Skills above.
- `setup-pstack` configures per-role Cursor models. It has no effect here. Ignore it.

## Skills the mode names that are not installed

Checked on this machine 2026-10-21. Read the name, mark the step `skip: <reason>`, do not install.

| Named in | Missing | Why | Instead |
|---|---|---|---|
| mode triggers | `deslop` | Ships in `cursor-team-kit`, not installed | No substitute. Mark the step `skip: skill not installed in DSH` and apply the `unslop` writing rules to the diff by hand |
| mode triggers | `control-ui`, `control-cli` | Same plugin, not installed | Use the `ego-browser` skill for browser surfaces |
| mode triggers | `create-skill` | Cursor built-in | Follow the shape of `pstack/poteto-mode/SKILL.md` |
| mode triggers | `orchestrate` skill | Not in the bundle; the playbook `playbooks/orchestrate.md` exists but needs `bun` | See Scripts below |

Every other leaf the mode names (`how`, `architect`, `swarm`, `arena`, `interrogate`, `unslop`, `technical-writing`, `no-comments`, `figure-it-out`, `show-me-your-work`, `reflect`, `why`, `benchmark-checklist`) is present under `.agents/skills/pstack/<name>/SKILL.md`.

## Scripts

`bun` is not installed here, checked 2026-10-21. `poteto-mode/scripts/` is a bun project (`package.json` test script is `bun test orch watch-pr check-plan.test.ts`; `orch/orch.ts` and `watch-pr/watch-pr` are `#!/usr/bin/env bun`). `orchestrate` drives everything through `orch init`, `orch unit`, `orch inbox`, `orch ledger`, so it cannot run: `skip: no bun in DSH`. `check-plan.mjs` runs under node, verified by its usage output, so `multi-phase-plan` step 6 works. The rest of that playbook still assumes Cursor: step 3 wants `poteto-agent` subagents with per-model choice, the live lanes in its skeleton want cloud VMs and `control-ui`, and step 2 wants `/loop 1h`, which is `schedule_create` here. Write the plan and check it; mark the lane steps `skip: no cloud agents in DSH`.

`autopilot-full` and `autopilot-stack` name no `bun` at all. Their blocker is different: `autopilot-full.md:8` says "Each owner is a Cursor cloud agent", and this harness has local subagents only, with no cloud environment. `autopilot-stack.md:3` inherits it ("**Autopilot-full** holds except for how verified work leaves") and never mentions cloud agents itself, so quote the blocker from `autopilot-full`. `skip: no cloud agents in DSH`. Both still use `gh`, which is installed at `C:\Program Files\GitHub CLI\gh.exe`; `origin` (the alternative forge CLI) is not.

`/loop` is a Cursor built-in with no skill on disk, but `schedule_create` with `every_seconds` covers it, verified by creating and deleting a schedule. `babysit` still cannot run its GitHub path, because that path calls `scripts/watch-pr/watch-pr`, which starts with `#!/usr/bin/env bun` and dies under node on `import.meta.dir` in `bootstrap.ts`.

Counts on this machine, from `Get-ChildItem`, 2026-10-21. Both totals are right; they answer different questions, so state the scope with the number.

| Scope | `SKILL.md` | disabled | enabled |
|---|---|---|---|
| `.agents/skills/pstack/` | 54 | 53 | `setup-pstack` |
| `.agents/skills/` (adds the two top-level entries) | 56 | 53 | `oil-ui`, `poteto-mode`, `pstack/setup-pstack` |

`pstack/` also holds 23 playbooks and 24 principle leaves. A count quoted without its scope reads as a contradiction, which is how 54 and 56 were both reported as the number.
