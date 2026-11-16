# Security Audit — Therapist SOAP Assistant (FastAPI + MongoDB + Gemini)

| Item | Value |
|------|-------|
| Repository | `zaakiraza/FYP-CHATBOT` (workspace copy; no re-clone was needed) |
| Branch / commit reviewed | `echo/clone-this-repo-and-make-a-security-audit-660d64` at `3674b82` (current default-branch tree and full git history) |
| Review date | 2026-11-16 |
| Review type | **Static, read-only.** No code was changed, the app was not run, no network calls were made, no scanners were run, and no `.env` or key files were read. |
| Standard | OWASP API Security Top 10 (2023), with a health-data privacy note |
| Severity labels | Critical / High / Medium / Low / Info. These are **provisional**: the client has not yet answered the severity-format question (Q-7). |
| Status of fixes | **Recommendations only.** No fixes were applied. Each remediation should be raised as a separate change request. |

---

## 1. Executive summary

This service answers therapists' clinical questions using retrieval over SOAP-note text stored in MongoDB, then passes the retrieved text to Google Gemini to draft a reply.

**Overall risk: Critical, as the code stands on the default branch.**

The main reasons:

1. **The chat endpoint has no authentication.** Anyone who can reach the service can ask questions. Authentication code exists in `app/auth.py`, but it is not connected to any route. The commit `f7f7112` removed the connection.
2. **Answers draw on every therapist's notes.** Retrieval is never limited to the signed-in therapist. The setting `RAG_THERAPIST_SCOPED` that the README describes as the privacy control is not defined in the code, so setting it does nothing.
3. **The documentation says the opposite of the code.** The README, `SCHEMA.md` and `docs/API_AND_ARCHITECTURE.md` describe JWT enforcement, a `SKIP_THERAPIST_JWT` switch, per-therapist scoping, a `/chat` UI, and a `/api/v1/chat` route. None of these are present in the current code. Operators relying on the docs will believe the service is protected when it is not.
4. **Clinical text leaves the system and is stored in readable form.** Full SOAP text is sent to Google for embedding and generation, and is stored in plaintext in `soap_chunks` alongside patient and therapist identifiers. Deleted or edited notes are not removed from the search index.

What is in good shape: no secret values were found in the current tree or in the git history (see Section 6). The chat logs record only counts, not message content. The request body is validated with length limits. No user input is placed into database filters in the current code.

**Top recommendations (for separate change requests):**

| Priority | Recommendation | Findings |
|----------|----------------|----------|
| 1 | Require a verified JWT on every chat route, with therapist identity taken from the token. Consider taking the service off public exposure until this is in place. | F-01, F-02 |
| 2 | Enforce therapist-scoped retrieval server-side, or get explicit client sign-off that cross-therapist answers are acceptable. Then align the docs. | F-03 |
| 3 | Add deletion and edit handling to the indexer, and a retention policy for `soap_chunks`. | F-08 |
| 4 | Confirm the legal basis and data-processing terms for sending clinical text to Google Gemini. | F-06 |
| 5 | Pin dependencies and add a lockfile, then run a vulnerability check in an environment that is allowed to run one. | F-13 |

---

## 2. Scope and coverage

| Area required by PRD | Files reviewed | Covered |
|---|---|---|
| `app/` | `main.py`, `auth.py`, `config.py`, `db.py`, `limiter.py`, `cache.py`, `watcher.py`, `routes/chat.py`, `rag/chunking.py`, `rag/embeddings.py`, `rag/retrieve.py`, `rag/llm.py` | Yes |
| `scripts/` | `backfill_chunks.py`, `debug_counts.py`, `__init__.py` | Yes |
| `frontend/` | **Not present on the current branch.** `frontend/therapist-chat.html` was removed in commit `f7f7112` and exists only in history. It was checked for secret patterns (none found). The README and docs still link to it. | Partially (history only) |
| `express-proxy-snippet.js` | Read in full. It is a comment-only example. | Yes |
| Config files | `requirements.txt`, `runtime.txt`, `Procfile`, `.gitignore`, `README.md`, `SCHEMA.md`, `docs/API_AND_ARCHITECTURE.md` | Yes |
| MongoDB embedding storage | `app/watcher.py`, `scripts/backfill_chunks.py`, `app/rag/retrieve.py`, `SCHEMA.md` | Yes |
| Git history (secrets) | All commits on all refs (9 commits) | Yes, by pattern search (Section 6) |

