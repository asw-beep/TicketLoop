# M4 · Events, tickets and document storage: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Admins manage events, users register tickets and upload private purchase evidence opened through 5-minute links, admins verify tickets, and no user can reach another user's ticket or files.

**Architecture:** Two new modules (`events`, `tickets`) in the M3 shape (models, schemas, errors, service, router), a boto3 storage adapter in `app/infrastructure/storage/` with an in-memory twin for tests, and an offset-in-a-cursor paging helper in `app/core/pagination.py`. MinIO (Chainguard build, pinned by digest) runs in Docker Compose and in CI through `docker run`. Four new React screens.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, Postgres 17, boto3, python-multipart, MinIO (`cgr.dev/chainguard/minio`), React 19 + TanStack Query + react-router + openapi-fetch, Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-22-m4-events-tickets-documents-design.md`

## Global Constraints

- All paths under `/api/v1`; every admin route under `/admin`.
- Request bodies extend `RequestModel` (unknown fields are 422); responses extend `ResponseModel`.
- Money is integer paise; `original_price_paise` is 1 to 10_000_000.
- Lists take `limit` (default 20, max 100) and `cursor`, and return `{items, next_cursor}`.
- A ticket or document that exists but is not yours is **404** with the same body as one that does not exist.
- Services never commit; `get_db` commits. Errors are `AppError` subclasses (problem+json).
- Evidence: PDF, PNG or JPEG decided by the file's first bytes; 1 to 5_242_880 bytes; key `ticket-documents/<uuid4 hex>`; presigned GET valid 300 seconds.
- MinIO image: `cgr.dev/chainguard/minio@sha256:548147b5fc95a67e3e45285dff7e0e50326c3eb1977c6218039461771156c5e7`, run with `server /data`.
- Never log file contents or presigned URLs.
- No AI-tool credit anywhere: no tool or vendor names, no co-author trailers, in any file or commit. Before every commit, run the attribution grep from the project notes over `git diff --cached` and expect no hits.
- Per-task commits are local checkpoints. Task 12 squashes all M4 work into one commit, `M4: Events, tickets and document storage`, and pushes.

## Review Focus

1. A `starts_at` sent without a timezone must be 422, never silently read as UTC. Test in Task 4.
2. Whitespace-only text fields (`"   "` as an event name or provider reference) must be 422 after trimming. Tests in Tasks 4 and 5.
3. A hand-edited cursor (garbage, negative offset, valid base64 of the wrong JSON) must be 400, and `limit=0` or `limit=101` must be 422. Tests in Task 2.
4. An upload named `../../etc/passwd.png` must be stored under a random key with no trace of the name. Test in Task 6.
5. A draft event whose start time passed before anyone published it must refuse to publish (409). Test in Task 4.

---

### Task 1: Object storage in Docker, settings and adapter

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (`uv add boto3 python-multipart`)
- Modify: `app/core/config.py`, `.env.example`, `docker-compose.yml`, `.github/workflows/ci.yml`, `app/main.py`
- Create: `app/infrastructure/storage/base.py`, `app/infrastructure/storage/s3.py`, `app/infrastructure/storage/memory.py`
- Modify: `app/infrastructure/storage/__init__.py`
- Modify: `tests/integration/conftest.py`
- Test: `tests/unit/test_storage_settings.py`, `tests/integration/test_storage_s3.py`

**Interfaces:**
- Produces:
  - `app.infrastructure.storage`: `ObjectStorage` (Protocol), `StorageError(Exception)`, `PRESIGN_SECONDS = 300`, `S3Storage`, `InMemoryStorage`, `get_storage(request) -> ObjectStorage`, `Storage = Annotated[ObjectStorage, Depends(get_storage)]`.
  - `ObjectStorage.put_object(key: str, data: bytes, content_type: str) -> None` (raises `StorageError` on any boto/connection failure)
  - `ObjectStorage.presigned_get_url(key: str, content_type: str, expires_in: int = PRESIGN_SECONDS) -> str`
  - `ObjectStorage.ensure_bucket() -> None`
  - `S3Storage.from_settings(settings: Settings, bucket: str | None = None) -> S3Storage`
  - `InMemoryStorage.objects: dict[str, tuple[bytes, str]]` (key to data and content type)
  - Settings: `storage_endpoint_url: str = "http://localhost:9000"`, `storage_public_url: str | None = None`, property `storage_public_endpoint -> str` (public URL or the endpoint), `storage_bucket: str = "ticketloop-documents"`, `storage_access_key: str = "ticketloop"`, `storage_secret_key: SecretStr = SecretStr(LOCAL_STORAGE_SECRET)` with `LOCAL_STORAGE_SECRET = "ticketloop-local-secret"`, `storage_region: str = "us-east-1"`, `max_request_bytes: int = 6 * 1024 * 1024` (used in Task 6).
  - `app.state.storage` holds the app's `ObjectStorage`.
  - Test fixture `s3_storage` (session scope): an `S3Storage` on bucket `ticketloop-documents-test`, emptied at session start.

- [ ] **Step 1: Write the failing settings test** in `tests/unit/test_storage_settings.py`:

```python
def test_production_refuses_the_built_in_storage_secret():
    with pytest.raises(ValidationError, match="STORAGE_SECRET_KEY"):
        Settings(_env_file=None, environment="production", jwt_secret_key="x" * 40)


def test_public_endpoint_defaults_to_the_endpoint():
    s = Settings(_env_file=None, storage_endpoint_url="http://minio:9000")
    assert s.storage_public_endpoint == "http://minio:9000"
    s2 = s.model_copy(update={"storage_public_url": "http://localhost:9000"})
    assert s2.storage_public_endpoint == "http://localhost:9000"


def test_in_memory_storage_keeps_bytes_and_type():
    storage = InMemoryStorage()
    storage.put_object("k", b"%PDF-1", "application/pdf")
    assert storage.objects["k"] == (b"%PDF-1", "application/pdf")
    assert "k" in storage.presigned_get_url("k", "application/pdf")
