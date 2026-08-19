# LLM Gateway — Backend (FastAPI)

## What this is

- **Gateway endpoint** (`POST /v1/call_llm`) — accepts the exact same payload
  shape as OpenAI's `chat.completions.create` (model, messages, tools,
  tool_choice, temperature, response_format, etc.) and returns the exact same
  response shape. Only three things are required to call it: a `provider`
  field (`"openai"`, `"anthropic"`, or `"qwen"`), a `model`, and the caller's
  own upstream key in the `X-Provider-Key` header — no gateway-issued
  credential needed. The guardrail engine (`src/guardrails.py`) runs on the
  request before it is forwarded and on the response before it is returned
  (see Guardrails below).
- **Admin policy API** (`/admin/policy`) — GET/PUT the single global guardrail
  policy that drives the engine.
- **Audit API** (`/audit/logs`) — every call_llm request/response is recorded
  for later review, including the caller's IP, which `provider` served it, and
  the guardrail decision + findings for both the request and the response.

## Guardrails (interceptor + processor)

Every call runs through the policy-driven guardrail engine:

- **Interceptor** (request) — scans the prompt and applies the policy per PII
  type: `allow`, `mask`, `redact`, or `block`. Redaction/masking happens in
  place before the request is forwarded; a `block` (or a denylist hit) returns
  `403 guardrail_blocked` and the request never reaches the provider.
- **Processor** (response) — the same engine runs on the model's output; a
  block-action finding withholds the content behind a policy notice.

Detectors (`src/detectors.py`) cover email, phone, credit card (Luhn-checked),
US SSN, CNIC, IP, and API keys/secrets — a regex baseline behind a `detect()`
contract that a heavier engine (e.g. Presidio) can replace.

Policy (`src/policy.py`) is stored in `db_json/guardrail_policy.json`, seeded
with a sensible default (email/phone → redact, card/SSN/CNIC/secret → block,
IP → mask), and editable via `/admin/policy`. Audit findings record only
type/action/counts — never raw PII — and detected PII is redacted in the
stored payload even when a request is blocked.

### External audit sink (optional)

Set `AUDIT_SINK_URL` in `.env` to forward every audit record to a SIEM /
webhook. Delivery is fire-and-forget on a background thread with bounded retry
(`src/sink.py`), so a slow or down sink never blocks the LLM call. Leave the URL
blank to disable.

## Auth & rate limiting

- **Admin auth** — `/admin/*` and `/audit/*` are guarded by `require_admin`
  (`src/security.py`). Set `ADMIN_API_KEY` in `.env`; callers must send it as an
  `X-Admin-Key` header. If `ADMIN_API_KEY` is unset the routes stay **open**
  (dev mode) and a warning is logged at startup — set it before deploying
  anywhere shared. `/v1/call_llm` and `/v1/responses` need no gateway
  credential at all.
- **Rate limiting** — `/v1/call_llm` and `/v1/responses` are limited per
  client IP to `RATE_LIMIT_PER_MINUTE` requests (sliding window,
  `src/ratelimit.py`). Exceeding it returns `429 rate_limit_exceeded` with a
  `Retry-After` header. Set `0` to disable.

## Tests

Unit + API tests (no network — providers are faked, storage is a temp dir):

```bash
cd backend
venv\Scripts\python.exe -m pytest -q
```

Covers detectors (Luhn, overlap priority), the guardrail engine
(redact/mask/block on request + response), provider routing (body param +
unknown provider), admin auth, the policy API, and rate limiting. The
`tests/test_*_developer_*.py` scripts are the older manual, network-hitting
scenarios (run individually with the server up).

## Providers (multi-provider routing)

The gateway routes to a provider chosen **per request** via the required
`provider` field in the JSON body (`"openai"`, `"anthropic"`, or `"qwen"`).
The `provider` field is stripped before the request is forwarded upstream.

Each provider is an adapter under `src/providers/` implementing a uniform
`call(body) -> openai_shaped_response`:

- **openai** — straight pass-through (the gateway's wire format is OpenAI's).
- **anthropic** — translates the OpenAI request to the Anthropic Messages API
  (system prompt, tools, tool_choice) and maps the response back to OpenAI's
  `chat.completion` shape, so callers stay drop-in.
- **qwen** — Alibaba Qwen via DashScope's OpenAI-compatible endpoint (reuses
  the OpenAI adapter with a different `base_url`).

**The gateway holds no provider API keys.** Each caller sends their own key
for whichever provider they're calling in the `X-Provider-Key` header; it's
passed straight through to build that request's provider client and is never
stored (`get_provider(name, api_key)` in `src/providers/__init__.py` builds a
fresh adapter per call — there's no server-side singleton with a baked-in
key). A request with no key for the chosen provider returns a clear `400`
(`provider_key_missing`); an unknown or missing provider returns `400`
(`provider_not_supported`).

## Storage

Everything lives as JSON under `db_json/` (no database needed for now):

- `guardrail_policy.json` — the single global guardrail policy
- `audit_logs.json` — every call_llm/responses request/response pair

## Setup (already done on this machine)

```bash
cd backend
python -m venv venv
venv\Scripts\pip install -r requirements.txt      # PowerShell/cmd
# or: ./venv/Scripts/pip.exe install -r requirements.txt from git-bash
```

## Run

```bash
cd backend
venv\Scripts\python.exe -m uvicorn src.main:app --reload --port 8000
```

- Swagger UI: http://localhost:8000/docs
- Health check: http://localhost:8000/health

## Manual test scripts

Three independent scripts, each making a different kind of call directly
against the gateway (provider + model + your own key, no registration step)
— proving the gateway and audit trail work the same way regardless of what's
sent. Run them in any order, or all three, with the server already up and
your own `OPENAI_API_KEY` set in the environment (the gateway has no key of
its own — it forwards yours from the `X-Provider-Key` header):

```bash
venv\Scripts\python.exe tests/test_1_developer_alice.py   # simple single-turn chat
venv\Scripts\python.exe tests/test_2_developer_bob.py     # multi-turn + system prompt
venv\Scripts\python.exe tests/test_3_developer_carol.py   # tool/function calling
```

Each script prints the raw call_llm response, then re-fetches
`/audit/logs?provider=openai` to confirm that exact request and response were
recorded in the audit trail.
