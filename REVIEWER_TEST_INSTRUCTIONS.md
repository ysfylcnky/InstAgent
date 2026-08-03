# InstaAgent — Reviewer Test Instructions (Meta App Review)

## What the app does
InstaAgent is an AI sales assistant for Instagram-based e-commerce stores. When a
customer sends a Direct Message to a connected Instagram **Business** account, the
app automatically replies, finds the requested product in the store's catalog
(via İKAS), clarifies color/size, and creates the order on the merchant's behalf.
The assistant replies in **Turkish** (target market), so the sample responses
below are in Turkish with an English gloss.

## Permissions requested & why they are needed

| Permission | Why the app needs it |
|---|---|
| `instagram_business_basic` | Read the connected Instagram Business account's basic info (account ID and username) to identify the business and correctly route incoming messages to the right merchant (multi-tenant). |
| `instagram_business_manage_messages` | Receive incoming Instagram DMs via webhook and send automated replies on the business's behalf — this is the core function of the product (auto-reply + order taking). |

The app does **not** post content, read other users' media, or access anything
beyond the messaging conversation and the connected account's basic identity.

## Prerequisites (already configured for review)
- The app is in **Live** mode; Business Verification is complete.
- A test Instagram **professional/business** account has been added under
  **App Dashboard → Roles → Instagram Testers** and the invite accepted.
- The Instagram webhook is subscribed to the **`messages`** field, and the
  `VERIFY_TOKEN` matches the app configuration.

## Step-by-step test flow

1. **Connect the account (already done for review).** The merchant connects their
   Instagram Business account inside InstaAgent (OAuth). This exercises
   `instagram_business_basic` (reads account id + username to identify the store).

2. **Send an incoming DM.** From a second Instagram account (or the reviewer's
   own), open the connected test business account in Instagram and send a Direct
   Message, e.g.:
   > `Bu ürün stokta var mı?`  *(“Is this product in stock?”)*

3. **Observe the automated reply.** Within a few seconds the business account
   replies automatically, e.g.:
   > `Merhaba! 😊 Hangi ürünü sormuştunuz? Ürünün adını yazabilir veya gönderisini paylaşabilirsiniz.`
   *(“Hi! Which product did you mean? You can type its name or share its post.”)*
   This demonstrates `instagram_business_manage_messages` (receiving + sending).

4. **Ask about / share a product.** Type a product name (e.g. `Vintage Gömlek`)
   or share a product post/reel from the store. The assistant identifies the
   product and replies with price, available colors and sizes, e.g.:
   > `Vintage Gömlek — bej, pudra ve bebe mavi, 38–44 beden mevcut. Fiyatı 549 TL.`

5. **Provide order details.** Reply with a size/color and delivery info, e.g.:
   > `Pudra, M beden. Ayşe Demir, Beşiktaş / İstanbul, kapıda ödeme.`
   The assistant confirms the order:
   > `Siparişiniz alındı ✅ En kısa sürede hazırlanıp kargoya verilecek.`
   *(“Your order has been received ✅ …”)*

All replies in steps 3–5 are sent through the Instagram Messaging API using
`instagram_business_manage_messages`.

## Screencast
The submitted screencast shows exactly the flow above: an incoming customer DM →
the app's automated reply → product lookup → order confirmation, demonstrating how
both permissions are used in a real conversation.

## Data Deletion & Deauthorize (compliance endpoints)
- **Deauthorize:** If the merchant removes the app from their Instagram/Facebook
  settings, Meta calls `POST /deauthorize` (signed_request verified with the app
  secret); the app deactivates that store's connection and clears its stored token.
- **Data Deletion:** A user data-deletion request triggers `POST /data-deletion`
  (signed_request verified); the app deletes that Instagram user's messages,
  orders and customer records, then returns a status URL and confirmation code.
- **Privacy Policy:** `https://ig.mumifashion.com/privacy` — **Terms:** `https://ig.mumifashion.com/terms`.

## Notes for the reviewer
- The assistant responds in Turkish because the merchant base and customers are
  Turkish-speaking; the behavior (auto-reply, product lookup, order capture) is
  language-independent.
- No login is required for the reviewer to observe the messaging behavior — simply
  DM the connected test business account and watch the automated responses.
