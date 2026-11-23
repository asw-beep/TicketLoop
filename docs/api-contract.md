# API contract

Every endpoint the backend will expose, with who may call it, what it takes, what it returns and how it fails. FastAPI also generates live OpenAPI docs at `/docs` from the code; this file is the plan that code must match.

## Conventions

| Topic | Rule |
| --- | --- |
| Base path | `/api/v1`. All paths below are relative to it. |
| Auth | `Authorization: Bearer <access token>`. "Public" means no token needed. |
| Roles | **Public**: anyone. **User**: any logged-in, non-suspended account. **Owner**: the user the resource belongs to. **Admin**: `role = ADMIN`. Every admin route lives under `/admin`, so one test can enumerate them all (M13). |
| Bodies | JSON, `snake_case` fields. Unknown fields are rejected (422). |
| IDs | UUID strings. |
| Money | Integer paise, field names end in `_paise`: `{"asking_price_paise": 125000}` is ₹1,250. Currency is `"INR"`. |
| Time | ISO 8601 in UTC: `"2026-12-31T18:30:00Z"`. |
| Pagination | Every list takes `limit` (default 20, max 100) and `cursor`. Responses are `{"items": [...], "next_cursor": "..." or null}`. The cursor is opaque base64; clients pass it back unchanged. As built in M4 it holds an offset; M6 switches it to the last row's sort key and id (keyset), and clients do not change. A cursor that does not decode, is longer than 200 characters, or holds an offset beyond 2³¹ − 1 is 400 `bad-cursor`; `limit` outside 1 to 100 is 422. |
| Errors | `application/problem+json` per RFC 9457: `type`, `title`, `status`, `detail`, `instance`, `request_id`, and for 422 an `errors` list. Built in `app/core/exceptions.py`. |
| Request ID | Every response has `X-Request-ID`. A client may send its own (up to 64 chars of `A-Z a-z 0-9 . _ -`). |
| Idempotency | `POST /orders` and `POST /payments/intents` require an `Idempotency-Key` header (a client-generated UUID). See below. |
| Not yours | A resource that exists but is not yours returns **404**, not 403, so nobody can probe which IDs exist. 403 means "I know who you are and this action is not allowed for you" (wrong role, own listing, suspended). |

### Status codes used everywhere

| Code | Meaning here |
| --- | --- |
| 200 | OK, body returned |
| 201 | Created, body is the new resource |
| 202 | Accepted, work continues in the background |
| 204 | Done, no body |
| 400 | Malformed request the schema cannot express (missing `Idempotency-Key`, bad cursor) |
| 401 | No token, bad token, expired token, or a webhook with a bad signature |
| 403 | Authenticated but not allowed |
| 404 | Does not exist, or not yours |
| 409 | Valid request that conflicts with current state: illegal transition, already reserved, duplicate |
| 413, 415 | Upload too large, wrong file type |
| 422 | Body or query failed validation, or an `Idempotency-Key` reused with a different body |
| 429 | Rate limit hit (M14); `Retry-After` header says when to try again |
| 503 | A dependency is down (health check) |

## Health (M1, built)

| Method and path | Who | Request | Success | Errors |
| --- | --- | --- | --- | --- |
| `GET /health` | Public | — | 200 `{"status": "ok", "checks": {"database": "ok"}}` | 503 same shape with `"unavailable"` |

## Authentication (M3, built)

| Method and path | Who | Request | Success | Errors |
| --- | --- | --- | --- | --- |
| `POST /auth/register` | Public | `{email, password, full_name}`; password 12 to 128 chars | 201 `User` | 409 email taken; 422 |
| `POST /auth/login` | Public | `{email, password}` | 200 `AccessToken`; sets the refresh cookie | 401 wrong email or password (same message for both); 403 suspended; 429 |
| `POST /auth/refresh` | Refresh cookie | no body | 200 new `AccessToken`; the cookie rotates and the old token is revoked | 401 missing, invalid, expired or revoked. A revoked token revokes its whole family. |
| `POST /auth/logout` | Refresh cookie | no body | 204; the cookie's session revoked and the cookie cleared | none; with no cookie or an unknown one there is simply nothing to end |
| `POST /auth/password-reset/request` | Public | `{email}` | 202, identical whether or not the email exists | 429 |
| `POST /auth/password-reset/confirm` | Public | `{token, new_password}` | 204; every refresh token for the user revoked | 400 invalid or used token; 422 |