```

- [ ] **Step 2: Run it and see it fail.** `uv run pytest tests/unit/test_storage_settings.py -v` → FAIL (import errors).

- [ ] **Step 3: Add the settings and validator** in `app/core/config.py`, mirroring `_check_jwt_secret`: staging and production with `LOCAL_STORAGE_SECRET` raise `ValueError("set STORAGE_SECRET_KEY: ...")`.

- [ ] **Step 4: Implement the adapter.** `uv add boto3 python-multipart`. `S3Storage` builds two boto3 clients with `Config(signature_version="s3v4", s3={"addressing_style": "path"})`: one on `storage_endpoint_url` for `put_object`/`head_bucket`/`create_bucket`, one on `storage_public_endpoint` for `generate_presigned_url("get_object", Params={Bucket, Key, ResponseContentType: content_type, ResponseContentDisposition: "inline"}, ExpiresIn=expires_in)`. Wrap `botocore.exceptions.BotoCoreError` and `ClientError` in `StorageError`. `ensure_bucket` creates the bucket when `head_bucket` says 404. `InMemoryStorage.presigned_get_url` returns `f"memory://{key}?expires_in={expires_in}"`.

- [ ] **Step 5: Run the unit test.** Same command → PASS.

- [ ] **Step 6: Wire the app.** In `create_app`, `app.state.storage = S3Storage.from_settings(settings)`. In `lifespan`, when `settings.environment == "local"`, call `ensure_bucket()` inside `try/except StorageError` that logs `storage.unavailable` as a warning (the API still starts without MinIO; uploads then return 503). Not in `test`: unit tests run without MinIO.

- [ ] **Step 7: Compose and env.** Add a `minio` service: the pinned image, `command: ["server", "/data", "--console-address", ":9001"]`, `MINIO_ROOT_USER: ${STORAGE_ACCESS_KEY:-ticketloop}`, `MINIO_ROOT_PASSWORD: ${STORAGE_SECRET_KEY:-ticketloop-local-secret}`, ports `9000:9000` and `9001:9001`, volume `miniodata:/data`. Give `api` `STORAGE_ENDPOINT_URL: http://minio:9000`, `STORAGE_PUBLIC_URL: http://localhost:9000`, and `depends_on: minio: condition: service_started`. Document every `STORAGE_*` variable in `.env.example`. Run `docker compose up -d minio` and confirm `curl -sf localhost:9000/minio/health/live` exits 0.

- [ ] **Step 8: Add the `s3_storage` fixture** in `tests/integration/conftest.py`: build `S3Storage.from_settings(test_settings, bucket="ticketloop-documents-test")`, call `ensure_bucket()`; on `StorageError`, `pytest.fail` when `REQUIRE_STORAGE == "1"`, else `pytest.skip("MinIO is not running. Start it with `docker compose up -d minio`.")`. Delete every object in the bucket before yielding.

- [ ] **Step 9: Write the real-storage test** `tests/integration/test_storage_s3.py` (mark `integration`):

```python
def test_a_presigned_link_serves_the_object_with_its_type(s3_storage):
    key = f"ticket-documents/{uuid.uuid4().hex}"
    s3_storage.put_object(key, PNG_BYTES, "image/png")
    response = httpx.get(s3_storage.presigned_get_url(key, "image/png"))
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == PNG_BYTES


def test_the_bucket_refuses_unsigned_reads(s3_storage):
    key = f"ticket-documents/{uuid.uuid4().hex}"
    s3_storage.put_object(key, PNG_BYTES, "image/png")
    unsigned = s3_storage.presigned_get_url(key, "image/png").split("?")[0]
    assert httpx.get(unsigned).status_code == 403


def test_a_link_stops_working_after_it_expires(s3_storage):
    key = f"ticket-documents/{uuid.uuid4().hex}"
    s3_storage.put_object(key, PNG_BYTES, "image/png")
    url = s3_storage.presigned_get_url(key, "image/png", expires_in=1)
    time.sleep(2)
    assert httpx.get(url).status_code == 403


def test_put_object_raises_storage_error_when_the_server_is_down(test_settings):
    dead = S3Storage.from_settings(
        test_settings.model_copy(update={"storage_endpoint_url": "http://localhost:9"})
    )
    with pytest.raises(StorageError):
        dead.put_object("k", b"x", "image/png")
```

`PNG_BYTES` is a valid 1×1 PNG; put it in `tests/integration/files.py` with `PDF_BYTES` (`b"%PDF-1.7\n..."`) and `JPEG_BYTES` (starting `b"\xff\xd8\xff"`) for later tasks. The HTTP client in the dev group imports as `httpx2` (`import httpx2 as httpx` keeps the snippets above as written); there is no `httpx` module.

- [ ] **Step 10: Run it.** `uv run pytest tests/integration/test_storage_s3.py -v` → 4 PASS with MinIO up; 4 SKIP after `docker compose stop minio` (then start it again).

- [ ] **Step 11: CI.** In the `test` and `e2e` jobs, add a step before the tests:

```yaml
- name: Start MinIO
  # Its image has no default command, which service containers cannot supply.
  run: |
    docker run -d --name minio -p 9000:9000 \
      -e MINIO_ROOT_USER=ticketloop -e MINIO_ROOT_PASSWORD=ticketloop-local-secret \
      cgr.dev/chainguard/minio@sha256:548147b5fc95a67e3e45285dff7e0e50326c3eb1977c6218039461771156c5e7 server /data
    for i in $(seq 1 30); do curl -sf http://localhost:9000/minio/health/live && exit 0; sleep 1; done
    docker logs minio; exit 1
```

Add `REQUIRE_STORAGE: "1"` to the `test` job's `env`.

- [ ] **Step 12: Full check and commit.** `uv run ruff check . && uv run ruff format --check . && uv run pytest` → all pass. Commit `M4 wip: object storage`.

---

### Task 2: Paging helper

**Files:**
- Create: `app/core/pagination.py`
- Test: `tests/unit/test_pagination.py`, `tests/integration/test_pagination_db.py`

