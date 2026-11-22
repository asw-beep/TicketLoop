# M4 · Events, tickets and document storage: design

Date: 2026-11-22. Sources: `build-and-learn-plan.md` (M4), `plan.md` (FR-02, FR-03), `docs/api-contract.md`, `docs/requirements.md`, `docs/state-machines.md`, `docs/database-design.md`.

## Intent

TicketLoop resells real tickets, so before anything can be listed (M5) the system needs events to attach tickets to, and a way for a user to register a ticket they own and prove they bought it. That proof (a PDF or a screenshot) is sensitive: it can carry a QR code that gets someone through the gate. M4 is where the project first stores user files, and first faces IDOR: changing an ID in a URL to read someone else's data.

Why now: listings point at verified tickets, tickets point at events. Nothing in M5 onward works without this.

## Goals

1. Admins create, edit, publish, complete and cancel events. Anyone can read published events.
2. Any user registers a ticket for a published, upcoming event. The owner comes from the token, never the body.
3. The owner uploads purchase evidence (PDF, PNG or JPEG, up to 5 MiB). It goes to a private bucket under a random key, and opens only through a presigned link that expires in 5 minutes.
4. An admin reviews pending tickets, opens their evidence, and marks each VERIFIED (with transfer eligibility) or REJECTED (with a reason the owner sees).
5. **Done when:** a test proves user B gets 404 on every ticket endpoint for user A's ticket, and the browser shows the not-found page for someone else's ticket address.
6. Screens for all of it in `frontend/`, tested by hand and by Playwright in CI.

## Non-goals

- Search, filters and keyset pagination on events: M6. M4's `GET /events` returns published events in date order, nothing more.
- Audit log rows for verification decisions: the audit helper arrives in M5, which retrofits it onto `verify`.
- Voiding a ticket (`ACTIVE → VOID`) and the "cancel an event with open listings" rule: they need listings and disputes (M5, M13).
- Resubmitting a rejected ticket. REJECTED is final in v1; the provider reference stays taken.
- Real provider verification. `plan.md` is explicit that a screenshot proves nothing; the admin's decision is a stand-in until a provider API exists.
- Virus scanning, image re-encoding, EXIF stripping, deleting evidence, and cleaning up orphaned objects.
- An admin screen for ticket providers. One provider is seeded; the API routes exist.

## Anti-goals (a failure even if everything "works")

- Any response, log line or error that lets a user learn whether another user's ticket or document ID exists.
- An object in the bucket readable without a signature, or a link that lives longer than 5 minutes.
- A stored file whose type came from the browser's claim rather than its bytes, or whose key contains the user's filename.
- Code that talks to MinIO specifically. Everything goes through the S3 API via boto3, so the server can be swapped by changing settings.

## Constraints

- Fits the M3 shape: `app/modules/<name>/{models,schemas,service,router,errors}.py`, plain `def` routes, services that never commit (`get_db` commits), errors as `AppError` subclasses rendered as problem+json, settings in `app/core/config.py` with defaults that match docker-compose.
- Contract conventions hold: `/api/v1`, every admin route under `/admin`, unknown body fields rejected, money in paise, lists return `{items, next_cursor}` with `limit` (default 20, max 100), and "not yours" is 404.
- MinIO stopped publishing its free image in Oct 2025. Use `cgr.dev/chainguard/minio`. Chainguard publishes only `latest`, so pin by digest (currently `sha256:548147b5fc95a67e3e45285dff7e0e50326c3eb1977c6218039461771156c5e7`).
- That image's entrypoint is `/usr/bin/minio` with no default command. GitHub service containers cannot pass a command, so CI starts MinIO with a `docker run` step instead.
- Presigned URLs are signed for a host. Inside Docker the API reaches storage at `minio:9000`; the browser reaches it at `localhost:9000`.
- No AI attribution in commits or files.

## Approaches (decided)

**Paging.** The build plan teaches limit/offset in M4 and keyset in M6; the contract wants an opaque cursor everywhere.
- **A (chosen):** offset paging now, with the offset hidden inside the opaque cursor. The response shape matches the contract from day one; M6 changes what the cursor holds and compares the two at page 500; clients never change.
- B: keyset now. Matches the contract fully, but skips learning offset first and removes M6's before-and-after.

**Storage server.**
- **Chainguard's free MinIO build (chosen)**, pinned by digest. What the plan suggests, and the most documented S3-compatible server.
- Garage or SeaweedFS: also S3-compatible, less familiar. Swapping later costs only settings.

**How tests reach storage.**
- **A real MinIO (chosen)**, run the way Postgres is: integration tests skip without it locally and fail without it in CI. A few unit tests of upload validation use an in-memory fake. Only a real server proves a presigned link opens and that the bucket refuses anonymous reads.
- Moto (S3 simulated inside the test process): faster, no container, but its links cannot be opened, so the browser test of "open your evidence" could not run.