`AccessToken` = `{access_token, token_type: "bearer", expires_in: 900}`. The refresh token never appears in a body: it travels only in the `refresh_token` cookie (`HttpOnly`, `SameSite=Strict`, `Path=/api/v1/auth`, `Max-Age` 7 days, `Secure` in staging and production). It is opaque (43 random URL-safe characters), not a JWT. Page scripts cannot read the cookie, and other sites cannot make the browser send it.

As built: logout ends the whole session, revoking every live token in the presented token's family. The reset request answers 202 `{"message": ...}` with the same body for every email; the email carries a link to the frontend's `/reset-password#token=...`; after `#`, so the token never reaches a server log or a `Referer` header. Locally it goes to Mailpit; in tests, to an in-memory list. Changing the password also clears the refresh cookie. Every 401 carries `WWW-Authenticate: Bearer`. Problem types: `email-taken`, `invalid-credentials`, `account-suspended`, `invalid-refresh-token`, `wrong-password`, `invalid-reset-token`, `not-authenticated`, `admin-required`. The 429s arrive with rate limits in M14.
`User` = `{id, email, full_name, role, account_status, created_at}`. Never `password_hash`.

## Users (M3 built; history in M13)

| Method and path | Who | Request | Success | Errors |
| --- | --- | --- | --- | --- |
| `GET /users/me` | User | — | 200 `User` | 401 |
| `PATCH /users/me` | User | `{full_name}` | 200 `User` | 422 |
| `POST /users/me/password` | User | `{current_password, new_password}` | 204; all refresh tokens revoked | 401 wrong current password |
| `GET /users/me/history` | User | `cursor`, `limit` | 200 page of `{kind: order, payment or transfer, id, status, amount_paise, at}` | — |

## Events (M4 built; filters in M6)

| Method and path | Who | Request | Success | Errors |
| --- | --- | --- | --- | --- |
| `GET /events` | Public | `cursor`, `limit` in M4; `q`, `category`, `city`, `starts_after`, `starts_before` arrive in M6 | 200 page of `Event` (`PUBLISHED` only), soonest first | 400 bad cursor |
| `GET /events/{event_id}` | Public | — | 200 `Event` | 404 (also for unpublished) |
| `GET /events/{event_id}/listings` | Public | same filters and sort as `GET /listings` | 200 page of `PublicListing` | 404 |
| `GET /admin/events` | Admin | `cursor`, `limit` | 200 page of `Event`, every status | 403 |
| `POST /admin/events` | Admin | `{name, description, category, venue, city, starts_at, organizer, provider_id}`; `starts_at` must carry a time zone | 201 `Event` (`DRAFT`) | 403; 404 provider; 422 `starts_at` in the past or without a zone |
| `PATCH /admin/events/{event_id}` | Admin | any of the above, plus `status` (validated by the event state machine); an explicit `null` is 422 | 200 `Event` | 404; 409 illegal status change, event `CANCELLED` or `COMPLETED`, or provider changed after `DRAFT`; 422 a changed `starts_at` in the past |
| `GET /admin/providers` / `POST /admin/providers` | Admin | `{code, name}`; `code` is 2 to 50 of `a-z 0-9 -` | 200 list / 201 provider | 409 code taken |

`Event` = `{id, name, description, category, venue, city, starts_at, organizer, provider_id, status}`.