**Interfaces:**
- Produces:
  - `class Page(ResponseModel, Generic[T])`: `items: list[T]`, `next_cursor: str | None`
  - `Limit = Annotated[int, Query(ge=1, le=100)]` (routes declare `limit: Limit = 20`)
  - `Cursor = Annotated[str | None, Query(max_length=200)]`
  - `class BadCursorError(AppError)`: status 400, code `bad-cursor`, title `The cursor is not valid`
  - `encode_cursor(offset: int) -> str`, `decode_cursor(cursor: str | None) -> int`
  - `paginate(db: Session, stmt: Select, cursor: str | None, limit: int) -> tuple[list, str | None]`

- [ ] **Step 1: Write the failing unit tests:**

```python
def test_a_cursor_round_trips():
    assert decode_cursor(encode_cursor(40)) == 40


def test_no_cursor_means_the_first_page():
    assert decode_cursor(None) == 0


@pytest.mark.parametrize(
    "bad",
    [
        "not base64!",
        encode_raw(b"[]"),
        encode_raw(b'{"o": -1}'),
        encode_raw(b'{"o": "x"}'),
        encode_raw(b"{}"),
    ],
)
def test_a_tampered_cursor_is_refused(bad):
    with pytest.raises(BadCursorError):
        decode_cursor(bad)


@pytest.mark.parametrize("limit", [0, 101])
def test_limits_outside_1_to_100_are_422(limit):
    probe = FastAPI()

    @probe.get("/probe")
    def route(limit: Limit = 20) -> int:
        return limit

    assert TestClient(probe).get("/probe", params={"limit": limit}).status_code == 422
```

`encode_raw` is base64url without padding in the test file; the cursor format is base64url of `{"o": <offset>}`.

- [ ] **Step 2: Run and see them fail.** `uv run pytest tests/unit/test_pagination.py -v` → FAIL (import error).

- [ ] **Step 3: Implement `app/core/pagination.py`.** `paginate` runs `stmt.offset(offset).limit(limit + 1)`, returns the first `limit` rows and `encode_cursor(offset + limit)` only when the extra row came back. Use `db.scalars(...).unique().all()` so joined eager loads work.

- [ ] **Step 4: Write the DB test** `tests/integration/test_pagination_db.py`: insert 7 providers with `make_provider`, page `select(TicketProvider).order_by(TicketProvider.code, TicketProvider.id)` with `limit=3`, follow `next_cursor` until `None`; assert 3 pages, every id seen exactly once, last page's cursor is `None`.

- [ ] **Step 5: Run both files** → PASS. Commit `M4 wip: paging helper`.

---

### Task 3: Migration and models

**Files:**
- Modify: `app/modules/tickets/models.py`, `app/models.py` (if it lists models explicitly)
- Create: `migrations/versions/2026_11_22_<rev>_ticket_documents_and_verification_note.py` (via `uv run alembic revision --autogenerate -m "ticket documents and verification note"`)
- Modify: `tests/integration/factories.py`, `tests/integration/test_migrations.py`, `tests/integration/test_constraints.py`

**Interfaces:**
- Produces:
  - `DocumentType(StrEnum)`: `PDF = "application/pdf"`, `PNG = "image/png"`, `JPEG = "image/jpeg"`
  - `MAX_DOCUMENT_BYTES = 5_242_880`, `MAX_DOCUMENTS_PER_TICKET = 5` (in `tickets/models.py`)
  - `TicketDocument(UUIDPrimaryKey, CreatedAt, Base)`: `ticket_id` (FK, `index=True`), `uploaded_by` (FK users), `storage_key` (unique), `content_type`, `size_bytes: BigInteger`; checks named `type` and `size` (`size_bytes BETWEEN 1 AND 5242880`)
  - `Ticket.verification_note: Mapped[str | None]`
  - `Ticket.event: Mapped[Event] = relationship(lazy="joined", innerjoin=True)`
  - `FAKE_PROVIDER_CODE = "fakeprovider"` (in `events/models.py`)
  - Factory `make_document(session, **fields) -> TicketDocument`

- [ ] **Step 1: Write the failing tests.** In `test_migrations.py` add `"ticket_documents"` to a new `M4_TABLES` included in `ALL_TABLES`, and:

```python
def test_the_fake_provider_is_seeded(db_session):
    assert (
        db_session.scalar(select(TicketProvider.name).where(TicketProvider.code == "fakeprovider"))
        == "FakeProvider"
    )
```

In `test_constraints.py`:

```python
def test_a_document_must_be_pdf_png_or_jpeg(db_session):
    with rejected_by(db_session, "ck_ticket_documents_type"):
        make_document(db_session, content_type="text/html")


@pytest.mark.parametrize("size", [0, 5_242_881])
def test_a_document_must_be_1_byte_to_5_mib(db_session, size):
    with rejected_by(db_session, "ck_ticket_documents_size"):
        make_document(db_session, size_bytes=size)


def test_two_documents_cannot_share_a_storage_key(db_session):
    make_document(db_session, storage_key="ticket-documents/same")
    with rejected_by(db_session, "uq_ticket_documents_storage_key"):
        make_document(db_session, storage_key="ticket-documents/same")
```

`rejected_by(session, constraint)` is the file's existing helper.

- [ ] **Step 2: Run and see them fail.** `uv run pytest tests/integration/test_constraints.py tests/integration/test_migrations.py -v` → FAIL.

- [ ] **Step 3: Models and migration.** Add the model, column and relationship. Autogenerate, read the draft, then append to `upgrade()`: `op.execute("INSERT INTO ticket_providers (code, name) VALUES ('fakeprovider', 'FakeProvider')")`; and to `downgrade()`, before dropping anything: `op.execute("DELETE FROM ticket_providers WHERE code = 'fakeprovider' AND NOT EXISTS (SELECT 1 FROM events WHERE provider_id = ticket_providers.id)")`. Write a docstring in the M3 migration's style.

- [ ] **Step 4: Run the tests plus `uv run alembic check`** → PASS and "No new upgrade operations detected." Commit `M4 wip: ticket documents table`.

---

### Task 4: Events and providers API

**Files:**
- Create: `app/modules/events/schemas.py`, `app/modules/events/errors.py`, `app/modules/events/service.py`, `app/modules/events/router.py`
- Modify: `app/api/router.py`
- Test: `tests/integration/test_events.py`, `tests/integration/test_providers.py`

