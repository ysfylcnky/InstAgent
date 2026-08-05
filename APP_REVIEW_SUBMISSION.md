# InstaAgent — Meta App Review Submission Metinleri

Bu dosyadaki bloklar Meta App Dashboard'daki ilgili alanlara **birebir
kopyala-yapıştır** içindir. Türkçe açıklamalar `>` ile işaretli — panele
girilmez.

> **Panelde yol:** Products → **Instagram → API setup with Instagram login** →
> *Complete app review* bölümündeki chevron → **Continue to app review** →
> **App Review → Requests** → **Edit**.

---

## 1 · App Verification → Platform Settings

> **Alan:** "Provide detailed step-by-step instructions for Meta reviewers to log
> in and test your app." Platform: **Web** (Instagram Login yalnız web'i destekler).
> `<DEMO_KULLANICI>` / `<DEMO_SIFRE>` / `<TEST_IG_HESABI>` yerlerini doldur.

```
PLATFORM: Web

WHAT THE APP DOES
InstaAgent is a B2B SaaS tool (Tech Provider) for online retailers in Turkey.
A merchant connects their Instagram professional account to InstaAgent. When a
customer sends a Direct Message to that account, InstaAgent automatically replies
in the customer's language, finds the requested item in the merchant's product
catalog, clarifies colour and size, and records the order for the merchant.
Because the app serves many independent merchants, each merchant's data is
isolated in a separate tenant.

TEST URL
https://ig.mumifashion.com/

STEP-BY-STEP TEST INSTRUCTIONS

1. Open https://ig.mumifashion.com/login and sign in with the test credentials
   provided in the Credentials section below. You will land on the merchant
   dashboard.

2. In the left sidebar click "Kurulum" (Setup). This is the onboarding wizard
   where a merchant connects their Instagram professional account.

3. In the "Instagram" section click the button labelled
   "Connect with Instagram" (Instagram'i Bagla). This starts Business Login for
   Instagram and requests the two permissions in this submission:
   instagram_business_basic and instagram_business_manage_messages.

4. Authorize the app with an Instagram professional account. After you approve,
   you are redirected back to the Setup page and the connected account's
   username is displayed on the page. Reading that username and account ID is
   the only use of instagram_business_basic.

   NOTE: An Instagram professional account is already connected for this review
   (@<TEST_IG_HESABI>). If you prefer to observe rather than connect your own
   account, skip to step 5.

5. From any second Instagram account, open @<TEST_IG_HESABI> in Instagram and
   send a Direct Message, for example:
       "Bu urun stokta var mi?"   (Turkish for "Is this product in stock?")

6. Within a few seconds the business account replies automatically, for example:
       "Merhaba! Hangi urunu sormustunuz?"
       ("Hello! Which product did you mean?")
   This reply is delivered through the Instagram Messaging API and is the only
   use of instagram_business_manage_messages.

7. Reply with a product name, for example "Vintage Gomlek". The assistant looks
   the item up in the merchant's catalog and answers with price, available
   colours and sizes.

8. Reply with a size and colour plus a name and address, for example
       "Pudra, M beden. Ayse Demir, Besiktas / Istanbul"
   The assistant confirms the order:
       "Siparisiniz alindi" ("Your order has been received").

9. Back in the dashboard, open "Konusmalar" (Conversations) in the left sidebar.
   The same conversation you just had is listed there, which is how the merchant
   reviews what the assistant said on their behalf.

LANGUAGE NOTE
The assistant and the dashboard are in Turkish because every merchant and every
end customer is Turkish-speaking. English glosses for each on-screen label are
included as captions in the screencast. The behaviour being demonstrated
(receive DM, reply, look up product, record order) is language-independent.

WHAT THE APP DOES NOT DO
The app does not publish content, does not read comments, does not read other
users' media, and does not access anything beyond the messaging conversation and
the connected account's own ID and username.

COMPLIANCE ENDPOINTS
Deauthorize callback:   https://ig.mumifashion.com/deauthorize
Data deletion callback: https://ig.mumifashion.com/data-deletion
Privacy policy:         https://ig.mumifashion.com/privacy
Terms of service:       https://ig.mumifashion.com/terms
All three callbacks verify Meta's signed_request / X-Hub-Signature-256 with the
app secret and reject unsigned requests.
```

---

## 2 · App Verification → Credentials

> Demo panel hesabı açtıktan sonra doldur. **Gerçek müşteri verisi olan
> tenant'ını verme** — reviewer için ayrı bir demo tenant aç.

```
InstaAgent merchant dashboard (web)
URL:      https://ig.mumifashion.com/login
Username: <DEMO_KULLANICI>
Password: <DEMO_SIFRE>

This account is a demo merchant tenant created specifically for this review. An
Instagram professional account (@<TEST_IG_HESABI>) is already connected to it,
so the reviewer can either observe the existing connection or connect their own
professional account from the Setup page.

No Instagram credentials are required to observe the messaging behaviour: simply
send a Direct Message to @<TEST_IG_HESABI> from any Instagram account and the
automated reply will arrive within seconds.
```