As built: status moves `DRAFT → PUBLISHED` (only while `starts_at` is in the future), `DRAFT → CANCELLED`, `PUBLISHED → CANCELLED` and `PUBLISHED → COMPLETED` (only once `starts_at` has passed). Every change is one conditional `UPDATE ... WHERE status = <the status read>`, so two admins racing get one success and one 409. Since M5, cancelling an event also cancels its open listings in the same transaction, moving `starts_at` earlier pulls later listing expiries back to it and moving it later carries listings that ended at the old start forward, and every create and change writes one audit row (`event.created`, `event.published`, `event.cancelled`, `event.completed`, or `event.updated` with the changed field names); a `PATCH` that changes nothing writes nothing. Text fields are trimmed, and blank after trimming is 422. The migration seeds one provider, `fakeprovider` (FakeProvider), so events can be created on a fresh database. Problem types: `event-not-found`, `provider-not-found`, `event-in-past`, `illegal-status-change`, `event-not-editable`, `provider-code-taken`.

## Tickets (M4, built)

| Method and path | Who | Request | Success | Errors |
| --- | --- | --- | --- | --- |
| `POST /tickets` | User | `{event_id, ticket_category, seat_details, original_price_paise, purchase_reference, provider_reference}`; price 1 to 10,000,000 paise | 201 `Ticket`, owner from the token, `verification_status: PENDING` | 404 event (also a `DRAFT` one); 409 provider reference already registered for that event, or event not `PUBLISHED`; 422 event already started |
| `GET /tickets/me` | User | `cursor`, `limit` | 200 page of `Ticket` | — |
| `GET /tickets/{ticket_id}` | Owner, Admin | — | 200 `Ticket` | 404 |
| `POST /tickets/{ticket_id}/documents` | Owner | `multipart/form-data`, field `file`: PDF, PNG or JPEG, up to 5 MiB | 201 `Document` | 404; 403 an admin (only the owner uploads); 409 ticket not `PENDING`, or already 5 documents; 413; 415; 422 empty file; 503 storage down |
| `GET /tickets/{ticket_id}/documents` | Owner, Admin | — | 200 list of `Document`, oldest first | 404 |
| `GET /tickets/{ticket_id}/documents/{document_id}` | Owner, Admin | — | 200 `{url, expires_at}`: a presigned URL valid for 5 minutes | 404 (also a document of another ticket) |
| `GET /admin/tickets/pending` | Admin | `cursor`, `limit` | 200 page of `Ticket` | — |
| `POST /admin/tickets/{ticket_id}/verify` | Admin, not the owner | `{decision: VERIFIED or REJECTED, transfer_eligible, note}`; `note` (up to 500 chars) required for `REJECTED`; `transfer_eligible` defaults to false | 200 `Ticket` | 404; 403 `own-ticket-review` (M5); 409 not `PENDING`; 422 |

`Ticket` = `{id, event_id, event: {id, name, venue, city, starts_at}, ticket_category, seat_details, original_price_paise, purchase_reference, provider_reference, verification_status, verification_note, transfer_eligible, status, current_owner_id, created_at}`. `current_owner_id` arrived in M5, so a page can tell the owner from an admin. In M4 only the owner and admins can read a ticket, so the references are always theirs to see; M5's public listing shape leaves them out.

`Document` = `{id, content_type, size_bytes, created_at}`. The file's name is never stored or returned.

As built: every route that takes a ticket ID goes through one check, so a ticket that is not yours gets exactly the 404 a random ID gets (`tests/integration/test_ticket_access.py` proves it route by route, and fails if a new ticket route is added without being listed). The file type comes from the first bytes (`%PDF-`, the PNG signature, `FF D8 FF`), not from the name or the browser's `Content-Type`. Files go to S3-compatible storage (MinIO locally) under a random key, `ticket-documents/<32 hex>`. A request whose `Content-Length` is over 6 MiB is refused with 413 before the body is read; a body sent without one (chunked) is counted as it arrives and cut off at 6 MiB with the same 413, before any form parsing or token check finishes. The verify call is one conditional `UPDATE ... WHERE verification_status = 'PENDING'`, so only the first decision lands. Since M5 every decision writes an audit row (`ticket.verified` or `ticket.rejected`), and an admin cannot verify a ticket they own. Problem types: `ticket-not-found`, `ticket-already-registered`, `event-not-open`, `event-started`, `document-not-found`, `owner-only`, `ticket-not-pending`, `too-many-documents`, `file-too-large`, `unsupported-file-type`, `empty-file`, `storage-unavailable`, `own-ticket-review`, and `bad-cursor` on any list.

