# PARKED TASKS — Phase 1 Freeze Inventory

**Generated:** 2026-10-14 (phase-1 freeze)  
**Baseline:** git master, commit `43ca2c6` + working-tree changes (config/avatar.yaml, docs/EDITOR_QUALITY_IMPROVEMENT.md, scripts/eval_editor_quality.py, scripts/test_editor_insight_outputs.py, server/assistant/editor_insights.py).  
**Health:** `python scripts/check_protocol.py` PASS; `python scripts/test_core.py` 38 passed / 0 failed (via `.venv`); local DB snapshots in `data/backups/20261014T075521410236Z/` (6 DBs, integrity ok).

## A. Phase 1 — Declared DONE / 已落地核心模块

### Voice / Full duplex (`CONTINUOUS_VOICE.md`, `FULL_DUPLEX.md`, `VOICE_BASELINE.md`)
- Continuous voice v1: push-to-talk + continuous mode; client-side energy VAD (~250 ms pre-roll, 900 ms silence auto-send); ~29.5 s per- utterance cap; server PCM16 mono 30 s hard cap; invalidated old turns (transcribe/generate/decode/playback can't resurrect); client turn_id protocol (start/text/audio_end/barge_in); ASR runs in own task; permission/network/hidden tab release mic; ScriptProcessor fallback. Tests: module 15/15, server 25/25, browser 13 groups.
- Full-duplex capture: AudioWorklet primary (~20 ms chunks, mic + actual played Gain mixed into mono), ScriptProcessor fallback (1024 samples); EchoGuard (2 kHz downsample, 80 ms window, 0–200 ms delay, correlation + residual, echo frames replaced by equal silence); barge-in policy table (balanced 120 ms @ 22% → cancel after 420 ms; conservative 600 ms; fast 300 ms; hotkey during playback); candidate 180 ms, ≥60% speech, max 2 s; CONFIRMED barge-in sends 260 ms pre-roll + whole candidate; tail protection 260 ms; playback resume retains only undecoded remainder + actual offset, single AudioBuffer, 90 s release, local only; in-memory local telemetry clearable, no sample persistence. Tests: `npm run test:voice` (44 modules + 15 browser groups + worklet synthesis).
- Local voice baseline experiment: explicit start, fixed environment grouping, export metadata only (no audio/transcripts); endpoint/decode/playback/stop timings. `VOICE_BASELINE.md`.

### Speech performance / animation (`SPEECH_PERFORMANCE.md`)
- Same-generation action intent; backend strips internal action tags, validates text anchors, delivers `audio.performance` (schema `{version:1, source, cues:[{intent,gesture,anchor,start_char,end_char,strength}]}`).
- Chain: LLM streamed reply → strip internal tags → validate anchors → audio.performance → PlaybackQueue decode/play → Avatar.speak → PerformanceDirector → VRM arm/wrist.
- Intent map (greeting/farewell→wave; explanation/question/reassurance→palm; disagreement→deny; self_reference→self; neutral→empty plan) with `neutral` kept as llm empty plan not overwritten by keywords; no valid tags → `source:"fallback"` (four keyword actions + 4 s cooldown).
- Strengths 0.15–0.75, max two cues per sentence; 60 code-point lossless slice; cross-slice anchors dropped, llm source retained. Edge TTS WordBoundary → word_timings; StepAudio HTTP TTS → estimated timings. PerformanceDirector alignment provider/estimated; AudioContext clock; pending audio doesn't advance gesture; up to 250 ms prep, fade in/hold/out, no same-arm concurrency; general poses scaled by intensity + reduced-motion; two-step calibration; pose transition layer; gesture paths.
- Tests: `web/avatar/test_performance.js`, `scripts/test_speech_performance.py`, `scripts/test-speech-performance-browser.mjs` (9 groups); tight-format spec `live-format.json` (model emits self_reference on "我觉得", anchors correct).

### Dialogue gaze / eye-head-integration (`DIALOGUE_GAZE_IMPLEMENTATION.md`, `DIALOGUE_GAZE_V3_RESULTS.md`)
- DialogueGazeDirector (`dialogue-gaze.js`) outputs only targets + micro-eye context; IdleDriver keeps existing eye/head motion; index.js sampling decides ownership; calibration snapshot/restore; avatar_lab + real chat entry; app.js via state UI + PlaybackQueue; turn gating for late audio.
- V1 behavior: listening → user focus + low/high-freq micro-drift; thinking/prep → ~0.35 s aversion 0.8 s then return, up to 8 s repeat; text/emotion/done before audio exits old thinking wait; audio start → response on AudioContext clock; between sentences holds attention; done → turn ends only when queue truly idle (pure text no fake speak); interrupt/stop → cancel all registered frames + mouth/gesture cleanup + follow state; reduced volume → yield attention keep frames; manual gaze/eye preview/explicit emotion expression wins (including explicit zero, same-side smile/lower-lid pairs); calibration=1 freezes. Config `gaze.dialogue` with defaults + ranges.
- V2/V3: avatar_lab `model=daily-lids&dialogue=1`; rhythms natural/grave/hesitant/teasing with auto emotion mapping ( grave→grave, teasing→teasing else natural); manual hesitant; timeline multipliers (natural 1.00/1.00/1.00/4, grave 0.65/1.15/0.80/1, hesitant 0.90/1.60/1.05/2, teasing 0.85/1.10/0.90/2) capped by gaze + style; plan fixed at wait start; deterministic seeded RNG; same turn tags don't restart; explicit emotion/gesture/discourse/manual preview priority including explicit zero; negative/unknown emotions never auto-smile; interrupt cancels pending eye motions.
- Review: off by default, in-memory, max 12 turns / 120 s / 1200 frames, text/events have caps; done only when playback queue truly idle; per-sentence wait inside same turn; only actual player source.start() (not audio/text receipt) recorded; resumed playback = separate turn; switching module/calibration/disconnect stops recording; calibration exit restores plan/rhythm/media clock; read-only timeline of gaze + play/interrupt points; ~500 ms refresh of lightweight index; export only on demand; DOM via textContent; no audio/recording/credentials uploaded; no browser storage.
- Components: dialogue-intent.js, dialogue-gaze.js, dialogue-expression.js, dialogue-review.js, dialogue-review-ui.js, index.js, app.js, avatar_lab.js. Assets: seed-daily-lids.vrm (11,883,? bytes; SHA256 `16df…aa2ea…`).
- Tests: `npm run test:dialogue-gaze` 122/122 (51 director, 26 expression, 4 intent, 19 review, 22 integration); plus test:eyes, test:calibration, test:voice, `test:dialogue-gaze:v3:browser` 87/87, V1 56/56, V2 75/75; eye/calibration/lower-lid/voice subsystem regressions.

### Editor companion / quality (`EDITOR_COMPANION.md`, `EDITOR_QUALITY_IMPROVEMENT.md`, `EDITOR_QUALITY_THIRD_REVIEW.md`)
- EditorInsightService.analyze/reply (`server/assistant/editor_insights.py`); budget up to 6144 (single attempt, no auto-retry); stance ≤240 chars, discussion ≤800 chars; reasoning_effort=medium only for step-3.7-flash insight path; plain chat low; 25→45 s timeout.
- Strict quiet: only complete `{"action":"quiet"}` counts as intentional quiet; empty body/finish=length/invalid JSON/missing fields each named failure; truncated JSON even if parseable rejected; reasoning cannot stand in for text. Failure counts against quota, no auto-resend, no bubble, no history write; UI shows reason, keeps draft/input/cursor/scroll.
- Follow-up reply separates raw source pack, real discussion role, current user request; draft claims ≠ verified facts; author style ≠ evidence; bounded new facts narrow judgment while unknown stays unknown; rewrites marked 《建议稿：》, preserve intent & limits, remove unsupported generalizations; never write/save behind author's back; `{"text","replacement"}` with server-side ungrounded-claim marker screen on 《建议稿：》; strip leading completion-state assertions (`COMPLETION_CLAUSE`), half-sentence >40 chars no terminal punctuation invalid, numeric-range validates Chinese numerals, tags stripped inside quotes, drop replacement if only tag/leftover.
- UI: `@media(max-height:560px)` tightens writing-mode margins (10px 12px), no structural change; 502×507 viewport puts textarea top inside first screen. Candidate hashes documented.
- Rollback/ops: `scripts/backup_local_databases.py` pre-restart 6 DBs integrity ok; health 200; step-3.7-flash + StepAudio `livelybreezy-female`; editor insights default closed; evaluator dry-run 0 calls; `--live` requires fixed request budget.
- Tests: backend/API/vendor output/eval WS 136 pass; editor browser 48; error-recovery browser 41; entry/notes/home pass. Ledger 14 runs / 143 vendor requests / 250,271 report tokens across runs.

### Memory / evidence (`PROJECT_DIRECTION.md`, `MEMORY_CONTEXT_DELIVERY.md`, `MEMORY_COMPOSITION_DESIGN.md`)
- Evidence chain (phase 1 first step): original user message returns ID after save; candidate message role/ID/excerpt; same-topic independent facts no longer overwrite; forbid invalidated explicit facts; ambiguous updates preserve conflicts; background writes add close/delete + message order guards; forget_key deletes all matching history; isolated tests company/role, preference correction, third-party/hypothetical, stale task late arrival, deletion, closure.
- Preference contract (`MEMORY_PREFERENCES.md`): tea prefs coexist; verbatim + time tags; cross-scope retirement order; member-level manual maintenance; 194 memory regressions + 37 home browser.
- Shared memory expression (`MEMORY_CONTEXT_DELIVERY.md` 1st increment): presentation.py pure conversion, two paths (normal chat + action assistant) share core + own periphery + confirmation hints; same candidate snapshot retrieval + text-signature cache; store adds effective-candidate query without schema bump (active+done-on-include); 201 memory regressions + 58 assistant/thread + 17 conversion + 12 replayed public outputs offline (0 network, 12 temp DBs reopen identical, SHA256 stable).
- Thread evidence & materials (`SHARED_THREAD_EVIDENCE.md`, `SHARED_THREAD_MATERIALS.md`): confirm request with immutable event versions; persist fixed note refs; current vs history separation by question; fixed saved versions for reference/current/draft/attachment; source validation; old materials never guessed into scope.
- Context budget (`CONTEXT_BUDGET_AND_USAGE.md`): normal chat + action assistant materials into shared char budget; final admission data exposes source list.
- Reading habit two-week loop (`READING_HABIT_TWO_WEEK_TRIAL.md`): discuss→decide→revise→reconnect→rationale review→recap + switch counter-example; 6 offline checks; 318 related Python regressions.
- Objective done modules: NoteStore (SQLite immutable versions, precise version read, delete); NoteProposalService local patch preview/confirm-save; PerformanceDirector + VRM gesture timing; DialogueGazeDirector; EditorInsightService.analyze/reply; LLM.complete_raw; scripts/eval_* and probe harness.
- Memory scope/time design (`MEMORY_COMPOSITION_DESIGN.md`): complementary memory roles (raw evidence+ refs, structured claims+current view, events/process logs, rebuildable index/summary, shared context converter, user management + change propagation); scope vs type independent; priority (current explicit request → most specific active constraint in scope → user global default → persona default); retrieval by object+point in time; timestamp split (utterance/event/write/known-valid interval, unknown preserved); write → model candidate → programmatic verification (identity/role/scope/field/evidence) → add/dup/revise/conflict → update current view.
- Procedural memory offline probe `probe_procedural_memory.py` 13/13 (`PROCEDURAL_MEMORY_EXPERIMENT.md`); eval harness `eval_procedural_memory.py` (default plan only, no domain/model; --prepare-sources builds 6 temp-domain recipes with 0 model calls; --live requires provider/https/base_url/model + dedicated PROCEDURAL_MEMORY_EVAL_API_KEY; pre-reserve requests; per-stage disk dump; PUBLIC_OUTPUT dumps; budget ≤99 requests; report includes model output, action, admit/reject reasons, card reopen readback, per-object versions, budget; optional blinded ranking file; `test_procedural_memory_eval.py` 24 contract tests). Real live runs documented: first 71 requests, 3 candidates all failed fixed validation, no card to P group; V2 induction 3 requests, one card admitted but carries unsupported recovery advice; diagnostic 7 requests (57 tokens) shows new lessons not fabricating recovery but one over-broad new-assertion (full new body verifiable but cannot write with old proposal) + 15 offline checks.

### Full-duplex caveats carried over (from docs)
- Still experimental: no semantic backchannel recognition; non-neural AEC; ASR still post-utterance (not streaming); EchoGuard validated on synthetic linear echoes only — nonlinear distortion/reverb/high-frequency/near-end reverberation/AEC post-processing mismatch may miss or over-suppress; recommended starting from headphones. Real mic/speaker/cloud model end-to-end acoustics/roundtrip latency not measured; programmatic metrics exclude 900 ms wait, not = sound card actual playback latency. Long drafts get readable carrier + voice summary, not infinite narration.

## B. Parked / Future items (not built)

### Voice / duplex
- End-to-end real microphone/speaker + cloud model acoustics & round-trip latency (exit gate for default always-on external output).
- Semantic backchannel classifier; neural VAD/AEC vs mature AEC choice pending error data; streaming ASR (Google Speech-to-Text/Csher etc) for immediate intent probing + per-sentence already-played receipts.
- Headset-first rollout; external-output availability gated by stress-test outcomes (reverb/long echo/continuous noise false barge-in/weak near-end miss already observed).
- Digitization of "pause" micro-commands; strong short-commmand fast-path.

### Memory
- Full fact effectiveness time; attribute synonym disambiguation; full chat-message citation chain; project scope / ObjectRef; cross-domain deletion; synonymous object normalization; evidence of model actually using memory not yet at production scale.
- Memory add management UI (where used / when said / why used / correct / stop / delete) deferred.
- Store degradation behavior under missing SQLite / network,DB outage 404 not 503.
- Cross-project portability gated by two-project switching counter-example.
- Procedural memory hardening (see C).

### Procedural memory / experience cards
- Not wired into online chat or background learning; natural-language retrieval untested; cache invalidation/on-off/retrieval contract not in production; hybrid retrieval; more fault controls; counter-example revision; holdout split by source family; statistical significance/thresholds/p95 CI don't apply; human semantic ratings still empty.
- No new domain skills/tools registration; Voyager-style code from experience not executed; no graph DB / vector indexes deployed.
- Long-term learning still future: using experience to prevent failures, taste/persona stability, long-run eval.

### Editor insight quality
- Initial semantic pass only 1/3 vs "should intervene 3/3"; sustained 2–6/6; recurring failure signatures (completion-state rhetoric, invented obligations, cross-scene inference, unchecked numeric counts, half-sentence delivery, timeouts).
- No determination of invented-obligation compliance (compensation reserve, activity fund per household, extra staff) — not covered by `UNGROUNDED_CLAIM_MARKERS`; requires product decision (accept residual risk with UI disclaimer OR a second model check on rewrite only, cost/latency).
- Generation stability variable (truncation 16.7% in group 4 vs 0 in group 7 dropouts? actually 0/12 no truncation in 7); timeouts tied to budget spent on reasoning.
- No one-click adopt; all results are agent-rated samples and lost real model outputs (15 prior run dirs under /eval deleted after chore commit added /eval to .gitignore; 14:41). Group 3 semantic review missing and cannot be restored.
- Candidate output hashes retained (`53241d0d`, `56839de4…`, `f2d621a8…`); real model outputs lost. Model/persona unchanged in test policy (step-3.7-flash insight medium reasoning, plain chat low, StepAudio `livelybreezy-female`).

### Avatar / character assets
- Still on `seed-daily-lids.vrm` (~11,884,568 bytes; SHA256 `16df…aa2eaa`); lower lid approximate; no new assets/protocol.
- No new asset/skeleton/hair physics/finger/weight additions; performance lab presets ≠ live LLM/TTS; no authored per-sentence semantics validated against real speakers/devices.

### Star-Map / companion UI & self-evolution (frozen / deferred per 2026-10-14)
- `SHARED activity` 3-role orphaned chat tests (partial).
- Desktop shell, transparent window, multi-skin, game control, multi-character collaboration, model fine-tuning, extra vector framework, multi-voice, original music, professional animation, auto portrait, generic shell/desktop ops, multi-user cloud — explicitly deferred.
- Persona self-evolution credits not yet in beta: coach notes, user preference profile, daily observation loops still experimental; must ship more reliable positive/negative examples + blind-rate baseline first.
- Clarification resume / `should_route` keyword per-message without history; wrong OpenAI grammar still bug-prone across turns; ChatGPT/Turbo model swapping support future.
- ObjectRef hierarchy extended to interactions/tasks/backfill channels not yet.

### Cross-cutting
- Docs drift: REQUIREMENTS.md still claims "no VRM"; COMPANION_ASSISTANT_DESIGN.md whole document marked unimplemented; several docs retain 404 references to old process; health has no build/version or complete capability declaration.
- Manual credential/model keys remain unmanaged.

## C. Files touched this freeze (working tree, not yet committed)
- config/avatar.yaml — added ParamEyeLLower/ParamEyeRLower to neutral + response (resolves check_protocol warning).
- docs/EDITOR_QUALITY_IMPROVEMENT.md — added freeze-context notes.
- scripts/eval_editor_quality.py, scripts/test_editor_insight_outputs.py — editor quality evaluator additions.
- server/assistant/editor_insights.py — editor insight service/validators.

## D. Verification baseline
- `python scripts/check_protocol.py` → PASS (6 emotions; 29 params full set; 16 message types report fields).
- `python scripts/test_core.py` (via `.venv`) → 38 passed / 0 failed.
- `python scripts/backup_local_databases.py` → 6 snapshots integrity ok.
- DB snapshot dir: data/backups/20261014T075521410236Z

## E. Focus / ownership for phase 2 (per AGENT_PLAYBOOK)
- Phase 2 targets owned by Role 1 (Memories / Self-evolution) + Role 2 (Speech delivery long continuous voice):
  - F1 episodic Diary ("小溪日记"), drift of attitude, latent sparks into persona.py.
  - F2 neuro-like long voice: micro-chunking + preloaded audio queue + breath/micro-expression markers, interruptible, in timeline.js.
  - F3 autonomous idle loop (assistant/loop.py): curiosity driven self-reading/reflection; Self-Taste / Worldview; proactive topics.
  - F4 star-map memory: backend graph memory + star graph view.
- Role 3 (Star-Map/companion UI) handles frontend graph views; Role 0 holds PROTOCOL.md / app.py / web entry + gates contract amendments.

## F. Role 0 工程基建待办（回归评测集落地时登记，暂不做）
- v0.7 打标后删除 `docs/AGENT_PLAYBOOK.md` 中「打磨轮临时授权」整段。
- 评估在 CI 中按 diff 路径机械校验角色白名单（目前白名单只靠自觉）。

## G. 回归评测 live 实跑暴露的待办（2026-10-17，2026-10-20 复核）

- **emotion-tag-retention-live**：2026-10-20 真实 live 3/3 通过（6 轮×3）；此前 10 次中 3 次失败主要是 `finish_reason=length` 的空正文，不是多轮标签格式漂移。`TESTING.md` 已写明这是装配级抽样，不是稳定性结论。
- **accuracy-over-persona-live**：已改为 `evals.checks:fact_correction_earth` 自定义判定，覆盖"不是。""椭球体""伪科学"等自然纠正；2026-10-20 live 3/3。原“扩 regex 词表”待办关闭。
- **runner live 口径**：仍是装配级（`build_messages` + `LLM.complete`），不含生产 `_render_turn_context` / pacing / 历史裁剪；这是**明确边界**，不是待修 bug。要验全链路请走浏览器/Orchestrator 回归，不要用 live 结果代替。
- **`scripts/test_voice_turns.py` 2 个 TimeoutError**：2026-10-18 已随隔离环境白名单补齐修复；2026-10-20 复跑 31/31 通过。