**Interfaces:**
- Consumes: `Page`, `Limit`, `Cursor`, `paginate` (Task 2); `AdminUser` (`app.modules.identity.dependencies`).
- Produces:
  - Schemas: `EventIn` (`name` 1–200, `description` ≤5000 default `""`, `category: EventCategory`, `venue` 1–200, `city` 1–100, `starts_at: AwareDatetime`, `organizer` 1–200, `provider_id: UUID`; strings trimmed), `EventPatch` (every `EventIn` field optional, plus `status: EventStatus | None`), `EventOut` (`id, name, description, category, venue, city, starts_at, organizer, provider_id, status`), `ProviderIn` (`code` matching `^[a-z0-9-]{2,50}$`, `name` 1–100), `ProviderOut` (`id, code, name`).
  - Errors: `EventNotFoundError` (404, `event-not-found`), `ProviderNotFoundError` (404, `provider-not-found`), `EventInPastError` (422, `event-in-past`), `IllegalStatusChangeError` (409, `illegal-status-change`), `EventNotEditableError` (409, `event-not-editable`), `ProviderCodeTakenError` (409, `provider-code-taken`).
  - Service: `ALLOWED_TRANSITIONS: dict[str, set[str]]`, `list_published_events(db, cursor, limit)`, `get_published_event(db, event_id) -> Event`, `list_all_events(db, cursor, limit)`, `create_event(db, data: EventIn) -> Event`, `update_event(db, event_id: UUID, data: EventPatch) -> Event`, `list_providers(db) -> list[TicketProvider]`, `create_provider(db, data: ProviderIn) -> TicketProvider`.
  - Routes: `GET /events`, `GET /events/{event_id}`, `GET /admin/events`, `POST /admin/events` (201), `PATCH /admin/events/{event_id}`, `GET /admin/providers`, `POST /admin/providers` (201). Routers `events_router`, `admin_events_router`.

- [ ] **Step 1: Write the failing tests** in `tests/integration/test_events.py`. Add an `admin` fixture to `tests/integration/conftest.py`: register through `api`, promote with `scripts.make_admin.set_role(db_session, email, UserRole.ADMIN)`, log in, return `{"id": ..., "token": ...}`. Tests send `bearer(admin["token"])`. Key tests:

```python
def test_a_user_cannot_create_an_event(api):            # 403, type ends "admin-required"
def test_an_admin_creates_a_draft_event(api, admin):     # 201, status "DRAFT", provider_id echoed
def test_starts_at_in_the_past_is_422(api, admin):       # type ends "event-in-past"
def test_starts_at_without_a_timezone_is_422(api, admin):  # "2027-01-01T18:00:00" → 422   (Review Focus 1)
def test_a_whitespace_only_name_is_422(api, admin):      # name "   " → 422                 (Review Focus 2)
def test_an_unknown_provider_is_404(api, admin)
def test_guests_see_published_events_but_not_drafts(api, db_session):
    # list contains the published event's id and not the draft's; GET /events/{draft_id} is 404
def test_the_admin_list_shows_every_status(api, admin, db_session)
@pytest.mark.parametrize("start,to,code", [
    ("DRAFT", "PUBLISHED", 200), ("PUBLISHED", "COMPLETED", None), ("DRAFT", "CANCELLED", 200),
    ("PUBLISHED", "CANCELLED", 200), ("DRAFT", "COMPLETED", 409), ("CANCELLED", "PUBLISHED", 409),
    ("COMPLETED", "PUBLISHED", 409), ("PUBLISHED", "DRAFT", 409)])
def test_status_changes_follow_the_state_machine(...)
def test_completing_needs_the_start_time_to_have_passed(...)        # future PUBLISHED → COMPLETED is 409; past one is 200
def test_a_draft_whose_start_passed_cannot_be_published(...)        # make_event(starts_at=yesterday) → 409   (Review Focus 5)
def test_cancelled_and_completed_events_are_read_only(...)          # PATCH name → 409 "event-not-editable"
def test_the_provider_is_fixed_after_draft(...)                     # PATCH provider_id on PUBLISHED → 409
def test_events_page_through_without_repeats(api, db_session)       # 25 published, limit=10 → 3 pages, 25 unique ids
def test_a_bad_cursor_is_400(api)                                   # GET /events?cursor=garbage → 400 "bad-cursor"
```

(The `("PUBLISHED", "COMPLETED")` row uses a past start time, created directly with `make_event`.) In `test_providers.py`: list includes `fakeprovider`; create returns 201; duplicate code gives 409 `provider-code-taken`; a user gets 403.

- [ ] **Step 2: Run and see them fail.** `uv run pytest tests/integration/test_events.py tests/integration/test_providers.py -v` → FAIL (404s: routes missing).

- [ ] **Step 3: Implement schemas, errors and service.** `update_event` order: load the row (404 if missing); `CANCELLED`/`COMPLETED` raise `EventNotEditableError`; a `provider_id` change outside `DRAFT` raises `EventNotEditableError`; a changed `starts_at` must be in the future (`EventInPastError`); a `status` change must be in `ALLOWED_TRANSITIONS`, `PUBLISHED` needs the effective `starts_at` in the future and `COMPLETED` needs it in the past (`IllegalStatusChangeError` with a detail saying which). Then write every change in one `update(Event).where(Event.id == event_id, Event.status == seen_status).values(...).returning(Event)`; no row back means someone changed it meanwhile, so raise `IllegalStatusChangeError`. Duplicate provider code: catch `IntegrityError`, check `violated_constraint(e) == "uq_ticket_providers_code"`.

- [ ] **Step 4: Implement the routers** and include both in `build_api_router`. List routes return `Page[EventOut]`.

- [ ] **Step 5: Run the tests** → PASS. `uv run ruff check . && uv run pytest` → all pass. Commit `M4 wip: events API`.

---

### Task 5: Register and read tickets

**Files:**
- Create: `app/modules/tickets/schemas.py`, `app/modules/tickets/errors.py`, `app/modules/tickets/service.py`, `app/modules/tickets/router.py`
- Modify: `app/api/router.py`
- Test: `tests/integration/test_tickets.py`