## Listings (M5 built; browsing in M6)

| Method and path | Who | Request | Success | Errors |
| --- | --- | --- | --- | --- |
| `POST /listings` | Ticket owner | `{ticket_id, asking_price_paise, expires_at}`; `expires_at` is optional and defaults to the event start | 201 `Listing` (`DRAFT`) | 404 ticket not yours; 403 `owner-only` for an admin listing someone else's ticket; 409 ticket not verified, not eligible or not active, event not `PUBLISHED`, or ticket already has an open listing; 422 price below ₹1 or above face value, expiry in the past or after the event start, event already started |
| `GET /listings` | Public | `q` (event name), `category`, `city`, `ticket_category`, `min_price_paise`, `max_price_paise`, `event_id`, `sort` (`price`, `-price`, `created_at`, `-created_at`), `cursor`, `limit` | 200 page of `PublicListing` (`ACTIVE` only) | 400 bad cursor; 422. **M6.** |
| `GET /listings/me` | User | `status`, `ticket_id`, `cursor`, `limit` | 200 page of `Listing`, newest first | 400 bad cursor; 422 |
| `GET /listings/{listing_id}` | Public for `ACTIVE`; seller and admins for any status. Signing in is optional. | — | 200 `PublicListing`, or `Listing` for the seller and admins | 404, also for a listing that exists but is not `ACTIVE` and not yours |
| `PATCH /listings/{listing_id}` | Seller | `{asking_price_paise, expires_at}`, either or both; an empty body or an explicit `null` is 422 | 200 `Listing` | 404; 403 `seller-only` for an admin; 409 not `DRAFT`, `PENDING_VERIFICATION` or `ACTIVE`, a price increase while `ACTIVE`, or the listing changed while you were editing it; 422 |
| `POST /listings/{listing_id}/submit` | Seller | — | 200 `Listing` (`PENDING_VERIFICATION`) | 404; 403 `seller-only`; 409 not `DRAFT`, or the ticket or event no longer listable; 422 expiry already passed |
| `DELETE /listings/{listing_id}` | Seller; Admin for `ACTIVE` only | — | 200 `Listing` (`CANCELLED`). A soft cancel; the row stays. | 404; 409 the seller's listing is `RESERVED` or closed, or an admin's target is not `ACTIVE` (a pending one is rejected, not cancelled) |
| `GET /listings/{listing_id}/history` | Seller, Admin | `cursor`, `limit` | 200 page of `HistoryEntry`, oldest first | 404, also for a buyer looking at an `ACTIVE` listing |
| `GET /admin/listings/pending` | Admin | `cursor`, `limit` | 200 page of `Listing`, oldest first | — |
| `GET /admin/listings` | Admin | `status`, `ticket_id`, `cursor`, `limit` | 200 page of `Listing`, every status, newest first | — |
| `POST /admin/listings/{listing_id}/approve` | Admin, not the seller | — | 200 `Listing` (`ACTIVE`) | 404; 403 `own-listing-review`; 409 not `PENDING_VERIFICATION`, or `listing-not-approvable` (the ticket, event or expiry no longer allows it: reject it instead) |
| `POST /admin/listings/{listing_id}/reject` | Admin, not the seller | `{reason}`, 1 to 500 characters after trimming | 200 `Listing` (`REJECTED`) | 404; 403 `own-listing-review`; 409 not `PENDING_VERIFICATION`; 422 missing or blank reason |

