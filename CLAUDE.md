# CLAUDE.md — Read this first, read only what it sends you to

You are working in **Mumio**. This file is the single entry point for any
Claude Code session. Do **not** scan the repository tree, and do **not** open
`Services/` file-by-file to orient yourself. Read this file, pick the task type,
open the one doc it points to, then open only the specific source files that doc
names. The docs describe **why** and **where**; the source holds the **what**.

---

## 1. What this project is

Mumio is a **multi-tenant SaaS**: an AI sales assistant for Instagram DMs,
built for the Turkish e-commerce market. A merchant (tenant) connects an
Instagram **Business** account. When a customer sends a DM:

1. Meta calls our `/webhook`.
2. We resolve which tenant owns the receiving account and isolate to it.
3. An OpenAI model (with tool-calling) chats in Turkish, searches the tenant's
   **İKAS** product catalog, clarifies colour/size, and creates the order.
4. The merchant is notified over **WhatsApp**; the order is saved to the DB.
5. A web **panel** (dashboard) shows conversations, customers, orders, AI cost.

It was adapted from an earlier WhatsApp bot; the AI/İKAS/order/session/panel
layers were copied, and only the customer channel was swapped to Instagram.

**Stack:** FastAPI + uvicorn · SQLAlchemy 2.0 ORM (MySQL in prod, SQLite in
tests) · Redis (chat sessions + message dedup) · OpenAI (chat + tools) · Jinja2
templates · JWT + bcrypt auth · Fernet (per-tenant secret encryption) · Docker.

Codebase language: **Python 3**. Comments and prompts are largely **Turkish**;
domain terms (e.g. `Faz` = phase, `urun_ara` = product search, `siparis` =
order) are kept in Turkish in both code and these docs.

---

## 2. Documentation index — pick one, then stop

| If the task is about… | Read | Then open (source) |
|---|---|---|
| Overall shape, "where does X live", request flow, adding a route | `docs/architecture.md` | the specific file that doc names |
| Tenant isolation, the DB schema, adding a tenant-owned table, an IDOR/leak concern | `docs/multi-tenancy.md` | `Services/db.py`, `Services/models.py`, `Services/tenant_service.py` |
| The bot's replies, tool-calling, product search, order state, sessions, AI cost | `docs/ai-and-commerce.md` | `main.py` (webhook flow), `Services/openai_service.py`, `ikas_service.py`, `order_service.py`, `session_store.py` |
| Instagram/Meta API, webhook signatures, OAuth connect, data-deletion/deauthorize, secret encryption | `docs/meta-integration.md` | `Services/meta_verify.py`, `meta_oauth_service.py`, `gdpr_service.py`, `instagram_service.py`, `crypto_service.py` |
| Panel pages, auth, reporting, setup wizard | `docs/architecture.md` (§Panel & Auth) | `main.py` routes, `Services/dashboard_service.py`, `auth_service.py`, `setup_service.py` |

Each doc has exactly one responsibility and cross-links the others. If two
concerns meet (e.g. "isolate the webhook by tenant"), start with the doc for the
concern that carries the risk (here: multi-tenancy).

---

## 3. Repository navigation guide

**Inspect these (real source of truth):**

| Path | What it is | Open when |
|---|---|---|
| `main.py` (~1980 lines) | FastAPI app: **all** routes + the webhook message-processing pipeline | Changing routing, the DM flow, or the app object |
| `config.py` | Env vars + tenant-aware config accessors (DB setting → `.env` → default) | Adding a setting or credential |
| `Services/` | All business logic, one concern per module | Guided by a `docs/` file — never scan blindly |
| `templates/`, `static/` | Jinja2 panel + landing (HTML/CSS/JS) | Only for panel UI changes |
| `migrations/run.py` | Additive, idempotent schema migration + backfill | Schema/DDL changes |
| `tests/` | pytest (SQLite); heavy on isolation/IDOR/webhook/OAuth/GDPR | Adding behaviour → mirror the matching test |
| `*_prompt.txt`, `siparis_ozellik_promptu.md`, `general_prompt.txt` | The AI system prompts (assembled at runtime) | Changing bot behaviour/tone |
| `.env.example` | Canonical list of every config key | Understanding required config |