**Not in scope** (per PRD): the Express backend, the MongoDB server, infrastructure, and live deployments.

---

## 3. Findings

Each finding lists: title, severity, location, description, impact, OWASP API Top 10 category, and recommendation. Recommendations are not implemented.

### F-01 — Chat endpoints have no authentication

- **Severity:** Critical
- **Location:** `app/routes/chat.py:117-133` (`POST /api/therapist-chat`); `app/auth.py` (unused); removed in commit `f7f7112`.
- **Description:** The chat route takes no authentication dependency. `get_current_therapist` in `app/auth.py` is never imported by any route. The route accepts any request.
- **Impact:** Any caller who can reach the service can receive clinical recommendations generated from SOAP content belonging to all therapists (see F-03). The per-IP rate limit (F-04) is the only control.
- **OWASP:** API2:2023 Broken Authentication; API5:2023 Broken Function Level Authorization (the chat function has no role or identity check).
- **Recommendation:** Attach the JWT dependency to every chat route. Take therapist identity only from verified token claims. Reject requests with no or invalid token with 401. Add tests that check a request without a token is refused.

### F-02 — Documented JWT bypass switch is not implemented; auth module references settings that do not exist

- **Severity:** High
- **Location:** `app/auth.py:33-63` (reads `settings.skip_therapist_jwt`, `dev_therapist_id`, `jwt_secret`, `jwt_algorithm`, `jwt_issuer`, `jwt_audience`); `app/config.py:7-75` (none of these fields exist); `README.md:21-23`; `SCHEMA.md:48`; `docs/API_AND_ARCHITECTURE.md:35,46`.
- **Description:** The `SKIP_THERAPIST_JWT` flag is documented as a local-only bypass to turn off before production. In the current code the flag is never read, and the JWT settings it depends on are not defined in `Settings`. If the auth dependency were attached as-is, it would fail with an attribute error on every request. Authentication therefore cannot be turned on or off with configuration; the effective state is permanently "no authentication" (F-01).
- **Impact:** The operator-facing documentation describes a control that does not exist. A team that trusts the README could deploy believing JWT is enforced, or could believe they can disable it. The "bypass" is in effect permanent, so it is never something an operator can turn off.
- **OWASP:** API2:2023 Broken Authentication; API9:2023 Improper Inventory Management.
- **Recommendation:** Define the JWT settings in `Settings`. Remove the skip flag entirely, or, if local development needs a bypass, make it fail closed (refuse to start unless an explicit non-production environment is set). Correct the docs once behaviour is decided. Test that the app refuses to start, or refuses requests, when auth is misconfigured.

### F-03 — Cross-therapist data exposure: retrieval is never scoped to the caller