`PublicListing` = `{id, event: {id, name, category, city, venue, starts_at}, ticket_category, seat_details, asking_price_paise, original_price_paise, status, created_at}`. No seller, no documents, no provider or order reference.
`Listing` = `PublicListing` plus `{ticket_id, seller_id, expires_at, reviewed_at, rejection_reason, updated_at}`. A client tells the two shapes apart by `ticket_id`.
`HistoryEntry` = `{action, from_status, to_status, actor_role: seller, admin or system, details, created_at}`. The actor is a role, never an identity: a seller never learns which admin reviewed the listing. `details` carries only `reason`, the old and new price, the old and new expiry, and the starting price and expiry; anything else in the audit row stays in the log.

As built: one function, `get_listing_for()`, decides who sees a listing, and every route that takes a listing ID goes through it (`tests/integration/test_listing_access.py` checks each route, and fails if a new one is added without being listed). An admin reaching a seller-only action gets 403 `seller-only`, because they can already see the listing; anyone else gets the 404. Every status change goes through `transition()` in `app/modules/listings/state.py`: an `ALLOWED` table, then one conditional `UPDATE ... WHERE id = :id AND status = :seen`, then an audit row in the same transaction. Two admins approving at once get one 200 and one 409 `listing-changed`. A `PATCH` also pins the price and expiry it was checked against, so two price cuts racing on an `ACTIVE` listing cannot end with the higher one written last. Submit and approve re-check the ticket and the event, since either may have changed since the draft. Cancelling an event cancels its `DRAFT`, `PENDING_VERIFICATION` and `ACTIVE` listings in the same transaction (`reason: event_cancelled`); moving its start earlier pulls any later listing expiry back to the new start, and putting it back carries listings that ended at the old start (the default) forward to the new one. `ACTIVE` listings past their expiry become `EXPIRED` when `scripts/expire_listings.py` runs; a pending listing never expires, because nobody can buy it. Problem types: `listing-not-found`, `illegal-listing-transition`, `listing-changed`, `price-out-of-range`, `price-increase-not-allowed`, `bad-expiry`, `ticket-not-listable`, `listing-already-open`, `listing-not-editable`, `listing-not-draft`, `listing-not-cancellable`, `listing-not-pending`, `listing-not-approvable`, `seller-only`, `own-listing-review`, plus `owner-only`, `event-not-open` and `event-started` from M4.

## Interests and allocation (M7)

| Method and path | Who | Request | Success | Errors |
| --- | --- | --- | --- | --- |
| `POST /listings/{listing_id}/interests` | User, not the seller | — | 201 `Interest` (`ACTIVE`) | 403 own listing; 404; 409 listing not `ACTIVE` or interest already live |
| `GET /listings/{listing_id}/interests` | Seller, Admin | `status`, `cursor`, `limit` | 200 page of `{id, buyer_display_name, status, created_at}` | 404 for everyone else, buyers included |
| `GET /interests/me` | User | `status`, `cursor`, `limit` | 200 page of `Interest` | — |
| `DELETE /interests/{interest_id}` | Owner | — | 200 `Interest` (`WITHDRAWN`) | 404; 409 not `ACTIVE` |
| `POST /listings/{listing_id}/allocations` | Seller | `{strategy: FCFS, RANDOM or SELLER_SELECTED, interest_id}`; `interest_id` only for `SELLER_SELECTED` | 201 `{interest_id, reservation: Reservation}` | 404; 409 listing not `ACTIVE`, no active interests, or chosen interest not `ACTIVE` |

## Reservations (M8)

| Method and path | Who | Request | Success | Errors |
| --- | --- | --- | --- | --- |
| `POST /listings/{listing_id}/reservations` | User, not the seller | — | 201 `Reservation` (`CREATED`, `expires_at` in 10 min). If this buyer already holds the live reservation, 200 with it. | 403 own listing; 404; 409 not available (`detail` says reserved, sold or expired) |
| `GET /reservations/{reservation_id}` | Buyer, Seller, Admin | — | 200 `Reservation` | 404 |
| `POST /reservations/{reservation_id}/cancel` | Buyer | — | 200 `Reservation` (`CANCELLED`) | 404; 409 not `CREATED` |