**Upload path.** Through the API (the contract's `multipart/form-data` route), not a presigned PUT from the browser. Only this way can the server check the file's bytes before it is stored.

## Design

### Data

One migration:

1. `ticket_documents`, exactly as `docs/schema-draft.sql` drafts it: `id`, `ticket_id` (FK, indexed), `uploaded_by` (FK users), `storage_key` (unique), `content_type` (CHECK in pdf/png/jpeg), `size_bytes` (CHECK 1 to 5 242 880), `created_at`.
2. `tickets.verification_note TEXT NULL`: the admin's reason, shown to the owner. **My call:** without it a rejected seller never learns why, and the audit log that could hold it does not exist until M5.
3. Seed `ticket_providers (code 'fakeprovider', name 'FakeProvider')` as data in the migration, so every database built from migrations (tests and CI included) has a provider to attach events to. The downgrade deletes it.

Models: `TicketDocument` in `app/modules/tickets/models.py`; `verification_note` on `Ticket`. `alembic check` must stay clean.

### Storage adapter (`app/infrastructure/storage/`)

A small class wrapping boto3, created once at startup and stored on `app.state` (like the email sender), and injected with a dependency so tests can swap it.

- `put_object(key, data: bytes, content_type)`: uploads with the given `ContentType`.
- `presigned_get_url(key, expires_in=300) -> str`: signed with SigV4 for the **public** endpoint. Presigning is local computation and makes no network call. Sets `ResponseContentType` to the stored type and `ResponseContentDisposition: inline`.
- `ensure_bucket()`: creates the bucket if it is missing. Called at startup only when `environment` is `local` or `test`; deployed environments provision the bucket outside the app.

Two boto3 clients: one on `storage_endpoint_url` for real calls, one on `storage_public_url` for presigning. Path-style addressing (`addressing_style: path`), SigV4.

Settings (all with defaults matching compose): `storage_endpoint_url` (`http://localhost:9000`), `storage_public_url` (defaults to the endpoint), `storage_bucket` (`ticketloop-documents`), `storage_access_key`, `storage_secret_key` (SecretStr), `storage_region` (`us-east-1`). Staging and production refuse to start with the built-in dev credentials, like the JWT key does.

New dependencies: `boto3`, `python-multipart` (FastAPI needs it for `UploadFile`).

### Paging (`app/core/pagination.py`)

- `Page[T]` response model: `{items: list[T], next_cursor: str | None}`.
- Cursor: base64url of a small JSON object holding the offset (`{"o": 40}`). Anything that fails to decode, or a negative offset, is 400 `bad-cursor`.
- A helper takes a `select()`, the cursor and the limit, fetches `limit + 1` rows, and sets `next_cursor` only when the extra row exists.
- Every list query has a total order ending in `id`, so pages never repeat or skip rows when sort values tie.

### Events (`app/modules/events/`)

| Route | Who | Notes |
| --- | --- | --- |
| `GET /events` | Public | `PUBLISHED` only, ordered `starts_at, id`. No filters until M6. |
| `GET /events/{id}` | Public | 404 for any non-published event. |
| `GET /admin/events` | Admin | **Added to the contract.** All statuses, ordered `starts_at, id`. The admin page needs drafts. |
| `POST /admin/events` | Admin | Creates `DRAFT`. 422 if `starts_at` is not in the future; 404 unknown `provider_id`. |
| `PATCH /admin/events/{id}` | Admin | Any field plus `status`. |
| `GET /admin/providers`, `POST /admin/providers` | Admin | As in the contract. 409 on a duplicate code. |

Rules, enforced in the service:
- Status transitions: `DRAFT → PUBLISHED`, `PUBLISHED → COMPLETED`, `DRAFT | PUBLISHED → CANCELLED`. Anything else is 409 `illegal-status-change`. The write is a conditional `UPDATE ... WHERE id = :id AND status = :from` (the pattern in `docs/state-machines.md`), so two admins acting at once cannot both win.
- **My calls:** publishing needs `starts_at` in the future. Completing needs `starts_at` in the past. `CANCELLED` and `COMPLETED` events are read-only (409 on any edit). `provider_id` can change only while `DRAFT`. A changed `starts_at` must be in the future.
- `Event` response: the contract's fields plus `provider_id` (the admin form needs it).

### Tickets (`app/modules/tickets/`)

| Route | Who | Notes |
| --- | --- | --- |
| `POST /tickets` | User | Body per the contract. Owner and original owner both come from the token. |
| `GET /tickets/me` | User | Your tickets, newest first (`created_at DESC, id DESC`). |
| `GET /tickets/{id}` | Owner, Admin | 404 for anyone else. |
| `POST /tickets/{id}/documents` | Owner | Upload. |
| `GET /tickets/{id}/documents` | Owner, Admin | **Added to the contract.** Lists `{id, content_type, size_bytes, created_at}`; the page needs it to show and open evidence. |
| `GET /tickets/{id}/documents/{doc_id}` | Owner, Admin | `{url, expires_at}`. 404 unless the document belongs to that ticket. |
| `GET /admin/tickets/pending` | Admin | `PENDING` tickets, oldest first (it is a queue). |
| `POST /admin/tickets/{id}/verify` | Admin | `{decision, transfer_eligible, note}`. |

Ownership means `current_owner_id = caller`. One lookup function, `get_visible_ticket(db, ticket_id, user)`, returns the ticket when the caller is the owner or an admin and raises 404 otherwise. Every ticket route goes through it, so there is one place to get IDOR right. The 404 detail is identical for "does not exist" and "not yours".

Registration rules:
- Event must be `PUBLISHED`. A `DRAFT` event is 404 (users cannot see drafts); `CANCELLED` or `COMPLETED` is 409 `event-not-open`; an event already started is 422 `event-started`.
- Duplicate `(event_id, provider_reference)` is 409 `ticket-already-registered`, detected from the unique constraint (the existing `violated_constraint` helper), not by a check-then-insert.
- **My calls on field limits:** `ticket_category` 1 to 50 chars, `seat_details` up to 200, `purchase_reference` and `provider_reference` 1 to 100 (trimmed), `original_price_paise` from 1 to 1 00 00 000 (₹1,00,000) to catch typos.

`Ticket` response: the contract's fields plus `purchase_reference`, `provider_reference` and `verification_note` (every M4 ticket route is owner-or-admin, so these are always included; M5's public listing shape leaves them out), plus **`event: {id, name, venue, city, starts_at}`** so the My tickets page needs no second call. Embedding the event summary is **my call** and is added to the contract, mirroring `PublicListing`.