**Interfaces:**
- Consumes: Task 2 paging, Task 3 models, `CurrentUser`.
- Produces:
  - Schemas: `TicketIn` (`event_id: UUID`, `ticket_category` 1–50, `seat_details` ≤200 or `None`, `original_price_paise` 1–10_000_000, `purchase_reference` 1–100, `provider_reference` 1–100; strings trimmed), `EventSummary` (`id, name, venue, city, starts_at`), `TicketOut` (`id, event_id, event: EventSummary, ticket_category, seat_details, original_price_paise, purchase_reference, provider_reference, verification_status, verification_note, transfer_eligible, status, created_at`).
  - Errors: `TicketNotFoundError` (404, `ticket-not-found`, detail always `"Ticket not found."`), `TicketAlreadyRegisteredError` (409, `ticket-already-registered`), `EventNotOpenError` (409, `event-not-open`), `EventStartedError` (422, `event-started`).
  - Service: `get_visible_ticket(db, ticket_id: UUID, user: User) -> Ticket` (owner by `current_owner_id`, or admin; else `TicketNotFoundError`), `register_ticket(db, owner: User, data: TicketIn) -> Ticket`, `list_my_tickets(db, owner: User, cursor, limit)`.
  - Routes on `tickets_router` (prefix `/tickets`): `POST /tickets` (201), `GET /tickets/me` (declared **before** `/{ticket_id}`), `GET /tickets/{ticket_id}`.

- [ ] **Step 1: Write the failing tests:**

```python
def test_registering_takes_the_owner_from_the_token(api, user, published_event):
    # 201; verification_status "PENDING"; event.name equals the event's; GET /tickets/me lists it
def test_a_body_that_names_an_owner_is_422(api, user, published_event):   # extra "current_owner_id" → 422
def test_the_same_provider_reference_twice_is_409(api, user, published_event)  # also from a second user
def test_a_draft_event_is_404(api, user, db_session)
@pytest.mark.parametrize("status", ["CANCELLED", "COMPLETED"])
def test_a_closed_event_is_409(api, user, db_session, status)             # "event-not-open"
def test_an_event_that_has_started_is_422(api, user, db_session)           # "event-started"
def test_a_whitespace_only_provider_reference_is_422(...)                  # "   "  (Review Focus 2)
@pytest.mark.parametrize("price", [0, 10_000_001])
def test_prices_outside_1_paise_to_1_lakh_rupees_are_422(...)
def test_my_tickets_lists_only_mine_newest_first(api, db_session)
def test_the_owner_and_an_admin_can_read_a_ticket(api, db_session)
def test_an_unknown_ticket_id_is_404(api, user)
```

Add `user` (registered and logged in, returns `{"id", "token"}` like `admin`) and `published_event` (`make_event(status="PUBLISHED")`, 30 days ahead) fixtures to `tests/integration/conftest.py`.

- [ ] **Step 2: Run and see them fail.** `uv run pytest tests/integration/test_tickets.py -v` → FAIL.

- [ ] **Step 3: Implement.** `register_ticket`: load the event (missing or `DRAFT` gives `EventNotFoundError` from the events module); `CANCELLED`/`COMPLETED` give `EventNotOpenError`; `starts_at <= now` gives `EventStartedError`; insert with both owner columns set to `owner.id`, flush, and map `uq_tickets_event_provider_reference` to `TicketAlreadyRegisteredError`. `list_my_tickets` orders by `created_at DESC, id DESC`.

- [ ] **Step 4: Run the tests** → PASS; full suite passes. Commit `M4 wip: tickets API`.

---

### Task 6: Evidence upload, list and links

**Files:**
- Create: `app/modules/tickets/files.py`, `app/modules/tickets/documents.py`
- Modify: `app/modules/tickets/schemas.py`, `app/modules/tickets/errors.py`, `app/modules/tickets/router.py`, `app/core/middleware.py`, `app/main.py`, `tests/integration/conftest.py`
- Test: `tests/unit/test_file_sniffing.py`, `tests/unit/test_body_size_limit.py`, `tests/integration/test_documents.py`

**Interfaces:**
- Consumes: `Storage`, `ObjectStorage`, `StorageError`, `PRESIGN_SECONDS` (Task 1); `get_visible_ticket`, `TicketNotFoundError` (Task 5); `TicketDocument`, `DocumentType`, `MAX_DOCUMENT_BYTES`, `MAX_DOCUMENTS_PER_TICKET` (Task 3).
- Produces:
  - `sniff_content_type(head: bytes) -> DocumentType | None` in `files.py`
  - Schemas `DocumentOut` (`id, content_type, size_bytes, created_at`), `DocumentLinkOut` (`url: str, expires_at: datetime`)
  - Errors: `OwnerOnlyError` (403, `owner-only`), `TicketNotPendingError` (409, `ticket-not-pending`), `TooManyDocumentsError` (409, `too-many-documents`), `FileTooLargeError` (413, `file-too-large`), `UnsupportedFileTypeError` (415, `unsupported-file-type`), `EmptyFileError` (422, `empty-file`), `StorageUnavailableError` (503, `storage-unavailable`), `DocumentNotFoundError` (404, `document-not-found`)
  - `upload_document(db, storage: ObjectStorage, ticket_id: UUID, user: User, data: bytes) -> TicketDocument`
  - `list_documents(db, ticket_id: UUID, user: User) -> list[TicketDocument]` (oldest first)
  - `document_link(db, storage: ObjectStorage, ticket_id: UUID, document_id: UUID, user: User) -> tuple[str, datetime]`
  - Routes: `POST /tickets/{ticket_id}/documents` (201, field `file: UploadFile`), `GET /tickets/{ticket_id}/documents`, `GET /tickets/{ticket_id}/documents/{document_id}`
  - `BodySizeLimitMiddleware(app, max_bytes: int)`: a request whose `Content-Length` exceeds `max_bytes` gets a 413 problem+json (`file-too-large`) without the body being read. Chunked bodies are left to the reverse proxy (M16).
  - The `db_app` fixture sets `app.state.storage = InMemoryStorage()`; fixture `memory_storage` returns it.

