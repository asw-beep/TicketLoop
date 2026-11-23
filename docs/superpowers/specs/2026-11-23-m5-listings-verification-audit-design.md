# M5 · Listings, verification and audit log: design

Date: 2026-11-23. Sources: `build-and-learn-plan.md` (M5), `plan.md` (FR-04, FR-12), `docs/requirements.md`, `docs/state-machines.md`, `docs/api-contract.md`, `docs/database-design.md`, the M4 design.

## Intent

TicketLoop is a resale marketplace, and M5 is where something first goes on sale. An owner offers a verified ticket at no more than face value, an admin checks the listing before buyers can see it, and every status change is recorded where nobody can edit it later.

It is also the project's first real state machine. The pattern learned here (one transition function, an allowed-moves table, a conditional `UPDATE ... WHERE status = :from`, and an audit row in the same transaction) is the pattern M8 uses under real concurrency for reservations, and M9 to M12 reuse for orders, payments and transfers. Getting it right in a calm setting is the point.

Why now: interests (M7) and reservations (M8) attach to an `ACTIVE` listing; discovery (M6) searches them. The audit helper starts now because every later state change must write it.

## Goals

1. A ticket's owner creates a listing (DRAFT), edits its price and expiry, submits it for review, and cancels it while that is still allowed.
2. An admin reviews pending listings and approves (ACTIVE) or rejects (REJECTED, with a reason the seller sees).
3. Every listing status change goes through one `transition()` function, backed by one `ALLOWED` table, applied with a conditional UPDATE, and writes an audit row in the same transaction.
4. An `audit.record()` helper used by listings and retrofitted onto existing admin actions (ticket verification, events, providers).
5. A listing's history (status changes and price changes) is readable by its seller and by admins.
6. Active listings past their expiry can be expired by a function and a script run by hand.
7. **Done when:** a table-driven test tries every (from, to) pair of listing statuses and only the allowed ones succeed.
8. Screens for all of it in `frontend/`, tested by hand and by Playwright in CI.

## Non-goals

- Search, filters, sorting and the public `GET /listings` and `GET /events/{id}/listings`: M6.
- Reaching `RESERVED` or `SOLD`. The transitions exist in `ALLOWED` (so the table-driven test covers the whole machine), but no M5 route reaches them; M8 and M12 do.
- Interests and their side effects on cancel or expiry ("live interests become REJECTED"): M7.
- The outbox events the state-machine doc names (`listing.approved`, `listing.rejected`, `listing.cancelled`) and seller notifications: M11 and M12.
- Scheduling listing expiry: M11. M5 ships the function and a script.
- Voiding a ticket and cancelling its open listing: M13.
- `GET /admin/audit-logs` and an admin audit screen: M13.
- Audit rows for user actions outside the listing lifecycle (registering a ticket, uploading evidence, logging in).
- Rate limits on listing creation: M14.

## Anti-goals (a failure even if everything "works")

- A listing status that changes without going through `transition()`, or without an audit row.
- An audit row that survives the rollback of the change it describes, or a change that commits without its audit row.
- A check-then-write race: reading the status in Python, deciding, then writing an UPDATE that does not re-check the status.
- A public response that leaks private data: the seller's identity, purchase or provider references, evidence documents, or a non-ACTIVE listing's existence.
- An audit row containing secrets or private data: tokens, presigned URLs, purchase or provider references, email addresses.
- A seller able to raise the price of an ACTIVE listing, or to price above face value.

## Constraints

- Fits the shape M3 and M4 set: `app/modules/<name>/{models,schemas,service,router,errors}.py`, plain `def` routes, services that never commit (`get_db` commits once the route returns), errors as `AppError` subclasses rendered as problem+json, lists as `Page[T]` (`{items, next_cursor}`, `limit` default 20, max 100), offset inside the opaque cursor until M6.
- Contract conventions hold: `/api/v1`, admin routes under `/admin`, unknown body fields rejected (422), money in paise, "not yours" is 404, 403 only when the caller can already see the resource.
- The `listings` and `audit_logs` tables already exist (M2 migration), with `ck_listings_min_price`, `ck_listings_price_cap`, `ck_listings_rejection_reason`, `uq_listings_one_open_per_ticket` (partial, over DRAFT, PENDING_VERIFICATION, ACTIVE and RESERVED) and the `forbid_update_delete()` trigger on `audit_logs`. M5 does not change them.
- Postgres 17 (no `RETURNING OLD`).
- No AI attribution in commits or files.