Upload rules (`POST /tickets/{id}/documents`):
1. Owner only. An admin gets 403 `owner-only`: they can already see the ticket exists, so 404 would hide nothing, and admins review evidence rather than add it. Anyone else gets 404. Ticket must be `PENDING`, else 409 `ticket-not-pending`. At most 5 documents per ticket, else 409 (**my call**, a cheap abuse limit).
2. Reject early with 413 when `Content-Length` is far beyond 5 MiB. Then read at most 5 MiB + 1 byte: more is 413 `file-too-large`; zero bytes is 422.
3. The type comes from the first bytes: `%PDF-` → `application/pdf`, `\x89PNG\r\n\x1a\n` → `image/png`, `\xFF\xD8\xFF` → `image/jpeg`. Anything else is 415 `unsupported-file-type`. The browser's claimed type and the filename are ignored and never stored.
4. Key: `ticket-documents/<uuid4 hex>`. Store the object first, then insert the row. If storage fails, 503 `storage-unavailable` and no row. If the insert fails after the upload, the object is left orphaned (harmless under a random key; cleanup is a non-goal).
5. Never log file contents or presigned URLs (the URL is a bearer credential for 5 minutes).

Verification rules (`POST /admin/tickets/{id}/verify`):
- Conditional `UPDATE ... WHERE verification_status = 'PENDING'`; a ticket already decided is 409 `ticket-not-pending`. Sets `verified_by`, `verified_at` and `verification_note`.
- `REJECTED` forces `transfer_eligible = false` and requires a `note` (422 without one). `VERIFIED` takes `transfer_eligible` as given; the note is optional. Notes are up to 500 chars.

### Frontend

New pages, with types regenerated by `npm run api:types`:

| Route | Who | What |
| --- | --- | --- |
| `/tickets` | Signed in | **My tickets**: your tickets with event, category, price and a status badge. A **Register a ticket** form with an event picker fed by `GET /events`. |
| `/tickets/:id` | Signed in | Ticket detail: fields, verification status and note, the evidence list with **Open** per document, and an upload form while `PENDING`. An API 404 renders the not-found page. |
| `/admin/events` | Admin | All events with status, plus create and edit forms and status actions (Publish, Mark completed, Cancel). |
| `/admin/tickets` | Admin | The pending queue: each ticket's details, its evidence with **Open**, and Verify (with a transfer-eligible checkbox) or Reject (with a required reason). |