- [ ] **Step 1: Write the failing unit tests:**

```python
@pytest.mark.parametrize("head,expected", [
    (b"%PDF-1.7\n", DocumentType.PDF), (b"\x89PNG\r\n\x1a\n\0\0", DocumentType.PNG),
    (b"\xff\xd8\xff\xe0", DocumentType.JPEG), (b"<html>", None), (b"GIF89a", None), (b"", None)])
def test_the_type_comes_from_the_first_bytes(head, expected):
    assert sniff_content_type(head) == expected

def test_a_body_over_the_limit_is_413_before_it_is_read(client):   # Content-Length 6 MiB + 1 → 413, problem+json
```

- [ ] **Step 2: Write the failing integration tests** in `tests/integration/test_documents.py` (all through `api` with `memory_storage`, except the last):

```python
def test_the_owner_uploads_a_png(api, owned_ticket, memory_storage):
    # 201 {content_type: "image/png", size_bytes: len(PNG_BYTES)}; one object stored with type image/png
def test_the_stored_type_comes_from_the_bytes_not_the_name(...)     # PDF_BYTES sent as "photo.png", type image/png → "application/pdf"
def test_a_text_file_renamed_jpg_is_415(...)
def test_an_empty_file_is_422(...)
def test_a_file_over_5_mib_is_413(...)                              # 5_242_881 bytes of PNG header + zeros
def test_the_key_never_contains_the_filename(...)                   # name "../../etc/passwd.png" → key matches r"^ticket-documents/[0-9a-f]{32}$"   (Review Focus 4)
def test_a_sixth_document_is_409(...)                               # "too-many-documents"
def test_uploading_to_a_verified_ticket_is_409(...)                 # "ticket-not-pending"
def test_an_admin_cannot_upload(...)                                # 403 "owner-only"
def test_an_anonymous_upload_is_401(...)
def test_storage_down_gives_503_and_no_row(api, owned_ticket, db_app, db_session):
    # db_app.state.storage = a stub whose put_object raises StorageError → 503 "storage-unavailable"; zero ticket_documents rows
def test_the_owner_lists_and_links_documents(...)                   # list has 1; link url starts "memory://"; expires_at ≈ now + 300 s (±5 s)
def test_a_real_link_opens(api, owned_ticket, db_app, s3_storage):  # swap in s3_storage, upload, fetch link with httpx → 200, image/png
```

Add an `owned_ticket` fixture (a `PENDING` ticket owned by `user`).

- [ ] **Step 3: Run and see them fail.** `uv run pytest tests/unit/test_file_sniffing.py tests/unit/test_body_size_limit.py tests/integration/test_documents.py -v` → FAIL.

- [ ] **Step 4: Implement.** Route (plain `def`): read `file.file.read(MAX_DOCUMENT_BYTES + 1)` and pass the bytes to `upload_document`; never touch `file.filename` or `file.content_type`. `upload_document` order: `get_visible_ticket` (404 for strangers); admin who is not the owner gets `OwnerOnlyError`; not `PENDING` gives `TicketNotPendingError`; count ≥ 5 gives `TooManyDocumentsError`; empty gives `EmptyFileError`; over the limit gives `FileTooLargeError`; `sniff_content_type(data[:8])` is `None` → `UnsupportedFileTypeError`; `storage.put_object(f"ticket-documents/{uuid4().hex}", data, type)` (a `StorageError` becomes `StorageUnavailableError`); then insert the row. `document_link` loads the document `WHERE id = :document_id AND ticket_id = :ticket_id` after `get_visible_ticket`, else `DocumentNotFoundError`; `expires_at = now + PRESIGN_SECONDS`. Add `BodySizeLimitMiddleware` with `settings.max_request_bytes` in `create_app`.

- [ ] **Step 5: Run the tests** → PASS; full suite passes. Commit `M4 wip: evidence documents`.

---

### Task 7: Admin verification

**Files:**
- Modify: `app/modules/tickets/schemas.py`, `app/modules/tickets/service.py`, `app/modules/tickets/router.py`, `app/api/router.py`
- Test: `tests/integration/test_verification.py`

**Interfaces:**
- Consumes: `TicketNotFoundError`, `TicketNotPendingError`, `AdminUser`.
- Produces:
  - `VerifyIn`: `decision: Literal["VERIFIED", "REJECTED"]`, `transfer_eligible: bool = False`, `note: str | None` (trimmed, 1–500); a model validator makes `note` required when `decision == "REJECTED"`.
  - `list_pending_tickets(db, cursor, limit)` ordered `created_at ASC, id ASC`
  - `verify_ticket(db, ticket_id: UUID, admin: User, data: VerifyIn) -> Ticket`
  - Routes on `admin_tickets_router` (prefix `/admin/tickets`): `GET /admin/tickets/pending`, `POST /admin/tickets/{ticket_id}/verify`.

- [ ] **Step 1: Write the failing tests:**

```python
def test_the_queue_lists_pending_tickets_oldest_first(api, admin, db_session)
def test_verifying_sets_who_and_when(api, admin, owned_ticket, db_session):
    # 200; verification_status "VERIFIED"; transfer_eligible True; row has verified_by == admin id and verified_at set
def test_rejecting_needs_a_reason(api, admin, owned_ticket)          # no note → 422
def test_rejecting_forces_transfer_eligible_false(...)               # transfer_eligible True + REJECTED → stored False; note visible to the owner via GET /tickets/{id}
def test_a_second_decision_is_409(...)                                # "ticket-not-pending"
def test_an_unknown_ticket_is_404(...)
def test_a_user_cannot_verify(api, user, owned_ticket)               # 403 "admin-required"
```

- [ ] **Step 2: Run and see them fail.** `uv run pytest tests/integration/test_verification.py -v` → FAIL.

- [ ] **Step 3: Implement.** `verify_ticket` runs `update(Ticket).where(Ticket.id == ticket_id, Ticket.verification_status == "PENDING").values(verification_status=..., transfer_eligible=data.transfer_eligible and data.decision == "VERIFIED", verification_note=data.note, verified_by=admin.id, verified_at=func.now()).returning(Ticket.id)`. No row: `db.get(Ticket, ticket_id)` decides 404 versus 409. Return the reloaded ticket.