## Approaches (decided)

**State machine.**
- **A (chosen):** one hand-written `transition()` and an `ALLOWED` dictionary, as the build plan prescribes. The rules are visible in one screen of code and the table-driven test reads straight off them.
- B: a state-machine library (`transitions`, `python-statemachine`). Hides exactly the mechanics M5 is meant to teach, and still needs the conditional UPDATE for correctness.
- C: a Postgres trigger that rejects illegal moves. A strong last line of defence, but it cannot see the actor or request ID without extra plumbing, and moves the rules out of the code the tests read.

**Audit writes.**
- **Explicit `record()` call (chosen)** inside the same transaction as the change. Every call site is visible and testable.
- SQLAlchemy event listeners: implicit, easy to bypass with a Core `update()`, which is exactly what conditional UPDATEs are.
- Database triggers: same actor and request-ID problem as C above.

**Listing creation.** The build plan's wording has create going straight to review; the API contract and `docs/state-machines.md` have create make a DRAFT, then `POST /listings/{id}/submit`. **The contract wins**: it is the more detailed M0 design, and `plan.md`'s lifecycle starts at DRAFT.

**Cancelling an event with open listings** (left open by M4).
- **Cancel them too (chosen):** the event cancel also moves each open listing to CANCELLED, in the same transaction, one audit row each. An event that is not happening should not stay on sale, and sellers should not be able to block an admin.
- Refuse the cancel while open listings exist: forces an admin to cancel listings one by one first, for no benefit.

## Design

### Data

One migration, indexes only (the names and shapes `docs/database-design.md` already lists, where they apply):

| Index | Serves |
| --- | --- |
| `ix_listings_review_queue (created_at, id) WHERE status = 'PENDING_VERIFICATION'` | `GET /admin/listings/pending` |
| `ix_listings_active_expiry (expires_at) WHERE status = 'ACTIVE'` | `expire_listings()` |
| `ix_listings_seller (seller_id, created_at, id)` | `GET /listings/me` |
| `ix_listings_event (event_id)` | the event-cancel cascade and the expiry clamp (Postgres does not index foreign keys) |
| `ix_audit_logs_entity (entity_type, entity_id, created_at)` | a listing's history |

The models declare the same indexes so `alembic check` stays clean. `ix_listings_active_by_created` waits for M6, which designs discovery's indexes against `EXPLAIN ANALYZE`.

### Audit helper (`app/modules/audit/service.py`)

`record(db, *, actor_id, entity_type, entity_id, action, details=None) -> AuditLog`

- Adds one `AuditLog` row to the session and flushes it, so a database error surfaces at the call site. Never commits.
- `request_id` is read from structlog's context variables (the request middleware binds it), so callers do not pass it. Outside a request (the expiry script) it is NULL.
- `details` is a small JSON-safe dict; UUIDs and datetimes are converted to strings. Never secrets or private data (see Anti-goals).
- Actions are dotted lowercase strings, `<entity>.<verb>`.

Retrofits (each in the same transaction as the change):

| Where | Action | Details |
| --- | --- | --- |
| `verify_ticket` | `ticket.verified` or `ticket.rejected` | `transfer_eligible`, `note` |
| `create_event` | `event.created` | `name`, `starts_at` |
| `update_event`, fields only | `event.updated` | names of changed fields |
| `update_event`, status change | `event.published`, `event.completed`, `event.cancelled` | `from`, `to`, and for a cancel `cancelled_listings` (count) |
| `create_provider` | `provider.created` | `code` |

An `update_event` call that changes both fields and status writes one row, with the status action. The actor is the admin.

### The listing state machine (`app/modules/listings/state.py`)

`ALLOWED: dict[ListingStatus, set[ListingStatus]]` holds the full table from `docs/state-machines.md`:

