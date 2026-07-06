# STRIDE Threat Model — BirdSong Bridge

A focused threat model for a single-user acoustic field-journal prototype. The
goal is honest scoping: what is mitigated, what is a deliberately accepted gap
for a prototype at this scale, and why. STRIDE = Spoofing, Tampering,
Repudiation, Information Disclosure, Denial of Service, Elevation of Privilege.

## System summary

- **Frontend** (`frontend/index.html`) — static page, calls the backend over HTTPS.
- **Agent backend** (`backend/main.py` + `app/`) — FastAPI wrapping an ADK 2.0 graph.
- **Data layer** (`bird_data.py`, `journal_store.py`) — external API calls + SQLite.
- **MCP server** (`mcp_server/server.py`) — the same data layer exposed as MCP tools, verified standalone.

The one place untrusted, open-ended input enters and later reaches an LLM prompt
**and** gets persisted is the user's free-text **field notes**. That is where the
security work is concentrated, rather than spread thin and generic.

---

## S — Spoofing

| Threat | Mitigation / status |
|---|---|
| Caller impersonates the frontend to the backend | **Accepted gap (prototype).** No per-user auth — the app is single-user and stores no personal accounts. Cloud Run + HTTPS provides transport identity. Adding auth (e.g. Firebase Auth) is the first step for a multi-user version. |
| Backend spoofs an upstream API | Not applicable — all upstreams (eBird, iNaturalist, xeno-canto) are queried over HTTPS with keys held server-side. |

## T — Tampering

| Threat | Mitigation / status |
|---|---|
| Malicious free-text note tampering with an LLM prompt (prompt injection) | **Mitigated.** `app/security.py::detect_injection()` screens every note; on a hit, `sanitize_user_notes()` replaces the note with a neutral placeholder **before** it reaches `journal_writer_agent`. This runs as its own deterministic graph node (`sanitize_node`), so it cannot be silently skipped by a later prompt change. |
| Tampering with data in transit | HTTPS end to end (Cloud Run). |
| Tampering with the SQLite file | Server-local file; not user-writable through the API surface. |

## R — Repudiation

| Threat | Mitigation / status |
|---|---|
| No audit trail of actions | **Accepted gap (prototype).** Single-user, no destructive multi-user actions to dispute. Cloud Run request logs provide basic traceability. |

## I — Information Disclosure

| Threat | Mitigation / status |
|---|---|
| API keys leaking into code or the repo | **Mitigated.** All keys read from environment variables; `.env` is git-ignored; a **pre-commit hook with a custom Semgrep rule** (`.semgrep/rules.yaml`) blocks hardcoded Google-style keys (`AIzaSy…`) and high-entropy `*_key` / `*_token` assignments at commit time. |
| PII in a user note being persisted or sent to the model | **Mitigated.** `redact_pii()` scrubs detected PII from notes as part of `sanitize_node`, before persistence and before the LLM prompt. |
| Uploaded audio left on disk | **Mitigated.** The uploaded clip is written to a temp file and deleted in a `finally` block after processing. |
| Keys visible in deployment config | Deployed via Secret Manager (`--set-secrets`), not plain env vars, so they don't appear in `gcloud run services describe`. |

## D — Denial of Service

| Threat | Mitigation / status |
|---|---|
| Oversized upload exhausts memory | **Mitigated.** 15 MB upload cap enforced in the backend. |
| Cost / quota exhaustion from traffic | **Partially mitigated.** Gemini free tier and eBird/xeno-canto rate limits act as natural ceilings; Cloud Run `--max-instances` caps concurrency. Not hardened against a determined attacker — acceptable for a demo. |
| One flaky upstream API crashes the request | **Mitigated.** Every external call catches errors and returns a clean `{"error": …}` dict instead of raising. |

## E — Elevation of Privilege

| Threat | Mitigation / status |
|---|---|
| A tool call does more than intended | **Mitigated by design.** The data layer only ever reads external APIs or writes the local journal — there is no shell, filesystem, or arbitrary-network tool exposed. The graph reaches external services **only** through that fixed tool surface. |
| Injection escalating into tool misuse | Bounded — even a successful injection reaches only the journal-writing agent, which has no destructive tools; and the note is screened before it gets there. |

---

## Deliberately accepted gaps (prototype scope)

1. **No authentication / multi-user isolation.** Single-user tool; auth is the first item for a shared deployment.
2. **SQLite is not durable on Cloud Run** across instance recycling — a documented reliability tradeoff, not a security one; the storage layer is isolated so it can be swapped for Firestore in one file.
3. **No rate-limiting middleware** beyond upstream quotas and `--max-instances`.

The principle throughout: concentrate real, tested defenses on the one genuine
attack surface (untrusted free text → LLM + persistence), keep every tool
narrow and read-mostly, and be explicit about what a prototype at this scale
does not yet defend.