- [ ] **Step 4: Run the tests** → PASS; full suite passes. Commit `M4 wip: ticket verification`.

---

### Task 8: The IDOR proof

**Files:**
- Test: `tests/integration/test_ticket_access.py`

**Interfaces:**
- Consumes: every route from Tasks 5 to 7.

- [ ] **Step 1: Write the tests:**

```python
IDOR_ROUTES = {
    ("GET", "/api/v1/tickets/{ticket_id}"),
    ("POST", "/api/v1/tickets/{ticket_id}/documents"),
    ("GET", "/api/v1/tickets/{ticket_id}/documents"),
    ("GET", "/api/v1/tickets/{ticket_id}/documents/{document_id}"),
}

def test_every_ticket_route_is_covered(db_app):
    found = {(m, r.path) for r in db_app.routes if isinstance(r, APIRoute)
             and ("{ticket_id}" in r.path or "{document_id}" in r.path) and "/admin/" not in r.path
             for m in r.methods}
    assert found == IDOR_ROUTES

@pytest.mark.parametrize("method,path", sorted(IDOR_ROUTES))
def test_another_user_gets_404(api, method, path, alices_ticket_and_document, bob):
    # body for POST is a valid PNG upload; status 404; body type ends "ticket-not-found";
    # detail equals the detail for a random UUID (no difference between "missing" and "not yours")

@pytest.mark.parametrize("method,path", sorted(IDOR_ROUTES))
def test_an_anonymous_caller_gets_401(api, method, path, alices_ticket_and_document)

def test_an_admin_reads_alices_ticket_and_documents(api, admin, alices_ticket_and_document)  # GET routes 200

def test_alices_document_under_her_other_ticket_is_404(api, alice, ...)   # document of ticket 1 via ticket 2 → 404
```