```
DRAFT                -> PENDING_VERIFICATION, CANCELLED
PENDING_VERIFICATION -> ACTIVE, REJECTED, CANCELLED
ACTIVE               -> RESERVED, CANCELLED, EXPIRED
RESERVED             -> ACTIVE, EXPIRED, SOLD, CANCELLED
SOLD, REJECTED, CANCELLED, EXPIRED -> (none)
```

`transition(db, listing, to, *, actor_id, values=None, details=None) -> Listing`

1. `seen = listing.status`. If `to` is not in `ALLOWED[seen]`, raise `IllegalListingTransitionError` (409 `illegal-listing-transition`, extension `current_status`).
2. `UPDATE listings SET status = :to, updated_at = now(), <values> WHERE id = :id AND status = :seen RETURNING id`. `values` carries the columns a move sets (`reviewed_by`, `reviewed_at`, `rejection_reason`).
3. No row returned: someone changed the listing first. Refresh it and raise `ListingChangedError` (409 `listing-changed`, extension `current_status`).
4. `audit.record(..., entity_type="listing", action=ACTION[(seen, to)], details={"from": seen, "to": to, **details})`.
5. Refresh and return the listing.

`ACTION` names the M5 moves: `listing.submitted` (DRAFT to PENDING_VERIFICATION), `listing.approved`, `listing.rejected`, `listing.cancelled` (from any state), `listing.expired`. Moves M5 does not reach get a name now (`listing.reserved`, `listing.released`, `listing.sold`) so the table-driven test can run them; later modules may rename them.

`transition()` knows nothing about who may act. Guards (ownership, roles, ticket and event checks) live in the service functions that call it. This keeps the state machine testable on its own.

### Price and expiry rules (`app/modules/listings/rules.py`, pure functions)

- `check_price(asking, original, *, current=None, status=None)`: `asking` from 100 to `original` inclusive, else `PriceOutOfRangeError` (422 `price-out-of-range`). When `status` is ACTIVE and `asking > current`, `PriceIncreaseError` (409 `price-increase-not-allowed`). Equal prices pass.
- `check_expiry(expires_at, event_starts_at, now)`: strictly after `now` and no later than `event_starts_at`, else `BadExpiryError` (422 `bad-expiry`). Naive datetimes are rejected by the schema.

The database CHECKs stay as the last line of defence; a CHECK violation that slips through is a bug and surfaces as 500.

### Listings (`app/modules/listings/`)

| Route | Who | Notes |
| --- | --- | --- |
| `POST /listings` | Ticket owner | `{ticket_id, asking_price_paise, expires_at?}`. 201 `Listing` (DRAFT). |
| `GET /listings/me` | User | Your listings, all statuses, newest first. Filters `status` and **`ticket_id` (added)**. |
| `GET /listings/{id}` | Public for ACTIVE; seller and admin for any status | `PublicListing` for the public, `Listing` for the seller and admins. |
| `PATCH /listings/{id}` | Seller | `{asking_price_paise?, expires_at?}`, at least one. |
| `POST /listings/{id}/submit` | Seller | DRAFT to PENDING_VERIFICATION. |
| `DELETE /listings/{id}` | Seller, Admin | Soft cancel to CANCELLED; 200 `Listing`. |
| `GET /listings/{id}/history` | Seller, Admin | **Added.** Page of history entries, oldest first. |
| `GET /admin/listings/pending` | Admin | PENDING_VERIFICATION, oldest first (`created_at, id`). Page of `Listing`. |
| `GET /admin/listings` | Admin | **Added.** Every listing, newest first (`created_at DESC, id DESC`), filters `status` and `ticket_id`. Page of `Listing`. Without it an admin has no way to reach an ACTIVE listing to cancel it until M6. |
| `POST /admin/listings/{id}/approve` | Admin | To ACTIVE; sets `reviewed_by`, `reviewed_at`. |
| `POST /admin/listings/{id}/reject` | Admin | `{reason}` 1 to 500 chars, trimmed. To REJECTED; sets the reason and reviewer. |