`Reservation` = `{id, listing_id, status, expires_at, created_at}`.

## Orders (M9)

| Method and path | Who | Request | Success | Errors |
| --- | --- | --- | --- | --- |
| `POST /orders` | User | header `Idempotency-Key`; `{reservation_id}` | 201 `Order` (`CREATED`); a replay returns the stored status and body with header `Idempotent-Replayed: true` | 400 header missing; 404 reservation not yours; 409 reservation not `CREATED` or expired, or an order already exists for it; 422 key reused with a different body |
| `GET /orders/{order_id}` | Buyer, Seller, Admin | — | 200 `Order` | 404 |
| `GET /orders/me` | User | `role` (`buyer` or `seller`), `status`, `cursor`, `limit` | 200 page of `Order` | — |
| `POST /admin/orders/{order_id}/refund` | Admin | `{note}` | 202 `Refund` (`REQUESTED`) | 404; 409 not `PAID` or `COMPLETED` |

`Order` = `{id, listing_id, reservation_id, amount_paise, currency, status, created_at, updated_at}`. There is no `amount` in the request: the server copies it from the listing.

**Idempotency-Key, step by step.** The server stores one row per `(user, key)` with a hash of the request.

1. First request with key K: do the work, store status and body under K, return 201.
2. Same K, same body again (a client retry): return the stored response. No second order.
3. Same K, different body: 422. The client has a bug.
4. Same K while the first is still running: the second request waits for the first transaction, then gets the stored response.
5. Keys expire after 24 hours.

## Payments (M10)

| Method and path | Who | Request | Success | Errors |
| --- | --- | --- | --- | --- |
| `POST /payments/intents` | Order's buyer | header `Idempotency-Key`; `{order_id}` | 201 `{payment_id, provider, provider_transaction_id, checkout_url, amount_paise, currency, status}` | 404; 409 order not `CREATED`, reservation expired, or a key still in progress; 422 key reused with a different body; 502 gateway refused |
| `POST /payments/webhooks/{provider}` | The gateway only, authenticated by signature | headers `X-Fakepay-Signature: <hex HMAC-SHA256>`, `X-Fakepay-Timestamp: <unix seconds>`; raw JSON body `{event_id, type, transaction_id, amount_paise, currency, occurred_at}` | 200 `{"received": true}` for new **and** duplicate events | 401 bad signature or timestamp older than 5 minutes; 404 unknown provider; 400 unreadable body |
| `GET /payments/{payment_id}` | Order's buyer, Admin | — | 200 `Payment` | 404 |
| `GET /admin/reconciliation-runs` | Admin | `cursor`, `limit` | 200 page of runs | — |
| `POST /admin/reconciliation-runs` | Admin | — | 202 `{run_id}`; also runs on a schedule | 409 a run is already in progress |

`Payment` = `{id, order_id, provider, status, amount_paise, currency, failure_reason, created_at, updated_at}`.

**Webhook signature.** `signature = hex(HMAC_SHA256(secret, timestamp + "." + raw_body_bytes))`. The server computes it over the exact bytes received, before parsing JSON, and compares with `hmac.compare_digest`. A duplicate gets 200 because a non-2xx tells the gateway to keep retrying something we already have.

`fakepay` itself is a separate small service with its own API (create intent, test checkout with a chosen outcome, lookup, refund), specified in M10.

## Transfers (M12)

