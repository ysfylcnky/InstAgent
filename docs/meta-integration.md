# Meta integration & compliance

**Responsibility:** the external Meta/Instagram surface and everything Meta App
Review requires — webhook signature verification, sending messages, OAuth
"connect your Instagram" flow, the mandatory data-deletion / deauthorize
callbacks, the 24-hour messaging window, and how tenant secrets are encrypted.

**Source of truth:** `Services/meta_verify.py`, `meta_oauth_service.py`,
`gdpr_service.py`, `instagram_service.py`, `media_service.py`,
`crypto_service.py`, plus the Meta routes in `main.py`. Tests:
`tests/test_webhook_signature.py`, `test_webhook_routing.py`,
`test_oauth_state.py`, `test_data_deletion.py`, `test_deauthorize.py`,
`test_settings_secrets.py`.

> Tenant routing after verification is in [multi-tenancy](./multi-tenancy.md);
> what happens to a verified message is in [ai-and-commerce](./ai-and-commerce.md).

---

## Meta-facing endpoints (all in `main.py`)

| Route | Method | Purpose | Verification |
|---|---|---|---|
| `/webhook` | GET | Meta subscription handshake — echoes `hub.challenge` if `hub.verify_token == VERIFY_TOKEN` | token match |
| `/webhook` | POST | Inbound DM events | `X-Hub-Signature-256` HMAC (fail-closed) |
| `/connect/instagram/callback` | GET | OAuth redirect target after the merchant authorizes; **redirects** back to the setup page (`?connected=1` / `?connect_error=…`) | single-use `state` |
| `/admin/connect/instagram` | GET | Starts the connect flow (builds authorize URL) | panel auth |
| `/admin/connect/instagram/refresh` | POST | Extends the tenant's long-lived IG token (~60 more days) | panel auth |
| `/data-deletion` | POST | App-Review: user requested data deletion | `signed_request` HMAC |
| `/data-deletion/status` | GET | Human-readable status page for a deletion code | — |
| `/deauthorize` | POST | App-Review: user removed the app | `signed_request` HMAC |

These three verified endpoints (`/webhook`, `/data-deletion`, `/deauthorize`)
are **App-Review requirements**. Do not weaken their verification or change their
response contract without understanding the review implications.

---

## Signature verification

`Services/meta_verify.py` proves a payload really came from Meta, using
`META_APP_SECRET`:

- `verify_webhook_signature(raw_body, header, secret)` — HMAC-SHA256 over the
  **raw** body, compared to `X-Hub-Signature-256`. Uses constant-time compare.
  In `main.py`, a missing signature/secret is treated fail-closed (rejected),
  except an explicit dev bypass when `META_APP_SECRET` is unset (logged loudly).
- `parse_signed_request(signed_request, secret)` — decodes and HMAC-verifies the
  base64url `signed_request` Meta posts to data-deletion/deauthorize; returns the
  payload (contains the user id) only if the signature is valid.

Because the HMAC is over the raw bytes, the webhook handler reads
`await request.body()` **before** JSON parsing — don't reorder that.

---

## Connect flow (OAuth)

One-click **"Instagram'ı Bağla"** in the setup wizard links the merchant's
Instagram Business account via **Instagram Business Login** (`graph.instagram.com`
path — *not* the Facebook-Page path). Platform app credentials
(`IG_APP_ID/IG_APP_SECRET/IG_REDIRECT_URI`, each falling back to the matching
`META_APP_*`) live in `.env`; per-tenant token + account id + username land in the
`settings` table (encrypted where secret).

```mermaid
sequenceDiagram
    participant U as Merchant (panel)
    participant App as main.py
    participant OS as meta_oauth_service
    participant IG as Instagram/Graph

    U->>App: GET /admin/connect/instagram
    App->>OS: build_authorize_url(tenant_id, user_id)
    OS->>OS: create_state → oauth_states (single-use, TTL)
    OS-->>U: authorize_url (www.instagram.com/oauth/authorize + state)
    U->>IG: authorize (scopes: instagram_business_basic, _manage_messages)
    IG-->>App: GET /connect/instagram/callback?code&state
    App->>OS: handle_callback(state, code)
    OS->>OS: consume_state (verify + delete; CSRF/tenant bind)
    OS->>IG: code → short token → long token → /me (user_id, username)
    OS->>OS: store token/id/username + IG_API_BASE in tenant settings (encrypted)
    App-->>U: redirect to setup (?connected=1)
```