- A `RequireAdmin` guard: non-admins see the not-found page on admin routes.
- Nav: **My tickets** for everyone signed in; **Events** and **Verify tickets** for admins.
- **Open**: fetch the presigned URL on click, then open it in a new tab. Opening a tab after an `await` can trip popup blockers; the builder picks a pattern that works in Chrome (for example, open the tab synchronously and set its location once the URL arrives).
- The upload sends `FormData` through the typed client.
- Styling follows the existing M3 look (the ticket card and form components); M18 polishes.

### Docker and configuration

- `docker-compose.yml`: a `minio` service from the pinned image, `command: ["server", "/data", "--console-address", ":9001"]`, root user and password from `.env` with dev defaults, ports `9000` (S3) and `9001` (web console), and a `miniodata` volume. The `api` service gets `STORAGE_ENDPOINT_URL=http://minio:9000` and `STORAGE_PUBLIC_URL=http://localhost:9000`.
- `.env.example` documents every storage variable.
- Day-to-day becomes `docker compose up -d db mailpit minio`.

### Tests

Backend (pytest):
- **The IDOR test (the Done-when).** User A owns a ticket with a document. For every ticket route with a path ID, user B gets 404 and an anonymous caller gets 401. A guard test enumerates the app's routes containing `{ticket_id}` or `{document_id}` and fails if one is missing from the IDOR list, so a route added later cannot skip it. An admin reads A's ticket and documents. A's own document ID under a different ticket is 404.
- Events: a USER creating an event gets 403; a guest sees published events and not drafts (list and detail); every legal and illegal transition; past `starts_at` gives 422; edits to cancelled events give 409; paging returns every row once across pages, and a garbage cursor gives 400.
- Tickets: owner comes from the token even if the body tries to set one (an unknown field gives 422); duplicate provider reference gives 409; draft, cancelled and started events are refused with their codes; `GET /tickets/me` returns only your own.
- Documents: anonymous upload 401; oversized 413; a `.png` name with PDF bytes is stored as PDF; text renamed to `.jpg` gives 415; empty file 422; sixth document 409; upload to a verified ticket 409. With a real MinIO, the presigned URL returns 200 with the right `Content-Type`, and the same object fetched without a signature returns 403 (the bucket is private).
- Verification: verify and reject paths; a second decision gives 409; reject without a note gives 422; a non-admin gets 403.
- Storage availability works like Postgres: a fixture skips storage tests when MinIO is unreachable, and fails instead when `REQUIRE_STORAGE=1`. Tests use their own bucket, emptied at the start of the run.

Frontend: unit tests for any new pure logic (cursor handling, file checks before upload). Playwright end to end:
1. An admin (promoted with `scripts.make_admin` from the test) creates and publishes an event.
2. A user registers a ticket for it, uploads a PNG, and opens it through its link.
3. The admin opens the evidence from the queue and verifies the ticket; the user sees VERIFIED.
4. A second user visits the first user's ticket address and sees the not-found page.

### CI

- `test` and `e2e` jobs: a step runs `docker run -d -p 9000:9000 ... cgr.dev/chainguard/minio@sha256:... server /data` and waits for `http://localhost:9000/minio/health/live` before the tests. The `test` job sets `REQUIRE_STORAGE=1`.
- Everything else stays as M3 left it.

### Documentation

- `docs/api-contract.md`: add `GET /admin/events` and `GET /tickets/{id}/documents`, add `event`, `verification_note` and `provider_id` to the shapes, and note that `GET /events` filters land in M6.
- `docs/database-design.md`: `verification_note` and the seeded provider.
- `README.md`: status table, the `minio` service, the storage variables, and a "Try it" walk-through for M4.
- At the end: `documentation/m04-events-tickets-and-document-storage.html` with the "Explain it" answers (404 versus 403, presigned links versus a public bucket), linked from the index, then one commit, `M4: Events, tickets and document storage`, pushed.

## Calls I made

- `tickets.verification_note` column, required on rejection.
- `GET /admin/events` and `GET /tickets/{id}/documents` added; `Ticket` embeds an event summary; `Event` includes `provider_id`.
- Event rules: publish only future events, complete only past ones, cancelled and completed are read-only, provider fixed after DRAFT.
- Field limits, including a ₹1,00,000 price ceiling.
- At most 5 documents per ticket; uploads only while PENDING; admins cannot upload (403, since they can see the ticket).
- One provider seeded by migration; no providers screen.
- `GET /events` has no filters until M6.
- Non-admins see the not-found page on admin screens.
- Bucket created by the app at startup in local and test only.

## Left to the builder

- Internal structure of services, schemas and the paging helper, within the M3 conventions.
- The exact early-reject threshold on `Content-Length`.
- The popup-safe way to open presigned links.
- Page layout and component split in the frontend, within the existing look.
- How the e2e test promotes its admin (subprocess call or a small test-only helper).