| Method and path | Who | Request | Success | Errors |
| --- | --- | --- | --- | --- |
| `GET /transfers/{transfer_id}` | Buyer, Seller, Admin | — | 200 `Transfer` | 404 |
| `POST /transfers/callbacks/{provider}` | The ticket provider only, signed like payment webhooks | `{transfer_reference, transfer_id, result: COMPLETED or FAILED, error}` | 200 for new and duplicate callbacks | 401; 404 |
| `POST /admin/transfers/{transfer_id}/retry` | Admin | — | 202 `Transfer` (`PROCESSING`) | 404; 409 not `REQUIRES_MANUAL_REVIEW` |
| `POST /admin/transfers/{transfer_id}/abandon` | Admin | `{note}` | 200 `Transfer` (`FAILED`); a refund starts | 404; 409 not `REQUIRES_MANUAL_REVIEW` |

`plan.md` puts retry at `POST /transfers/{id}/retry`. It moved under `/admin` because it is admin-only, and every admin route lives there.

`Transfer` = `{id, order_id, status, attempt_count, last_error (admin only), initiated_at, completed_at}`.

## Notifications (M12)

| Method and path | Who | Request | Success | Errors |
| --- | --- | --- | --- | --- |
| `GET /notifications` | User | `unread_only`, `cursor`, `limit` | 200 page of `{id, notification_type, payload, read_at, created_at}` | — |
| `PATCH /notifications/{notification_id}/read` | Owner | — | 200 the notification with `read_at` set; repeating it is harmless | 404 |

## Disputes (M13)

| Method and path | Who | Request | Success | Errors |
| --- | --- | --- | --- | --- |
| `POST /orders/{order_id}/disputes` | Order's buyer or seller | `{reason, description}` | 201 `Dispute` (`OPEN`) | 404; 409 an open dispute exists or the order is not `PAID`, `TRANSFER_PENDING`, `TRANSFER_FAILED` or `COMPLETED` |
| `POST /disputes/{dispute_id}/evidence` | Participant | `multipart/form-data` `file` | 201 `{id, content_type, size_bytes}` | 404; 409 dispute closed; 413; 415 |
| `GET /disputes/me` | User | `status`, `cursor`, `limit` | 200 page of `Dispute` | — |
| `GET /disputes/{dispute_id}` | Participant, Admin | — | 200 `Dispute` with `history` (its `dispute_events`) | 404 |
| `GET /admin/disputes` | Admin | `status`, `cursor`, `limit` | 200 page of `Dispute` | — |
| `POST /admin/disputes/{dispute_id}/resolve` | Admin | `{resolution: REFUND_BUYER, RELEASE_TO_SELLER or REJECT, note}` | 200 `Dispute` | 404; 409 already resolved |

## Admin: users and audit (M13)

| Method and path | Who | Request | Success | Errors |
| --- | --- | --- | --- | --- |
| `GET /admin/users` | Admin | `q`, `account_status`, `cursor`, `limit` | 200 page of `User` | — |
| `POST /admin/users/{user_id}/suspend` | Admin | `{reason}` | 200 `User` (`SUSPENDED`); refresh tokens revoked; open listings cancelled | 404; 409 already suspended or target is an admin |
| `POST /admin/users/{user_id}/reactivate` | Admin | — | 200 `User` (`ACTIVE`) | 404; 409 |
| `GET /admin/audit-logs` | Admin | `entity_type`, `entity_id`, `actor_id`, `cursor`, `limit` | 200 page of audit rows | — |

## Endpoints added to `plan.md` section 10

`plan.md` lists the core paths; these were added because the features need them: `/health`, password reset and change, `/users/me/history`, all `/admin/events` (including `GET /admin/events`, which lists every status), `/admin/providers` and `/admin/tickets` routes, ticket documents (upload, list and link), `/listings/me` (with its `ticket_id` filter), `/listings/{id}/submit`, `/listings/{id}/history`, `GET /admin/listings` (every status, with `status` and `ticket_id` filters), `/interests/me`, `/listings/{id}/allocations`, `/admin/orders/{id}/refund`, reconciliation runs, transfer callbacks and abandon, all dispute routes, and admin user and audit routes.