`_exchange_code_for_token` orchestrates three isolated `_ig_*` Graph steps
(short-lived token → ~60-day long-lived token → `/me?fields=user_id,username`),
each monkeypatched in tests. **`user_id` from `/me` is the routing key** (equals
webhook `entry.id` / messaging `recipient.id`); the app-scoped `id` is *not* used.
`handle_callback` writes `IG_ACCOUNT_ID`, `IG_ACCESS_TOKEN` (encrypted),
`IG_USERNAME`, and pins `IG_API_BASE=graph.instagram.com` (the Instagram-Login
token is only valid against that base). `refresh_token(tenant_id)` extends the
long-lived token in place (the panel's *Token'ı Yenile* button).

`create_state` / `consume_state` back the CSRF `state` with the `oauth_states`
table (single-use, expiring, tenant/user-bound). Storing the account id also
updates `Tenant.ig_account_id`, the webhook routing key
([multi-tenancy](./multi-tenancy.md)), and rejects an account already bound to
another tenant. `handle_callback` accepts an injectable `exchange_fn` (2-tuple)
so older tests avoid real Meta calls; the real path returns a 3-tuple with
username. The **manual token-paste fields in the setup wizard remain** as a
fallback. Tests: `tests/test_instagram_signup.py` (Graph mocked).

---

## Sending & receiving media

- `Services/instagram_service.py` — `send_instagram_message(recipient_id, msg)`
  POSTs to `graph.{facebook|instagram}.com/<ver>/<IG_ACCOUNT_ID>/messages`. The
  API base and token come from the tenant's config accessors. `main.py:send_message`
  wraps it and logs the outbound message to `conversations`.
- `Services/media_service.py` — on Instagram, attachments arrive as **URLs**
  (unlike WhatsApp's media-id + token). `download_attachment(url)` fetches the
  bytes; `transcribe_audio` sends audio to OpenAI transcription. Signed CDN URLs
  can 401/403 if fetched late.

### The 24-hour window (hard platform constraint)

Instagram only allows a free-form reply **within 24 hours of the customer's last
message**. Normal bot replies are immediate, so this is fine — but there is **no
proactive-message capability** (WhatsApp's template messages have no Instagram
equivalent). Do not design features that assume the bot can message a silent
user after 24h; the API will reject it.

---

## Data deletion & deauthorize (GDPR / App Review)

`Services/gdpr_service.py` implements the two mandatory callbacks. Both are
**cross-tenant platform operations** and therefore use
`get_session(scoped=False)` (see [multi-tenancy](./multi-tenancy.md)):

- `handle_data_deletion(igsid)` / `delete_customer_data(igsid)` — remove a
  customer's rows from `conversations`, `orders`, `customers`, `usage_logs`
  everywhere the IGSID appears (it maps to `sender` / `customer_phone` / `phone`
  depending on the table). A single IGSID can exist under multiple tenants, so
  deletion must sweep all of them.
- `deauthorize_tenant(ig_account_id)` — handles a merchant removing the app:
  `tenants.status = "inactive"` + clears `IG_ACCESS_TOKEN` + invalidates the
  resolver cache.

**Reconnect path (don't break this).** Routing only resolves `status == "active"`
tenants, so `handle_callback` flips `inactive` → `active` on a successful
reconnect. Without it a merchant who removed and re-added the app would see
"connected" in the panel while every webhook was silently rejected. The flip is
deliberately narrow — only from `inactive`, so a tenant the operator suspended
for another reason cannot revive itself by reconnecting Instagram. Tests:
`tests/test_deauthorize.py::test_reconnect_after_deauthorize_reactivates_tenant`
and `::test_reconnect_does_not_revive_operator_suspended_tenant`.

`/data-deletion` returns the Meta-expected `{url, confirmation_code}` shape;
`/data-deletion/status` renders `templates/deletion_status.html`.

---

## Tenant secret encryption

`Services/crypto_service.py` — symmetric **Fernet** (AES-128-CBC + HMAC) keyed by
`ENCRYPTION_KEY` (`.env`, platform-level). `settings_service` uses it
transparently: secret keys (an allowlist — IG/İKAS tokens, etc.) are
`encrypt()`-ed into `settings.svalue` on write and `decrypt()`-ed on read;
`is_encrypted()` lets it tell ciphertext from legacy plaintext during migration.

Design rules to preserve: on a missing/broken key, crypto **raises**
(`CryptoError`) rather than silently falling back to plaintext — so a
misconfigured key fails loudly instead of leaking. Never store a tenant secret
unencrypted, and keep platform secrets out of the `settings` table entirely.