**Visibility.** One lookup, `get_listing_for(db, listing_id, user | None)`, mirrors M4's `get_visible_ticket`: the seller and admins see any status; anyone else (signed in or not) sees only ACTIVE; every other case raises `ListingNotFoundError` with one fixed detail. Every route with `{listing_id}` goes through it. Seller-only routes then raise 403 `seller-only` when the caller is an admin who is not the seller (they can see the listing, so 404 hides nothing), mirroring M4's `owner-only`. A non-seller USER on a seller-only route gets 404, even for an ACTIVE listing they can see: **my call** to keep it simple and uniform (`PATCH`, `submit` both 404 for strangers; `history` is seller-and-admin, so also 404).

**An admin who is the seller.** M4 lets an admin register and own a ticket, so an admin can be a seller. **The seller check runs first:** on their own listing an admin is treated as the seller everywhere (edit, submit, cancel by the seller's rules, audit `by: "seller"`, history `actor_role: "seller"`). Admin powers apply only to other people's listings.

**No self-review (decided by the project owner).** An admin may not approve or reject their own listing (`seller_id` is the caller): 403 `own-listing-review`. M5 adds the same block to M4's ticket verification: an admin verifying a ticket they currently own gets 403 `own-ticket-review`. Self-review is exactly what an audit log exists to catch, and blocking only one of the two would leave the other open. Both checks run before the conditional UPDATE, and neither writes an audit row.

**From-states are checked by each action, not only by `ALLOWED`.** `ALLOWED` describes the whole machine, so it permits moves an M5 route must not make: `RESERVED → ACTIVE` would let "approve" reopen a reserved listing, and `RESERVED → CANCELLED` would let "cancel" drop one. Each service function names the states it acts from (approve and reject: PENDING_VERIFICATION; submit: DRAFT; cancel: per the cancel rules; expire: ACTIVE) and refuses anything else with its own 409 before calling `transition()`. The conditional UPDATE then pins the state it checked.

**Optional authentication.** `GET /listings/{id}` needs to know the caller when there is one. A new `OptionalUser` dependency: no `Authorization` header means anonymous; a header with a bad or expired token is 401, so the client refreshes instead of silently seeing the public view.

**Shapes** (as the contract has them):
- `PublicListing` = `{id, event: {id, name, category, city, venue, starts_at}, ticket_category, seat_details, asking_price_paise, original_price_paise, status, created_at}`. `ticket_category` and `seat_details` come from the ticket.
- `Listing` = `PublicListing` plus `{ticket_id, expires_at, reviewed_at, rejection_reason, updated_at}`.
- `GET /listings/{id}` declares its response as a union; `Listing` is a superset, so the frontend checks for `ticket_id`.
- `HistoryEntry` = `{action, from_status, to_status, actor_role, details, created_at}`. `actor_role` is `seller`, `admin` or `system` (NULL actor), derived by comparing the actor with the listing's seller and their role. The admin's identity is never shown to the seller. `details` is filtered to safe keys per action: the rejection `reason`, `old_price_paise` / `new_price_paise`, `old_expires_at` / `new_expires_at`, and `reason: "event_cancelled"`.

**Create rules (`POST /listings`).**
1. Ticket lookup through `get_visible_ticket`: unknown or not yours is 404 `ticket-not-found`. An admin who is not the owner gets 403 `owner-only`.
2. The ticket must be `VERIFIED`, `transfer_eligible` and `ACTIVE`, else 409 `ticket-not-listable` with a detail naming which.
3. The event must be `PUBLISHED`, else 409 `event-not-open`; already started is 422 `event-started` (the M4 codes).
4. `check_price` against the ticket's `original_price_paise`; `expires_at` defaults to the event's `starts_at`, then `check_expiry`.
5. Insert with `seller_id` from the token, `event_id` and `original_price_paise` copied from the ticket. A second open listing is detected from `uq_listings_one_open_per_ticket` via `violated_constraint` inside a SAVEPOINT (the M4 pattern), giving 409 `listing-already-open`. No check-then-insert.
6. Audit `listing.created` with `asking_price_paise` and `expires_at`. Creation is not a transition, so it is the one listing audit row written outside `transition()`.

**Edit rules (`PATCH /listings/{id}`).**
- Allowed in DRAFT, PENDING_VERIFICATION and ACTIVE; any other status is 409 `listing-not-editable`.
- `check_price` with `current` and `status`; `check_expiry` against the event's start. ACTIVE may lower the price and move the expiry either way within the rule.
- Applied with a conditional UPDATE `WHERE id = :id AND status = :seen AND asking_price_paise = :seen_price AND expires_at = :seen_expiry`, so an edit cannot land on a listing an admin just rejected, and two edits racing on an ACTIVE listing cannot end with a price higher than the one either of them checked against. Zero rows is 409 `listing-changed`.
- Writes `listing.price_changed` and/or `listing.expiry_changed` with old and new values. **My call:** price changes are what interested buyers care about, and FR-04 rule 7 asks for "every important listing state change".
- A PATCH that changes nothing returns the listing and writes no row.

**Submit rules.** Seller only, from DRAFT. The create guards 2 and 3 are checked again (the ticket or event may have changed), and `expires_at` must still be in the future (422 `bad-expiry`).

**Cancel rules (`DELETE`).** The seller may cancel from DRAFT, PENDING_VERIFICATION and ACTIVE. An admin may cancel from ACTIVE only (a pending listing is rejected instead), else 409 `listing-not-cancellable`. RESERVED and closed listings are 409 `listing-not-cancellable` for everyone, with the current status in the detail (FR-04 rule 5). Audit details carry `by: "seller"` or `by: "admin"`.

**Approve and reject.** From PENDING_VERIFICATION only (anything else is the transition's 409). Approve re-checks create guards 2 and 3 and that `expires_at` is in the future; a failure is 409 `listing-not-approvable` with the reason, and the admin rejects instead. Reject requires the reason (422 without it); the reason goes into `rejection_reason` and the audit details.

**Concurrency.** Two admins approving or rejecting the same listing at once: exactly one succeeds, the other gets 409 `listing-changed`. A seller cancelling while an admin approves: exactly one wins. Both follow from the conditional UPDATE; tests prove them with threads, as M3's refresh-race test does.

### Event cascade (`app/modules/events/service.py`, calling into listings)

- **Cancel.** When `update_event` moves an event to CANCELLED, after its own conditional UPDATE it calls `listings.service.cancel_open_listings_for_event(db, event_id, actor_id)`. That function selects the event's DRAFT, PENDING_VERIFICATION and ACTIVE listings `FOR UPDATE` (so none can change underneath it), then calls `transition()` on each with `details={"reason": "event_cancelled"}`. It returns the count, which goes into the `event.cancelled` audit row. RESERVED listings cannot exist yet; M8 must decide what an event cancel does to them, and this function's docstring says so.
- **Start moved earlier.** When `starts_at` changes, open listings whose `expires_at` is later than the new start get `expires_at = new start` in one UPDATE. Not a status change, so no listing audit rows; the count goes into the event's audit details (`listings_expiry_moved`). **My call:** it keeps the rule that a listing never outlives its event's start.

### Expiry

`expire_listings(db, now=None) -> int` selects ACTIVE listings with `expires_at <= now` (`FOR UPDATE SKIP LOCKED`, so two runs never fight) and calls `transition(..., EXPIRED, actor_id=None)` on each. `scripts/expire_listings.py` opens a session, runs it, commits and prints the count, like `scripts/make_admin.py`. Running it twice expires nothing the second time.

Until M11 schedules it, an ACTIVE listing can sit past its expiry until someone runs the script. M5 accepts that; M6 and M8 add `expires_at > now()` to their queries, as the state-machine doc already specifies for reserving.

A PENDING_VERIFICATION listing past its expiry stays in the queue; approve refuses it and the admin rejects it. The state machine has no PENDING_VERIFICATION to EXPIRED move, and M5 does not add one.

### Frontend

New and changed pages, with types regenerated by `npm run api:types`:

| Route | Who | What |
| --- | --- | --- |
| `/tickets/:id` (changed) | Owner | When the ticket is listable and has no open listing (`GET /listings/me?ticket_id=`), a **List this ticket** form: price in rupees (defaults to the original price, shows the cap), optional expiry (defaults to the event start). **Submit for review** creates and submits; **Save draft** only creates. When an open listing exists, a link to it with its status badge. |
| `/listings` | Signed in | **My listings**: each listing's event, price, status badge and updated time, linking to its page. A status filter. |
| `/listings/:id` | Anyone | For the seller: details, status, rejection reason, actions allowed in the current status (Submit, Change price and expiry, Cancel), and the **history** timeline. For admins: the same view without seller-only actions, plus Cancel when ACTIVE and a link to the ticket. For everyone else: the public view of an ACTIVE listing. Not found renders the not-found page. |
| `/admin/listings` | Admin | **Review listings**, two tabs. **Pending review**: the queue, oldest first; each entry shows the event, category, seat, asking and original price, expiry (flagged when past), a link to the ticket and its evidence, **Approve**, and **Reject** with a required reason. **All listings**: `GET /admin/listings` with a status filter, each row linking to the listing page (where an admin can cancel an ACTIVE one). |
| `/tickets/:id` (admin viewing) | Admin | Shows the ticket's open listing, found with `GET /admin/listings?ticket_id=`, linked to its page. |

- The `/listings/:id` route is not behind `RequireAuth`; it sends the token when there is one.
- Nav: **My listings** for everyone signed in; **Review listings** for admins.
- Prices are typed in rupees and sent in paise; the conversion lives in `lib/format.ts` with unit tests (rounding, the ₹1 minimum, the cap).
- Styling follows the existing look (ticket card, form components, badges); M18 polishes.

### Tests

Backend (pytest):
- **The Done-when.** A parametrised test over all 64 (from, to) pairs of the 8 statuses: insert a listing directly in `from` (a factory that bypasses the API), call `transition()` to `to`. Allowed pairs change the status and write exactly one audit row with the right action and `from`/`to`. Every other pair raises `IllegalListingTransitionError` and leaves both the status and the audit table unchanged. A second check asserts `ALLOWED` matches the table in `docs/state-machines.md` move for move (written out in the test, so a change to either side fails it).
- **Rollback.** A transition followed by a failure in the same transaction (for example, a forced CHECK violation before commit) leaves neither the status change nor the audit row. Also through HTTP: a request whose service raises after `transition()` commits nothing.
- **Request ID.** The audit row's `request_id` equals the response's `X-Request-ID`.
- **Rules (unit).** `check_price` and `check_expiry` tables, including the boundaries (₹1, equal to original, equal to the event start, equal to now).
- **Create.** Each guard gives its code: someone else's ticket 404; admin 403; pending, rejected, ineligible and void tickets 409; draft event 404, cancelled 409, started 422; price above original and below ₹1 422; expiry after the event start 422; a second open listing 409; a new listing after the first is cancelled succeeds. Unknown body fields (`seller_id`, `status`) 422.
- **Edit, submit, cancel, review.** Each allowed and refused case above, including a price rise while ACTIVE (409), a PATCH on a rejected listing (409), admin cancelling a pending listing (409), a RESERVED listing (inserted directly) refusing cancel (409), approve refusing an expired pending listing (409), reject without a reason (422), and a non-admin on every `/admin/listings` route (403).
- **Races.** Two approves at once: one 200, one 409. Approve against cancel at once: exactly one wins, and the audit log holds exactly one of the two rows. Two price cuts on an ACTIVE listing at once: one wins, the other gets 409, and the final price is the winner's.
- **From-states.** Approve on a RESERVED listing (inserted directly) is 409 and leaves it RESERVED, even though `ALLOWED` has `RESERVED → ACTIVE`.
- **Admin as seller.** An admin edits, submits and cancels their own DRAFT by the seller's rules, recorded as `seller`.
- **No self-review.** An admin approving or rejecting their own listing gets 403 `own-listing-review`; an admin verifying a ticket they own gets 403 `own-ticket-review`; in both cases nothing changes and no audit row is written. A second admin can still review them.
- **Visibility.** Anonymous and another user: ACTIVE gives `PublicListing` without `ticket_id`, references or seller; any other status gives 404. Seller and admin get `Listing`. A guard test enumerates every route with `{listing_id}` and checks each against the visibility table, as M4's IDOR guard does for tickets. A bad token on `GET /listings/{id}` is 401.
- **History.** Seller and admin read it with the right `actor_role` per row and the rejection reason; another user gets 404; no row exposes an admin's identity.
- **Retrofits.** Verifying a ticket, creating, editing, publishing and cancelling an event, and creating a provider each write their row.
- **Cascade.** Cancelling an event cancels its DRAFT, PENDING_VERIFICATION and ACTIVE listings with one audit row each, leaves closed ones alone, and records the count. Moving `starts_at` earlier pulls back later expiries.
- **Expiry.** Past-expiry ACTIVE listings become EXPIRED with a system audit row; future ones and other statuses are untouched; a second run expires nothing; the script runs end to end.
- **Admin routes (FR-12's test).** A test enumerates every `/admin` route in the app and checks a USER gets 403 on each.

Frontend: unit tests for the rupee/paise conversion and any new pure logic. Playwright end to end:
1. A user registers a ticket and an admin verifies it as transfer-eligible (reusing the M4 helpers).
2. The user lists it below face value with **Submit for review** and sees PENDING_VERIFICATION.
3. The admin approves it from **Review listings**.
4. The user sees ACTIVE and a history of created, submitted and approved.
5. A second user opens the listing's address and sees the public view without the seller's private fields.
6. The user lists a second ticket as a draft; the second user opens that address and sees the not-found page.

### CI

No new services. The existing `test` and `e2e` jobs run the new tests.

### Documentation

- `docs/api-contract.md`: add `GET /listings/{id}/history` and `HistoryEntry`, `GET /admin/listings`, the `ticket_id` filter on `/listings/me`, the error codes above, the 403 `seller-only` rule, and the 403 self-review rules on listing review and ticket verification.
- `docs/state-machines.md`: the event-cancel cascade, the expiry clamp, and that PENDING_VERIFICATION listings do not expire.
- `docs/database-design.md`: the indexes M5 built.
- `README.md`: status table, a "Try it" walk-through for M5, and how to run `scripts/expire_listings.py`.
- At the end: `documentation/m05-listings-verification-and-audit-log.html` with the "Explain it" answers (why the state check belongs in the UPDATE's WHERE clause; what happens to the audit row when the listing update fails), linked from the index, then one commit, `M5: Listings, verification and audit log`, pushed.

## Calls I made

- Follow the contract's DRAFT plus `submit`, not the build plan's create-to-review.
- `GET /listings/{id}/history` added, for the seller and admins; the admin's identity is shown only as `admin`.
- `ticket_id` filter added to `GET /listings/me` so the ticket page can find its open listing.
- `GET /admin/listings` (status and ticket filters) and an **All listings** tab, so an admin can reach an ACTIVE listing to cancel it before M6 exists.
- An admin who is the seller is treated as the seller on their own listing.
- (Your decision) No self-review: 403 on approving or rejecting your own listing, and on verifying your own ticket.
- Each action checks its own from-states; the conditional PATCH also pins the price and expiry it read.
- Cancelling an event cancels its open listings; moving its start earlier pulls back later listing expiries.
- `expires_at` must be in the future and no later than the event start.
- Price and expiry changes are audited, not only status changes.
- Audit retrofitted onto ticket verification, event create, edit and status changes, and provider creation.
- 403 `seller-only` for admins on seller-only routes; 404 for any other non-seller.
- A bad token on the public listing route is 401, not a silent public view.
- Admins cancel only ACTIVE listings; pending ones are rejected.
- Approve refuses an expired pending listing; there is no PENDING_VERIFICATION to EXPIRED move.
- The review queue orders by `created_at`, not submission time (no new column).
- **Submit for review** on the ticket page is two calls (create, then submit); a failed submit leaves a draft the seller can see.

## Left to the builder

- Internal structure of services, schemas and errors, within the M3 and M4 conventions.
- How the history endpoint filters `details` per action, within the safe-key list above.
- How the rollback test forces its failure.
- Whether the union response on `GET /listings/{id}` is a Pydantic union or two response schemas with a discriminator, as long as the OpenAPI types let the frontend tell them apart.
- Page layout and component split in the frontend, within the existing look.