Fixtures local to this file: `alice` and `bob` (two users shaped like `user`), and `alices_ticket_and_document` (Alice's PENDING ticket with one document uploaded through the API to `memory_storage`, returning both ids). The path placeholders are filled from it.

- [ ] **Step 2: Run them.** `uv run pytest tests/integration/test_ticket_access.py -v` → all PASS. A failure here is a real IDOR bug: fix it in the route or `get_visible_ticket`, not in the test.

- [ ] **Step 3: Commit** `M4 wip: IDOR tests`.

---

### Task 9: Frontend groundwork and the admin events page

**Files:**
- Modify: `frontend/openapi.json`, `frontend/src/api/schema.d.ts` (`npm run api:types`)
- Modify: `frontend/src/components/Guards.tsx`, `frontend/src/components/Layout.tsx`, `frontend/src/App.tsx`
- Create: `frontend/src/lib/format.ts`, `frontend/src/lib/format.test.ts`, `frontend/src/pages/AdminEventsPage.tsx`

**Interfaces:**
- Produces:
  - `RequireAdmin({ children })`: renders children for an admin, `NotFoundPage` for any other signed-in user, the login redirect when signed out.
  - `format.ts`: `formatPaise(paise: number): string` (`250000` → `"₹2,500"`, en-IN grouping, paise shown only when non-zero: `123456` → `"₹1,234.56"`); `rupeesToPaise(input: string): number | null` (`"2,500"` → `250000`, `"12.5"` → `1250`, `"abc"`/`""`/`"-1"` → `null`); `isoToLocalInput(iso: string): string` and `localInputToIso(value: string): string` for `<input type="datetime-local">` (local time in, UTC ISO out).
  - Routes: `/admin/events` (inside `RequireAdmin`). Nav: **Events** for admins.

- [ ] **Step 1: Write the failing unit tests** in `format.test.ts` with the values above, plus `localInputToIso(isoToLocalInput(x)) === x` for a whole-minute UTC instant.
- [ ] **Step 2: Run** `npm test --prefix frontend` → FAIL.
- [ ] **Step 3: Implement `format.ts`**, regenerate types, add `RequireAdmin`, the route and the nav link.
- [ ] **Step 4: Build `AdminEventsPage`:** a table of `GET /admin/events` (name, city, start, status, with "Load more" following `next_cursor`); a create form (provider picker from `GET /admin/providers`, defaulting to `fakeprovider`); an edit form per event (prefilled, sends only changed fields); and status buttons shown only for legal moves (Publish on DRAFT, Mark completed on PUBLISHED, Cancel on DRAFT or PUBLISHED). Field errors come from `errorsByField`; other errors from `FormError`. No business rules beyond which buttons appear.
- [ ] **Step 5: Run** `npm test && npm run typecheck && npm run lint && npm run format:check` (in `frontend/`) → all pass. Check by hand: an admin creates and publishes an event; a non-admin opening `/admin/events` sees the not-found page. Commit `M4 wip: admin events screen`.

---

### Task 10: My tickets and the ticket page

**Files:**
- Create: `frontend/src/pages/MyTicketsPage.tsx`, `frontend/src/pages/TicketPage.tsx`, `frontend/src/lib/documents.ts`, `frontend/src/lib/documents.test.ts`
- Modify: `frontend/src/App.tsx`, `frontend/src/components/Layout.tsx`

**Interfaces:**
- Consumes: `formatPaise`, `rupeesToPaise` (Task 9).
- Produces:
  - `documents.ts`: `MAX_DOCUMENT_BYTES = 5_242_880`; `checkFile(file: { size: number; type: string }): string | null` (a message for empty, over 5 MiB or not PDF/PNG/JPEG; `null` when fine; a convenience only, the server decides); `uploadDocument(ticketId: string, file: File): Promise<Document>` (FormData through the typed client); `openDocument(ticketId: string, documentId: string): Promise<void>` (opens a blank tab synchronously, fetches the link, sets the tab's location, clears `opener`; closes the tab and rethrows on error).
  - Routes `/tickets` and `/tickets/:ticketId` inside `RequireSession`. Nav: **My tickets**.

- [ ] **Step 1: Write the failing `checkFile` tests:** 0 bytes, 5_242_881 bytes, `text/plain` each give a message; 1 byte `image/png` and 5_242_880 bytes `application/pdf` give `null`.
- [ ] **Step 2: Run** `npm test` → FAIL.
- [ ] **Step 3: Implement `documents.ts`.**
- [ ] **Step 4: Build `MyTicketsPage`:** your tickets from `GET /tickets/me` (event name and date, category, `formatPaise` price, a status badge for PENDING, VERIFIED or REJECTED, each linking to `/tickets/:id`); a **Register a ticket** form whose event picker lists `GET /events?limit=100` and whose price field takes rupees through `rupeesToPaise`. On success, go to the new ticket's page.
- [ ] **Step 5: Build `TicketPage`:** the ticket's details, its verification status and note; the evidence list from `GET /tickets/{id}/documents` with an **Open** button per document calling `openDocument`; an upload form while `PENDING` (runs `checkFile` first, then `uploadDocument`, then refreshes the list). A 404 from `GET /tickets/{id}` renders `NotFoundPage`.
- [ ] **Step 6: Run** the four frontend checks → pass. Check by hand: register, upload a PNG, open it in a new tab. Commit `M4 wip: my tickets screens`.

---

### Task 11: Admin verification page and the browser tests

**Files:**
- Create: `frontend/src/pages/AdminTicketsPage.tsx`, `frontend/e2e/tickets.spec.ts`, `frontend/e2e/fixtures/evidence.png`
- Modify: `frontend/src/App.tsx`, `frontend/src/components/Layout.tsx`

**Interfaces:**
- Consumes: `openDocument` (Task 10), `RequireAdmin` (Task 9).
- Produces: route `/admin/tickets` inside `RequireAdmin`; nav **Verify tickets** for admins.

- [ ] **Step 1: Build `AdminTicketsPage`:** the queue from `GET /admin/tickets/pending` (event, category, seat, price, purchase and provider references), each ticket's evidence with **Open**, a **Verify** button with a "Transfer eligible" checkbox (checked by default), and **Reject** with a required reason. A decided ticket leaves the queue.
- [ ] **Step 2: Write `tickets.spec.ts`** (reuse the register/login helpers from `auth.spec.ts`, moved to `e2e/helpers.ts` if needed). Promote the admin with `execFileSync("uv", ["run", "--directory", "..", "python", "-m", "scripts.make_admin", email])`.

```ts
test("an admin publishes an event, a user registers a ticket with evidence, the admin verifies it", async ({ page, browser }) => {
  // admin: /admin/events → create → Publish → status shows Published
  // user (new context): /tickets → register for that event → ticket page shows Pending
  //   upload e2e/fixtures/evidence.png → listed; Open → popup URL starts http://localhost:9000/,
  //   and a request to it returns 200 with content-type image/png
  // admin: /admin/tickets → Open shows the same evidence → Verify
  // user: reload ticket page → Verified
});

test("another user's ticket address shows the not-found page", async ({ browser }) => {
  // user A registers a ticket; user B opens /tickets/<A's id> → heading of NotFoundPage visible
});

test("a non-admin sees the not-found page on admin screens", async ({ page }) => { /* /admin/events and /admin/tickets */ });
```

- [ ] **Step 3: Run** `docker compose up -d db mailpit minio`, then `npm run e2e --prefix frontend` → all specs pass (the M3 ones too).
- [ ] **Step 4: Screenshots.** With Playwright driving Chrome, capture the four new screens in light, dark and at 390 px width; fix anything broken or unreadable. Save the light desktop ones as `documentation/assets/m04-*.png` for Task 12.
- [ ] **Step 5: Commit** `M4 wip: admin verification screen and browser tests`.

---

### Task 12: Docs, revision page, one commit, push

**Files:**
- Modify: `docs/api-contract.md`, `docs/database-design.md`, `README.md`, `build-and-learn-plan.md` (only if a fact in M4's section changed)
- Create: `documentation/m04-events-tickets-and-document-storage.html`
- Modify: `documentation/index.html`, `documentation/m03-identity-and-auth.html` (pager "Next" becomes a link)

- [ ] **Step 1: Contract.** Add `GET /admin/events` and `GET /tickets/{ticket_id}/documents`; add `provider_id` to `Event`, and `event` plus `verification_note` to `Ticket`; note on `GET /events` that filters arrive in M6 and that M4 cursors hold an offset; add the new problem codes. Update the "added beyond plan.md" sentence.
- [ ] **Step 2: Database design.** `tickets.verification_note`, the seeded `fakeprovider`, `ticket_documents` marked built in M4.
- [ ] **Step 3: README.** Status table (M4 done, M5 next); `docker compose up -d db mailpit minio`; the MinIO console at `http://localhost:9001`; storage variables; a "Try the ticket flow" walk-through (promote an admin, create and publish an event, register a ticket, upload, verify).
- [ ] **Step 4: Revision page.** Follow the module-page rules: copy M3's page structure and shared assets, base every claim on the code, include Screens with the Task 11 screenshots, Decisions (offset in a cursor, sniffing bytes, 404 versus 403, presigned links, the two storage endpoints, `docker run` in CI), the two "Explain it" answers as interview questions, Traps and fixes met during the build, and "Check it still works" commands. Mark M4 done and M5 next in the index. Check the page with headless Chrome in light, dark and 390 px.
- [ ] **Step 5: Final verification.** `uv run ruff check . && uv run ruff format --check . && uv run alembic check && uv run pytest` with MinIO up and `REQUIRE_STORAGE=1` → no skips. In `frontend/`: `npm test && npm run typecheck && npm run lint && npm run format:check && npm run build && npm run e2e` → all pass. The attribution grep over the staged diff → no hits.
- [ ] **Step 6: Squash and push.** `git reset --soft c8ed14b` (the last pushed commit; this folds the design doc, the plan and every `M4 wip` commit together), then `git commit` with title `M4: Events, tickets and document storage` and a body listing what it built. `git push origin main`.
- [ ] **Step 7: Watch CI.** `gh run watch <id> --exit-status` → all three jobs pass, and the `test` log shows the storage tests ran rather than skipped.