**Usually ignore (noise / generated / vendored):**

| Path | Why ignore |
|---|---|
| `latest.md` (~1 MB), `repomix.md` (~0.5 MB) | Auto-generated full-codebase dumps. **Never read** — reading them defeats the purpose of this doc set. |
| `.venv/`, `__pycache__/`, `.pytest_cache/`, `*.pyc` | Virtualenv / build / cache artifacts |
| `.git/` | Version control internals |
| `static/webfonts/`, `static/css/` (vendor), `favicon.svg` | Static assets / vendored fonts |
| `generate_password_hash.py`, `model_cost_compare.py` | One-off helper scripts, not app code |

**Historical narrative docs (context, not source of truth — may be stale):**
`INSTAAGENT_SAAS_ROADMAP.md`, `INSTAAGENT_SAAS_IMPLEMENTATION.md`,
`APP_REVIEW_CHANGES.md`, `CLAUDE_CODE_APP_REVIEW_TODO.md`,
`REVIEWER_TEST_INSTRUCTIONS.md`, `gpt4o_test_senaryolari.md`, `README.md`.
These record *how the SaaS was built in phases (Faz 1–12)* and Meta App-Review
prep. Read one only when you need project history or the reasoning behind a past
decision — the `docs/` set below is the maintained reference. Do not treat these
as current unless verified against source.

---

## 4. Token-efficient workflow (follow in order)

1. **Read `CLAUDE.md`** (this file).
2. **Classify the task** using the index table in §2.
3. **Read the one matching `docs/` file.** It explains the design and names the
   exact source files involved.
4. **Open only those source files** — and only the relevant functions. Prefer
   `Grep` for a symbol over reading a whole module. `main.py` is large; jump to
   the function, don't read it top to bottom.
5. **Do not** open unrelated `Services/` modules, the generated `*.md` dumps, or
   `templates/`/`static/` unless the task is UI.
6. If the docs and the code disagree, **the code wins** — then fix the doc (§5).

---

## 5. Editing & maintenance rules for the docs

- Keep each doc to its **single responsibility** (§2). Don't duplicate content
  across docs — **cross-reference** instead (`see docs/multi-tenancy.md`).
- Document **relationships, design decisions, and locations**, not line-by-line
  behaviour a single file already makes obvious.
- When you change code that a doc describes, **update that doc in the same
  change**. A stale doc costs more tokens than no doc.
- Prefer tables, Mermaid diagrams, and file/function references over prose.
- If a new subsystem appears that fits no existing doc, prefer **extending** an
  existing doc over creating a new file. Add a new `docs/` file only when a
  genuinely new domain of comparable weight to the existing four emerges.

---

## 6. Safety rules (this codebase can leak customer data or money)

- **Tenant isolation is sacred.** Never remove or bypass the ORM tenant filter.
  `get_session(scoped=False)` is a deliberate, audited cross-tenant escape hatch
  — do not add new callers without reading `docs/multi-tenancy.md`. Any new
  tenant-owned table **must** be added to `TENANT_OWNED_MODELS` in
  `Services/models.py`, or it silently leaks across tenants.
- **Fail-closed stays closed.** Unknown Instagram accounts, missing tenant
  scope, and bad webhook signatures are rejected on purpose. Don't "fix" a
  rejection by falling back to a default tenant.
- **Secrets never touch plaintext storage.** Tenant credentials are Fernet-
  encrypted in the `settings` table; platform secrets (OpenAI key, Meta App
  secret, `ENCRYPTION_KEY`, `JWT_SECRET`) live only in `.env`. Never log,
  echo, or write a secret to `tenant_settings` in clear text.
- **Don't break Meta compliance endpoints.** `/webhook` signature verification,
  `/data-deletion`, and `/deauthorize` are App-Review requirements. Changing
  their contract can get the app rejected.
- **Migrations are additive & idempotent.** Never write a destructive migration
  in `migrations/run.py` without an explicit request and a backup path.
- This is a documentation/analysis task by default — **do not modify application
  behaviour** unless the user explicitly asks for a code change.