- **Severity:** Critical (High on its own; raised to Critical because F-01 makes it reachable without any identity)
- **Location:** `app/routes/chat.py:90-99` (`therapist_id_filter=None` hard-coded); `app/rag/retrieve.py:65-71`; `SCHEMA.md:3,5,15`; `README.md:25-28` (retrieval scope section); `docs/API_AND_ARCHITECTURE.md:110`.
- **Description:** The retrieval call passes `therapist_id_filter=None` unconditionally. The setting `RAG_THERAPIST_SCOPED` existed in an earlier commit (`a12f614`) and was removed in `f7f7112`, and the current `Settings` class has no such field. The docs describe `RAG_THERAPIST_SCOPED=false` as the default and `true` as the stricter privacy option. In the current code, the answer is always the unscoped case, and the privacy option cannot be selected.
- **Impact:** A therapist's question can be answered using excerpts from other therapists' clients' SOAP notes, and the reply can reflect the content of those notes. Combined with F-01, anyone can run repeated questions to pull out clinical content from across the whole corpus (see F-04 on rate limits).
- **OWASP:** API1:2023 Broken Object Level Authorization (other therapists' records are reachable); API6:2023 Unrestricted Access to Sensitive Business Flows (question-by-question extraction).
- **Recommendation:** Decide the intended product rule with the client (scoped by default, or cross-therapist with a documented legal basis). Enforce the chosen rule server-side from the verified token, not from client input. Restore a real configuration field if a switch is wanted. Add tests that show a caller only receives chunks from their own therapist ID when scoped.

### F-04 — Rate limiting is per-IP, in-memory, and inconsistently configured

- **Severity:** High
- **Location:** `app/limiter.py:1-9`; `app/config.py:53` (`rate_limit_per_minute`, never used); `app/main.py:55-56`; `app/routes/chat.py:127`.
- **Description:** The limit is set per client IP using slowapi's default in-memory store, so it resets on restart and is per process. It applies only to the chat route. The limit value is read with `os.getenv` at import time, so a `RATE_LIMIT_PER_MINUTE` value set in `.env` is ignored unless also exported to the environment. The `Settings` field for the same value is never used.
- **Impact:** Rotating source IPs bypasses the limit. Each allowed request triggers paid Gemini calls (embedding and generation), so the limit is also a cost control. Operators who set the value in `.env` will believe it is in effect when it is not.
- **OWASP:** API4:2023 Unrestricted Resource Consumption.
- **Recommendation:** Use one configuration source for the limit. Apply limits to all chat routes. Once authentication exists (F-01), limit per authenticated identity as well as per IP. Consider a shared store if more than one process is run. Document the limit's real behaviour.

### F-05 — Retrieval loads up to 8,000 full vectors per request, unsorted, blocking the event loop

- **Severity:** High
- **Location:** `app/rag/retrieve.py:65-113` (brute-force path); `app/config.py:32` (`RAG_MAX_CHUNKS_FETCH` default 8000).
- **Description:** Every chat request (on a cache miss) runs `collection.find(filt, projection).limit(max_fetch)`, pulling up to 8,000 documents with their full `embedding` arrays into Python lists, then converts them to a numpy matrix. The query has no sort, so the subset considered is arbitrary (natural order) once the corpus is larger than the limit. The numpy operations are synchronous inside an `async` function, so they block the event loop while they run.
- **Impact:** Each request can use a large amount of memory and CPU. The vector size for the configured model is typically large (the default output of `gemini-embedding-001` is commonly cited as 3072 dimensions; this is **unverified** against current Google documentation). With 8,000 chunks, the in-memory footprint per request could plausibly reach several hundred MB; this is an estimate, not a measurement. Concurrent requests multiply the load. Relevant chunks may never be considered once the corpus grows past the limit, which is a correctness issue as well as a resource one.
- **OWASP:** API4:2023 Unrestricted Resource Consumption.
- **Recommendation:** Use the Atlas vector search path with a vector index for corpora of any size, or cap and index the brute-force path. Run numpy work off the event loop (for example in a thread) or make it cheaper. Measure memory and latency under load in a separate, approved test (not part of this static audit).

### F-06 — Clinical text is sent to a third party (Google Gemini)

- **Severity:** High (privacy; see Section 5)
- **Location:** `app/rag/embeddings.py:41-86` (query and document embeddings); `app/rag/llm.py:34-105` (prompt with SOAP excerpts and the therapist's question); `app/watcher.py:45-63`; `scripts/backfill_chunks.py:88,146,204`.
- **Description:** SOAP excerpts are sent to Google's embedding API when they are indexed, and each question is sent to Google's embedding API. Retrieved SOAP excerpts and the question are sent to Google's generation API in the prompt. Nothing in the repository records a data-processing agreement, a legal basis, or an approved set of data categories.
- **Impact:** Highly sensitive health information leaves the organisation's control for each indexed chunk and each question. Whether this is lawful depends on the applicable regime and contracts, which are outside this repository. This audit cannot determine it.
- **OWASP:** API10:2023 Unsafe Consumption of APIs (the third-party service is trusted with sensitive data without a documented boundary); see Section 5 for the privacy analysis.
- **Recommendation:** Confirm the contract and data-residency terms with the client before production use. Document which fields leave the system. Minimise: send only the text required (strip identifiers before embedding, where clinically acceptable). Consider a provider that offers a suitable agreement for health data.

### F-07 — Patient and therapist identifiers stored alongside plaintext clinical text; the "anonymised" claim is not enforced

- **Severity:** High
- **Location:** `app/watcher.py:48-58`; `scripts/backfill_chunks.py:91-101`; `app/rag/chunking.py:57-135,166-327` (chunk drafts copy raw text); `app/routes/chat.py:48,111-114` (API says replies are "anonymised").
- **Description:** Each `soap_chunks` document stores the raw SOAP text (`text`), `therapistId`, `patientId`, `sessionId`, and `sessionDate` in readable form. Nothing in the code removes names or other identifiers that may appear inside free-text notes. The API description, the prompt, and the response schema all say replies are anonymised, but this rests on the language model being told not to output identifiers, and on nothing else.
- **Impact:** A database read gives a complete plaintext copy of clinical notes with patient linkage. The model may still repeat identifiers that are present in the excerpts, despite the prompt instruction. The anonymisation claim is therefore not a control.
- **OWASP:** API3:2023 Broken Object Property Level Authorization (excess data is stored and can be exposed); data minimisation is covered in Section 5.
- **Recommendation:** Store only the fields needed for retrieval. Remove `patientId` if the UI does not need it (the docs mention optional UI filtering, which is not implemented). Apply de-identification to free text before indexing, and validate outputs before returning them. Use database-level encryption at rest and access control for the MongoDB deployment (outside this repo).

### F-08 — Deleted and edited notes remain searchable; no retention policy

- **Severity:** High (privacy)
- **Location:** `app/watcher.py:89` (change stream matches only `insert`, `update`, `replace`); `app/watcher.py:33-35,46` (chunk key is a hash of the text); `scripts/backfill_chunks.py:71-73,87-106` (no removal of stale chunks).
- **Description:** The change stream does not handle `delete` events, so removing a source record does not remove its chunks from `soap_chunks`. Chunk keys are derived from the text, so when a note is edited the new text is written as a new chunk and the old text stays in the collection. The backfill script never removes chunks whose source has gone. There is no TTL or retention field.
- **Impact:** Clinical text that was deleted or corrected at source can continue to be retrieved and used in replies indefinitely. This conflicts with erasure and retention expectations for health data.
- **OWASP:** API8:2023 Security Misconfiguration (no lifecycle control on sensitive data). Privacy analysis in Section 5.
- **Recommendation:** Handle `delete` events and remove the matching chunks. On update, delete chunks for that source record that are no longer present. Add a documented retention period and a purge job. Add tests for delete and edit propagation.

### F-09 — Upstream error details and internal status returned to clients

- **Severity:** Medium
- **Location:** `app/rag/llm.py:128-131` (`detail=f"Gemini API error ({code}): {e!s}"`); `app/routes/chat.py:38-49` (`key_used` in the response).
- **Description:** When Gemini returns a non-retryable error, the raw exception text is included in the HTTP response. The response also reveals which internal key slot (`key_1`, `key_2`) served the request.
- **Impact:** Internal error text can reveal provider details, quota state, or configuration to any caller (which, per F-01, is anyone). The key-slot label gives an attacker information about the number of keys and which was used. Neither is needed by the client.
- **OWASP:** API8:2023 Security Misconfiguration (verbose errors).
- **Recommendation:** Return a generic error message with a correlation ID. Log the detail server-side without secrets or patient content. Remove `key_used` from the public response, or make it internal-only.

### F-10 — Response and embedding cache: unscoped keys, low-entropy hashes, unauthenticated Redis by default

- **Severity:** Medium
- **Location:** `app/cache.py:86-89` (key = SHA-256 of lowercased, trimmed text); `app/cache.py:136-142` (response cache keyed by question only); `app/cache.py:28-54` (Redis connection from `REDIS_URL`, no TLS or auth requirement in code); `app/routes/chat.py:80-83`.
- **Description:** The response cache key is the question alone. Once scoping is restored (F-03), a reply generated for one therapist's scope would be served to any other therapist who asks the same question, which is a latent cross-therapist leak. Keys are unsalted hashes of the question; common clinical questions can be checked against the cache by anyone who can read Redis keys. Cached replies and question embeddings (both derived from clinical text) are stored in Redis for up to 24 hours, and the code does not require TLS or Redis authentication.
- **Impact:** Latent cross-therapist leak when scoping is enabled. Anyone with read access to Redis can see cached clinical replies and test whether a known question was asked. Redis is a second store of health-derived data that is not covered by the MongoDB controls.
- **OWASP:** API8:2023 Security Misconfiguration.
- **Recommendation:** Include the caller's scope in the cache key, or disable the response cache when scoping is on. Use keyed hashing (HMAC with a server-side secret) for keys. Require TLS and authentication for Redis in deployment docs and checks. Shorten the TTLs for clinical content if the product allows.

### F-11 — Network exposure and default configuration

- **Severity:** Medium
- **Location:** `Procfile:1` and `README.md:13` (`--host 0.0.0.0`); `app/main.py:35-54` (FastAPI defaults: `/docs`, `/redoc`, `/openapi.json` enabled in all environments); `app/config.py:48-51` (default CORS origins include `https://therakonnect.com` and `http://localhost:3000`); `app/main.py:68-74` (CORS with `allow_credentials=True`, all methods and headers).
- **Description:** The service listens on all interfaces, which with F-01 makes it reachable from any network that can route to the host. The interactive API documentation is on in every environment with no switch. The default CORS list includes a local development origin. Credentials are allowed cross-origin with wildcard methods and headers. No security headers are set by the app.
- **Impact:** A public host exposes the full API surface and its schema to anyone. A development origin in production defaults widens the set of browser origins that can call the API with credentials. Wildcard headers and methods are broader than the single chat POST requires.
- **OWASP:** API8:2023 Security Misconfiguration.
- **Recommendation:** Disable the docs routes outside development. Remove `localhost` from production defaults. Restrict methods and headers to what the client uses. Terminate TLS in front of the service and bind to the private interface where possible. Add standard security headers at the proxy or in the app.

### F-12 — Prompt construction: question and retrieved text are concatenated without guards

- **Severity:** Low
- **Location:** `app/rag/llm.py:34-72` (question and excerpts concatenated into one prompt); `app/rag/llm.py:97-105` (model output returned verbatim).
- **Description:** The therapist's question and retrieved SOAP text are placed directly in the prompt. Notes can contain text that tries to change the model's instructions (indirect prompt injection). The output is returned without validation.
- **Impact:** A crafted note or question could make the model ignore its rules (for example, reveal identifiers or give out-of-scope advice). Impact is limited to the reply content, but the reply is delivered to clinicians.
- **OWASP:** API10:2023 Unsafe Consumption of APIs (model output is trusted as-is).
- **Recommendation:** Treat excerpts as data (delimit and label them clearly). Validate replies against simple rules before returning (for example, length and identifier checks). Test with adversarial notes in a separate, approved exercise.

### F-13 — Dependencies are unpinned, have no lockfile, and have not been checked for known vulnerabilities

- **Severity:** Medium (unverified)
- **Location:** `requirements.txt:1-12`; `runtime.txt:1`.
- **Description:** Every dependency uses a lower bound only (`>=`), so installs resolve to whatever is newest at install time. There is no lockfile and no pinned version. `redis` is listed twice (lines 11 and 12). `python-multipart` (line 9) appears unused: no form or file-upload route was found in the code. `runtime.txt` specifies Python 3.12, while the README says "Python 3.10+".
- **Impact:** Builds are not reproducible, and a compromised or vulnerable new release could be picked up silently. Unused packages add attack surface.
- **Known vulnerabilities:** **Unverified.** No dependency scanner (such as `pip-audit`) was run, as the PRD forbids. Because the file pins no versions, the set of installed versions cannot be determined from the repository. Any statement about specific advisories would require an authorised scan of a resolved environment.
- **OWASP:** API10:2023 Unsafe Consumption of APIs (supply chain); API8:2023.
- **Recommendation:** Pin versions with a lockfile. Remove unused packages. Run a dependency vulnerability scan in an approved environment and record the results in a follow-up change request. Align the Python version in the README with `runtime.txt`.

### F-14 — Documentation and API inventory do not match the code

- **Severity:** Medium
- **Location:** `docs/API_AND_ARCHITECTURE.md:25-35` (endpoint table lists `GET /`, `GET /chat`, `GET /ui/therapist-chat.html`, `POST /api/v1/chat`); `docs/API_AND_ARCHITECTURE.md:52-54` (`patient_id` and `session_id` request fields); `docs/API_AND_ARCHITECTURE.md:137-138` (`app/main.py` said to serve `/chat`); `README.md:42` (frontend demo, links to the absent `frontend/therapist-chat.html`).
- **Description:** The docs list endpoints, request fields, and a frontend that the current code does not implement. `app/routes/chat.py` defines only `POST /api/therapist-chat`, and `ChatRequest` has only `message`. The `/api/v1/chat` alias, the landing page, and the `/chat` UI are absent.
- **Impact:** Operators and reviewers rely on a wrong picture of what is exposed. Security decisions made from the docs (for example, "auth is on", "scope is on") will be wrong (see F-01 to F-03).
- **OWASP:** API9:2023 Improper Inventory Management.
- **Recommendation:** Regenerate or correct the docs from the code, or restore the features deliberately, with their security controls, in a separate change request. Remove the links to the absent frontend.

### F-15 — Operator script prints database inventory without guard

- **Severity:** Low
- **Location:** `scripts/debug_counts.py:10-25`.
- **Description:** The script lists every collection in the configured database and prints document counts to stdout. It has no guard, and it creates its own client rather than using `app/db.py`.
- **Impact:** Limited to whoever runs it, but output may be captured in logs or shared. It discloses database structure, which is useful to an attacker who gains a shell.
- **OWASP:** API9:2023 Improper Inventory Management.
- **Recommendation:** Restrict to local use, or remove it from the deployed package. Use the shared database helper.

### F-16 — Express proxy example would expose the unauthenticated service

- **Severity:** Low (depends on F-01)
- **Location:** `express-proxy-snippet.js:1-17`.
- **Description:** The file is a comment-only example that forwards `/chatbot-api` to the Python service. It sets no authentication and does not forward the caller's identity.
- **Impact:** If this pattern is adopted as written, every browser user of the Express app would reach the unauthenticated chat API. The pattern itself is reasonable once F-01 is fixed.
- **OWASP:** API2:2023 Broken Authentication (by inheritance).
- **Recommendation:** When adopting this pattern, verify the Express session server-side and forward only a verified identity claim. Do not rely on the proxy alone for access control.

### F-17 — Repository hygiene

- **Severity:** Low
- **Location:** `.gitignore:2` (ignores `.env.example`, which is not in the repo although `README.md:8` refers to it); `.gitignore:8` (stray line `5.0.0`); 31 compiled `*.pyc` files tracked in git (for example `app/__pycache__/auth.cpython-311.pyc`), although `.gitignore` excludes `__pycache__/`.
- **Description:** Compiled bytecode is committed, which may contain stale code and identifiers. The example environment file cannot be committed and is absent, so operators must construct configuration from the README alone. The stray ignore line has no effect but signals loose maintenance.
- **Impact:** Stale or misleading artefacts in the repository; slower and less reliable configuration setup. No secret values were found in these files (see Section 6).
- **OWASP:** API9:2023 Improper Inventory Management (informational for most items).
- **Recommendation:** Remove tracked `.pyc` files (`git rm --cached`) in a separate change. Provide a non-secret `.env.example` template (with placeholder values only) and allow it in `.gitignore`. Remove the stray line.

### F-18 — Positive observations (no action required)

These are recorded so the report is balanced. They are not findings.

- **No secret values found** in the current tree or in history (Section 6).
- **Chat logs record only counts and paths**, not message content (`app/routes/chat.py:101`; `app/main.py:64-66`).
- **Request validation:** `ChatRequest.message` has a minimum and maximum length (1–8000 characters) and is typed (`app/routes/chat.py:29-35`).
- **No injection into database filters:** the current route passes no user-controlled values into MongoDB queries. The planned `patient_id` and `session_id` filters (documented but not implemented) would need care to avoid operator injection if added.
- **Exception handling:** no custom handler returns stack traces. FastAPI's default 500 response does not expose internals.
- **Bounded input:** the 8000-character limit prevents very large prompts from user input (though it does not limit the request body before parsing; see F-05 for resource concerns).

---

## 4. OWASP API Security Top 10 (2023) mapping

| Category | Relevant findings | Assessment |
|---|---|---|
| API1 Broken Object Level Authorization | F-03 | Any caller can reach other therapists' records through retrieval. |
| API2 Broken Authentication | F-01, F-02, F-16 | No authentication on chat; documented auth controls are absent. **Most serious category.** |
| API3 Broken Object Property Level Authorization | F-07 | Excess identifiers and raw text are stored and can surface in replies. |
| API4 Unrestricted Resource Consumption | F-04, F-05 | Per-IP limit is bypassable; large per-request memory and blocking CPU. |
| API5 Broken Function Level Authorization | F-01 | No role or identity check on the chat function. |
| API6 Unrestricted Access to Sensitive Business Flows | F-03 | Repeated questions can extract clinical content across the corpus. |
| API7 Server Side Request Forgery | None found | No outbound requests driven by user input. |
| API8 Security Misconfiguration | F-08, F-09, F-10, F-11 | Verbose errors, public docs, open network bind, cache and lifecycle gaps. |
| API9 Improper Inventory Management | F-02, F-14, F-15, F-17 | Documentation and inventory do not match the code. |
| API10 Unsafe Consumption of APIs | F-06, F-12, F-13 | Sensitive data sent to Gemini; model output trusted; unpinned dependencies. |

---

## 5. Health-data privacy note

The data processed by this service is therapy SOAP notes, which are health information about identifiable people and are normally treated as a special category under data protection law. Whether the applicable regime is HIPAA, UK GDPR, EU GDPR, or another regime is **not stated in the repository and is an open question for the client**. This section describes concerns from the code; it is not legal advice.

| Concern | Evidence | Relevant findings |
|---|---|---|
| **Purpose and access.** Clinical content is available to any caller and to other therapists' answers. | F-01, F-03 | Access is not limited to the clinicians entitled to see each note. |
| **Third-party processing.** Text and questions are sent to Google Gemini. | F-06 | No agreement or data-residency terms in the repo. |
| **Minimisation.** Identifiers and full text are stored in a search index that does not need them all. | F-07 | `patientId`, `therapistId`, `sessionDate`, and full text are stored. |
| **Retention and erasure.** Deleted or edited notes are not removed from the index. | F-08 | No delete handling and no retention policy. |
| **Secondary copies.** Replies and question embeddings are cached in Redis for hours. | F-10 | Redis is not covered by MongoDB controls. |
| **Information in errors and logs.** Upstream error text is returned to callers. | F-09 | Could contain clinical or configuration details. |
| **Transparency.** Claims of anonymisation are made in the API description and prompt but are not enforced. | F-07 | Misleading to clinicians and patients if relied on. |

**Recommended privacy actions (for the client to decide):** confirm the applicable regime and lawful basis; obtain or confirm data-processing terms with Google; decide the cross-therapist access rule; define retention and deletion; and conduct a formal data protection impact assessment before production use.

---

## 6. Secrets and git history

- **Method:** Pattern search over all commits on all refs (9 commits), using `git log -G` and `git grep`. The search matched on common key shapes (Google-style API keys), credentialed database and Redis URIs, private-key blocks, and assignments to known variable names (`JWT_SECRET`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `MONGODB_URI`, `REDIS_URL`) with values. A broader keyword search was then run and reviewed manually at the file level.
- **Result:** **No secret values were found** in the current tree or in any commit. Hits on keywords were variable names, parameter names, or documentation text only.
- **Files ever committed that could hold configuration:** none. No `.env`, key, or credentials file appears in any commit.
- **Limitations:** This is a regular-expression search, not a dedicated secret-scanning tool. It would miss secrets in unusual formats or obfuscated values. No secret values were reproduced in this report. If a real secret is suspected, it should be rotated regardless of this result.
- **Removed files:** `frontend/therapist-chat.html` existed in history until commit `f7f7112`. It was included in the search above and contained no matches.

**AC-8 statement:** Secrets were **not** found in git history for the patterns searched. No values are reproduced.

---

## 7. Limitations and checks not run

| Check | Status | Reason |
|---|---|---|
| Running the application | **Not run** | Static review only (PRD R-5). |
| Dynamic or penetration testing | **Not run** | Out of scope and forbidden (PRD R-5). |
| Dependency vulnerability scan (`pip-audit` or similar) | **Not run** | Forbidden by PRD R-5. Dependency risk is **unverified** (F-13). |
| Dedicated secret scanner | **Not run** | Only the regex search in Section 6 was used. |
| Automated lint, type, or test checks | **Not available** | No ruff, flake8, mypy, or pytest configuration detected. No tests exist in the repo. |
| Reading `.env` or key material | **Not done** | Forbidden (PRD R-5; security rules). `.env` is git-ignored and was not in the reviewed tree. |
| Live database, Redis, or Gemini configuration | **Not checked** | Outside this repository. |
| Frontend (`frontend/`) | **Not present** on this branch | Only in history; no current code to review. |
| Express backend and MongoDB server | **Out of scope** | PRD. |
| Atlas vector search path | **Reviewed by code reading only** | Not exercised. |
| Resource-usage claims (F-05) | **Estimates** | Not measured. |
| Severity scale | **Provisional** | Q-7 unanswered. |
| Legal and regulatory compliance | **Not assessed** | Requires client confirmation of applicable regime (Section 5). |

**Note on the PRD's "clone this repo":** the repository was already present in the workspace on the required branch, so no fresh clone was made.

---

## 8. Recommended follow-up change requests

Fixes are **not** part of this audit (PRD R-7). Suggested groupings, each to be a separate change request:

1. **Authentication and scoping** (F-01, F-02, F-03, F-16): restore JWT enforcement, define the scope rule, and align the docs.
2. **Data lifecycle and privacy** (F-07, F-08, F-10): delete and edit propagation, de-identification, retention, cache keying.
3. **Resource and abuse controls** (F-04, F-05): unified rate limits, index-backed retrieval, off-loop compute.
4. **Error handling and exposure** (F-09, F-11, F-15): generic errors, docs off in production, CORS tightening, script restriction.
5. **Dependency and repository hygiene** (F-13, F-14, F-17): lockfile, vulnerability scan in an approved environment, docs correction, `.pyc` removal, example config.
6. **Prompt and output handling** (F-12): delimiting, output validation, adversarial tests.
7. **Third-party data terms** (F-06): contract and data-residency review (business and legal, not a code change).

---

## 9. Open questions for the client

- **Q-7 (open, non-blocking):** Severity rating format is not yet confirmed. The labels here are provisional.
- Intended access rule: should therapists see only their own notes, or are cross-therapist answers an approved product feature? (F-03)
- Applicable data protection regime and whether data-processing terms with Google are in place. (F-06, Section 5)
- Retention and deletion requirements for SOAP-derived chunks. (F-08)
