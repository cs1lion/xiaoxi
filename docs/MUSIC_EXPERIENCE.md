# Original-song music adapter (offline by default)

## Scope and truthfulness

The pure adapter now has an **opt-in application route and original-music page**, but is disabled by default. Development has made no actual provider or paid calls: all verification uses fake HTTP responses. The adapter itself never reads configuration or persists audio; the integrating route uses only dedicated music settings, never chat/subscription credentials.

Open `/static/music.html` from the main page. Before deliberate real use, configure locally (never post keys in chat):

```text
MUSIC_ENABLED=true
MUSIC_URL=https://api.stepfun.com/v1/audio/music
MUSIC_API_KEY=<dedicated authorized key>
```

Restart the project backend and refresh the page. `GET /api/music/capabilities` only reports configuration, never proves provider entitlement. `POST /api/music/create` requires an explicit user confirmation. Unconfigured mode returns unavailable without opening a provider client. One generation per process is allowed; this is not a cross-worker quota. Audio is transient, not stored in project files; the browser owns and releases its playback Blob.

The goal is an original song from a style prompt and optional original lyrics.
Omitting lyrics leaves lyrical content to the music provider; the project does not call a separate LLM to write lyrics. This is not a live duet, singing
score, real-time streaming performance, or voice cloning experience. Cover and
dry-vocal input tasks are deliberately not exposed. Do not add unsupported UI
controls or claim these capabilities exist in this adapter.

## Verified official protocol

Source inspected: [StepFun music API documentation](https://platform.stepfun.com/docs/zh/api-reference/audio/music.md).

- Explicit music base URL: `https://api.stepfun.com/v1/audio/music`.
- `POST /submit` with `Authorization: Bearer <dedicated music key>` and
  `Content-Type: application/json; charset=utf-8`.
- Request fields implemented: `task: "text_to_music"`,
  `model_id: "stepaudio-3-music-preview"`, `caption` (the prompt), optional `lyrics`,
  and `response_format` (defaults to `mp3`; also supports `wav/flac/opus/pcm`).
- Submit response: `{ "task_id": "..." }`.
- `POST /query` with `{ "task_id": "..." }`; documented statuses are `PENDING`,
  `RUNNING`, `SUCCESS`, and `FAILED`. Poll no faster than the recommended five seconds.
- `SUCCESS` returns **Base64 `audio`**, `response_format`, `sample_rate`, and
  optional rewritten caption/lyrics. It does **not** document an audio URL.
- `FAILED` is a terminal business failure even when HTTP status is 200; the
  adapter reports `generation_failed` without exposing provider error messages.
- The official page documents **no cancel endpoint**. `supports_remote_cancel`
  is false; local cancellation must never be presented as cancellation of billing
  or removal of an already queued provider task.

Music is asynchronous: complete songs generally take tens of seconds to minutes,
with duration model-controlled (typically 1–3 minutes), not precise or real-time.
Availability, preview pricing and any free-preview promotion may change. The
`preview` model name does not guarantee free use; check official pricing/account
entitlements before opting in. This documentation makes no free-call promise.

## Exact adapter interface

`MusicTransport(*, music_url="", music_key="", http_client=None, enabled=False,
request_timeout=30, total_timeout=600, poll_interval=5, max_polls=120,
sleep=asyncio.sleep)`

- `await submit(prompt, lyrics=None, response_format="mp3") -> str`: task ID.
- `await query(task_id) -> dict`: validated status and bounded text metadata;
  success includes decoded `audio: bytes`, format and sample rate; failure has a
  normalized `error_stage`.
- `await generate(prompt, lyrics=None, response_format="mp3", cancel_event=None)
  -> dict`: submits once, waits between queries, returns success, or raises a
  safe `MusicError` with `.code` and optional `.task_id`.
- Setting `cancel_event` (an `asyncio.Event`) or cancelling the coroutine raises
  `asyncio.CancelledError`, cancelling local HTTP/poll work and cleaning up child
  tasks. Already submitted remote work may continue and may incur charges.

Opt-in requires **all** of `enabled=True`, the explicit official music URL, a
separately supplied music key, and an injected client. Never source this key from
chat subscription/session credentials or silently reuse another provider's key.
The transport does not create or own a real HTTP client.

The injected async client's contract is:

```python
await client.post(url, headers={...}, json={...},
                  timeout=request_timeout, follow_redirects=False)
# returns response.status_code: int and response.json(): dict (synchronous)
```

An HTTPX-compatible client can implement this contract, but a production wrapper
must enforce a **response-body size cap before buffering/parsing** (48 MiB
recommended), own its lifecycle, avoid logging keys/body/audio, and honor timeouts
and task cancellation. The integrating route supplies a bounded streaming HTTPX wrapper, activated only after explicit configuration and a user-confirmed request.
Hard deadlines use non-waiting cancellation: even a cancellation-resistant
injected client cannot keep the caller waiting past the deadline. One outstanding
HTTP task and one generation are tracked per transport; new work is rejected as
`music_busy` until stubborn prior work settles, preventing task accumulation.
Production clients should still honor cancellation to release resources promptly.
No automatic retries are made,
especially on ambiguous submit failures that might otherwise duplicate paid work.
HTTP errors are deliberately sanitized; transport exceptions, credentials, and
provider messages are not surfaced.

## Application validation

`python scripts/test_music_api.py` tests disabled/no-network gates, strict request consent, one-generation process lock, output limits and bounded HTTP parsing. `node scripts/test-music-browser.mjs` tests actual static page assets, disabled state, explicit consent, sanitized errors, audio Blob release and stop warnings. These remain offline tests, not provider entitlement/quality checks.

## Validation and SSRF boundary

Only the exact documented HTTPS origin/path is accepted for the configured base;
userinfo, alternate hosts, insecure schemes, query strings and fragments are
rejected. Redirect following is disabled. Task IDs are bounded opaque strings,
never task URLs or paths. Prompt/lyrics have application safety caps of 4,000 and
16,000 characters (not claimed provider limits); empty explicit lyrics and control
characters are rejected. Response task/status, text metadata, format, sample rate
and strict Base64 are validated. Decoded output is capped at 32 MiB. Validation
confirms encoding/schema, not that bytes are playable media.

**Never server-fetch arbitrary provider-returned audio URLs.** This adapter does
not fetch audio URLs, return remote URLs, or add a proxy/download endpoint. It
accepts the documented inline Base64 audio only. Unknown fields are not returned.
If a future official protocol changes to remote audio, implement a separately
reviewed HTTPS client-playback URL contract, rather than silently adding server
fetching or guessing schema. Signed URLs must not be logged or persisted casually.

The integrating caller owns playback, any persistence, disclosure of remote data
sharing/cost, and applicable AI-generated-content labeling. PCM is raw headerless
media; playback needs explicit format handling, not assumptions about an MP3 file.

## Offline tests

Run `python scripts/test_music.py`. The standalone test loads only this module,
uses no application imports or credentials, and never calls a provider. It covers
exact submit/query bodies, optional lyrics, offline defaults, configuration and
input validation, URL/task-ID rejection, Base64 and size checks, unsafe returned
URLs, malformed responses, failures, bounds, timeout cleanup, and event/coroutine
cancellation. A successful fake test is not a live-provider compatibility or
pricing guarantee.