---

## 3 · Permission: `instagram_business_basic`

> **Alan:** "How will your app use instagram_business_basic?"

```
InstaAgent is a multi-tenant SaaS product used by many independent merchants, so
it must know which Instagram professional account belongs to which merchant.

We use instagram_business_basic for exactly one thing: immediately after a
merchant completes Business Login for Instagram, we call GET /me?fields=user_id,
username once to read the connected account's Instagram user ID and username.

- The user ID is stored as that merchant's routing key. Every incoming messaging
  webhook carries this ID, and we use it to route the event to the correct
  merchant's isolated tenant. Without it we cannot tell whose account a message
  was sent to, and the app cannot function at all.
- The username is displayed in the merchant's dashboard so they can confirm they
  connected the right account.

We do not read media, insights, followers, or any other profile data, and we do
not request this permission for any other account than the one the merchant
themselves connects.

The screencast shows a merchant clicking "Connect with Instagram", authorizing,
and the connected account's username then appearing in the dashboard.
```

---

## 4 · Permission: `instagram_business_manage_messages`

> **Alan:** "How will your app use instagram_business_manage_messages?"

```
This permission is the core function of the product: InstaAgent answers the
Instagram Direct Messages that customers send to the merchant's professional
account.

Receiving: we subscribe to the "messages" webhook field. When a customer sends a
DM to a connected merchant account, Meta delivers the event to our webhook. We
verify the X-Hub-Signature-256 signature with the app secret, route the event to
the merchant's tenant, and read the message text (or transcribe a voice message)
so the assistant can understand what the customer is asking for.

Sending: we call the Instagram Messaging API to send the reply back to that same
customer, inside the standard 24-hour messaging window. The assistant answers
product questions using the merchant's own catalog, clarifies colour and size,
and confirms the order. The merchant sees every message in their dashboard.

The app only replies to conversations the customer started, never sends
unsolicited or promotional messages, and never messages anyone who has not
messaged the merchant first. Message content is used only to serve that
conversation, is never sold or used for advertising, and is deleted on request
through our data deletion callback.

Without this permission the product has no function, because receiving and
replying to customer DMs is the entire service.
```

---

## 5 · Screencast çekim listesi

> Tek video her iki izni de kapsayabilir, ama Meta izin başına ayrı video da
> kabul ediyor. **İngilizce altyazı zorunlu** (UI Türkçe olduğu için).

| # | Ekran | Altyazı (İngilizce) |
|---|---|---|
| 1 | `/login` → giriş | `Merchant signs in to the InstaAgent dashboard` |
| 2 | Sidebar → Kurulum | `"Kurulum" = Setup — the merchant onboarding wizard` |
| 3 | **Connect with Instagram** butonu (yakın plan) | `Business Login for Instagram — the login button` |
| 4 | Instagram izin ekranı | `Requesting instagram_business_basic and instagram_business_manage_messages` |
| 5 | Setup'a dönüş, kullanıcı adı görünür | `instagram_business_basic — app reads the account ID and username to identify this merchant` |
| 6 | Telefonda ikinci hesaptan DM | `A customer sends a Direct Message: "Is this product in stock?"` |
| 7 | Botun otomatik yanıtı | `instagram_business_manage_messages — the app receives the DM via webhook and sends the automated reply` |
| 8 | Ürün adı → renk/beden yanıtı | `The assistant looks the item up in the merchant's catalog` |
| 9 | Sipariş onayı | `"Siparisiniz alindi" = "Your order has been received"` |
| 10 | Sidebar → Konuşmalar | `"Konusmalar" = Conversations — the merchant reviews what the assistant said` |

**Kurallar:** Ekranda kalan her Türkçe butonun altına İngilizce açıklama koy.
Videoda gerçek hesap kullan, sahte hesap kullanma. Login butonu net görünsün —
Meta'nın action item'larından biri bu.

---

## 6 · App Settings kontrol listesi

App Review → **Review your app settings**:

- [ ] App icon — 1024×1024 PNG
- [ ] Privacy Policy URL — `https://ig.mumifashion.com/privacy`
- [ ] App Category — *Business and Pages* (veya *Messaging*)
- [ ] Business Email — `info@mumifashion.com`

Instagram → API setup with Instagram login:

- [ ] Webhook Callback URL — `https://ig.mumifashion.com/webhook`, `messages` alanı **abone**
- [ ] Verify token — `.env`'deki `VERIFY_TOKEN` ile birebir aynı
- [ ] OAuth Redirect URI — `https://ig.mumifashion.com/connect/instagram/callback`
- [ ] Deauthorize Callback URL — `https://ig.mumifashion.com/deauthorize`
- [ ] Data Deletion Callback URL — `https://ig.mumifashion.com/data-deletion`
- [ ] En az 1 başarılı API çağrısı yapılmış olmalı (Advanced Access şartı) — kendi hesabınla DM testi bunu karşılar
