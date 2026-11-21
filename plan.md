# TicketLoop
## Distributed Peer-to-Peer Ticket Exchange Platform

**Project Type:** Backend Engineering / Distributed Systems  
**Document Version:** 1.0  
**Status:** Planning  
**Primary Objective:** Build a production-inspired ticket exchange platform demonstrating backend engineering, database concurrency, payment reliability, asynchronous processing, scalability and observability.

---

# 1. Project Overview

## 1.1 Problem Statement

People frequently purchase tickets for events, concerts, sports matches and other activities but become unable to attend due to unexpected circumstances.

Currently, recovering the money spent on such tickets can be difficult.

At the same time, other people may be looking for tickets to sold-out events.

There is a gap between people who have tickets they cannot use and people who want to attend those events.

TicketLoop aims to bridge this gap through a verified peer-to-peer ticket exchange marketplace.

Ticket holders can list eligible tickets, interested buyers can discover them, and the platform facilitates secure reservations, payments and ownership transfers.

## 1.2 Vision

Build a reliable and scalable secondary ticket marketplace where:

- Sellers can list tickets they legitimately own.
- Buyers can discover available tickets.
- Listings are verified to reduce fraudulent sales.
- Multiple interested buyers can register their interest.
- Ticket allocation is handled fairly.
- Payments and ticket transfers are processed reliably.
- Concurrent requests cannot result in duplicate purchases.
- Failures can be recovered without compromising transaction correctness.

## 1.3 Core Product Principle

TicketLoop is an exchange platform, not a ticket-scalping platform.

The initial marketplace will use fixed-price listings.

Sellers cannot arbitrarily increase prices through competitive bidding. The platform will enforce applicable resale restrictions and ticket-provider terms.

A platform fee, if introduced, must be transparently disclosed and separately accounted for.

The initial version will support only ticket categories and providers that explicitly permit the proposed transfer mechanism.

Railway tickets and other non-transferable tickets are outside the product scope.

---

# 2. Project Objectives

The project has two objectives.

## 2.1 Product Objectives

1. Enable users to list eligible tickets.
2. Allow buyers to search and discover listings.
3. Verify ticket ownership and listing eligibility.
4. Support fixed-price ticket purchases.
5. Allow multiple buyers to express interest in a listing.
6. Implement temporary ticket reservations.
7. Integrate a payment processing workflow.
8. Facilitate ticket ownership transfers.
9. Maintain transaction history and audit records.
10. Handle disputes and failed transactions.

## 2.2 Engineering Objectives

The primary engineering objective is to demonstrate how a backend system maintains correctness and reliability under concurrent access and partial failures.

The project should demonstrate:

- ACID transactions.
- Database locking and concurrency control.
- Race-condition prevention.
- Idempotent API design.
- Reliable event-driven processing.
- Transactional outbox pattern.
- Asynchronous task execution.
- Payment reconciliation.
- Caching and rate limiting.
- Performance optimization.
- Containerization and CI/CD.
- Monitoring, logging and distributed tracing.
- Automated integration and load testing.

---

# 3. Assumed System Scale

These are engineering targets for simulation and testing, not claims of actual production usage.

| Parameter | Target |
|---|---:|
| Registered users | 100,000 |
| Concurrent users during peak traffic | 10,000 |
| Active listings | 10,000 |
| Tickets for a popular event | 1,000 |
| Simultaneous requests for one ticket | 100 |
| Target API availability | 99.9% |
| Initial reservation duration | 10 minutes |
| Initial target API p95 latency | Under 300 ms for normal read endpoints |

The system should be tested at progressively increasing traffic levels.

Performance targets will be revised based on actual hardware, deployment configuration and test results.

---

# 4. User Roles

## 4.1 Guest

Can:
- Browse public events.
- Search listings.
- View listing information.

Cannot:
- Create listings.
- Express interest.
- Reserve tickets.
- Purchase tickets.

## 4.2 Registered User

Can:
- Browse events.
- Express interest in listings.
- Purchase eligible tickets.
- View orders.
- Track ticket transfers.
- View transaction history.

## 4.3 Seller

A registered user who owns an eligible ticket.

Can:
- Create listings.
- Upload purchase evidence.
- Set resale price within applicable limits.
- Manage active listings.
- Cancel eligible listings.
- Track reservations and transfers.

## 4.4 Administrator

Can:
- Review flagged listings.
- Manage users.
- Suspend fraudulent accounts.
- Review disputes.
- Inspect transaction history.
- Manage event and provider information.
- View audit records.

Administrative actions must be permission-controlled and audited.

---

# 5. Functional Requirements

## FR-01: Authentication and Authorization

The system must support:

- User registration.
- Login and logout.
- Password hashing.
- Access-token authentication.
- Refresh-token rotation.
- Role-based access control.
- Account suspension.
- Basic account recovery.

Security requirements:

- Passwords must never be stored in plaintext.
- Protected APIs must validate user identity.
- Users must only access their own orders and private information.
- Administrative endpoints must require appropriate permissions.

## FR-02: Event Management

The system must maintain event information.

Event attributes:

- Event ID.
- Event name.
- Description.
- Event category.
- Venue.
- City.
- Event date.
- Event status.
- Organizer.
- Ticket provider.

Event categories initially include:

- Concerts.
- Sports.
- College events.
- Other eligible entertainment events.

Administrators can create and manage event information.

## FR-03: Ticket Management

A ticket represents an individual entitlement to attend an event.

Ticket attributes:

- Ticket ID.
- Original owner ID.
- Event ID.
- Ticket category.
- Seat or section information.
- Original purchase price.
- Purchase reference.
- Provider reference.
- Verification status.
- Transfer eligibility.
- Current lifecycle status.

Ticket documents must be stored securely.

Do not expose QR codes, barcodes or original ticket documents publicly.

A screenshot or uploaded QR code must not be treated as proof of current ticket validity.

Real verification should eventually use provider APIs or authorized transfer mechanisms.

## FR-04: Listing Management

Sellers can list eligible tickets.

Listing attributes:

- Listing ID.
- Ticket ID.
- Seller ID.
- Asking price.
- Original price.
- Listing status.
- Created timestamp.
- Expiration timestamp.
- Verification status.

Listing lifecycle:

DRAFT → PENDING_VERIFICATION → ACTIVE → RESERVED → SOLD

Alternative transitions:

- PENDING_VERIFICATION → REJECTED
- ACTIVE → CANCELLED
- ACTIVE → EXPIRED
- RESERVED → ACTIVE after reservation expiry
- RESERVED → SOLD after successful purchase and transfer workflow

Rules:

1. Only verified and eligible tickets can be listed.
2. One ticket cannot have multiple active listings.
3. A reserved ticket cannot be purchased by another buyer.
4. A seller cannot purchase their own listing.
5. A listing cannot be cancelled while an irreversible transfer is in progress.
6. Price changes must follow applicable resale restrictions.
7. Every important listing state change must be recorded.

## FR-05: Marketplace Discovery

Buyers must be able to:

- Browse events.
- Search by event name.
- Filter by event category.
- Filter by location.
- Filter by price.
- Filter by ticket category.
- Sort by listing date and price.
- View listing details.

Pagination is mandatory.

Avoid loading all listings into memory.

Search endpoints should support indexed queries.

## FR-06: Interest Registration

A buyer can express interest in an available listing.

An interest record contains:

- Interest ID.
- Buyer ID.
- Listing ID.
- Registration timestamp.
- Interest status.

Interest statuses:

- ACTIVE
- SELECTED
- EXPIRED
- WITHDRAWN
- REJECTED

A buyer cannot register multiple active interests for the same listing.

Allocation strategies:

1. First-come-first-served.
2. Random selection among eligible interested buyers.
3. Seller-selected buyer, subject to platform rules.

Monetary bidding is excluded from the initial implementation.

## FR-07: Ticket Reservation

When a buyer is selected, the platform temporarily reserves the ticket.

Initial reservation duration: 10 minutes.

Reservation attributes:

- Reservation ID.
- Listing ID.
- Buyer ID.
- Expiration timestamp.
- Reservation status.
- Created timestamp.

Reservation lifecycle:

CREATED → PAYMENT_PENDING → CONFIRMED

Alternative transitions:

- CREATED → EXPIRED
- PAYMENT_PENDING → EXPIRED, only when safe to release
- PAYMENT_PENDING → FAILED

Important requirement:

A reservation expiry worker must never release a ticket that has already been successfully paid for or whose payment is still unresolved.

The database must be the source of truth for inventory availability.

## FR-08: Order Management

Orders represent a buyer's purchase attempt.

Order attributes:

- Order ID.
- Buyer ID.
- Seller ID.
- Listing ID.
- Reservation ID.
- Amount.
- Currency.
- Order status.
- Idempotency key.
- Created timestamp.
- Updated timestamp.

Order lifecycle:

CREATED → PAYMENT_PENDING → PAID → TRANSFER_PENDING → COMPLETED

Alternative states:

- PAYMENT_PENDING → PAYMENT_FAILED
- PAYMENT_PENDING → CANCELLED
- PAID → REFUND_PENDING
- REFUND_PENDING → REFUNDED
- TRANSFER_PENDING → TRANSFER_FAILED

Only valid transitions must be accepted.

A state machine should govern transitions rather than allowing arbitrary status updates.

## FR-09: Payment Processing

Implement a simulated payment gateway initially.

The payment subsystem must support:

- Payment intent creation.
- Payment initiation.
- Successful payment simulation.
- Failed payment simulation.
- Delayed payment responses.
- Duplicate webhooks.
- Invalid webhook signatures.
- Payment timeout.
- Payment reconciliation.
- Refund simulation.

Payment attributes:

- Payment ID.
- Order ID.
- Provider transaction ID.
- Amount.
- Currency.
- Payment status.
- Idempotency key.
- Provider response.
- Created timestamp.

Critical requirements:

1. Payment amounts must be verified server-side.
2. Payment callbacks must be authenticated.
3. Duplicate webhook events must not duplicate financial operations.
4. A payment must be associated with exactly one intended order.
5. Payment state changes must be persisted.
6. Failed workflows must be recoverable.
7. Payment records must never be silently overwritten.

For the future Razorpay integration, use sandbox credentials and the provider's documented payment and webhook workflows.

## FR-10: Ticket Transfer

After successful payment, the platform initiates ticket transfer.

Initial implementation:

- Simulate the external provider.
- Generate a transfer request.
- Simulate successful or failed transfer.
- Record transfer status.
- Notify the buyer and seller.

Future implementation:

Integrate with authorized ticket-provider APIs.

Transfer statuses:

- INITIATED
- PROCESSING
- COMPLETED
- FAILED
- REQUIRES_MANUAL_REVIEW

A paid order must not automatically be marked COMPLETED before the required transfer has succeeded.

## FR-11: Notifications

Users should receive notifications for:

- Listing verification.
- New reservation.
- Successful payment.
- Failed payment.
- Reservation expiry.
- Ticket transfer.
- Listing cancellation.
- Dispute updates.

Initially support in-app notifications.

Email and WebSocket notifications can be added later.

## FR-12: Audit and Dispute Management

Maintain audit logs for:

- Listing creation and modification.
- Reservation creation and expiry.
- Order creation.
- Payment state changes.
- Ticket transfer.
- Refunds.
- Administrative actions.

Disputes should support:

- Dispute creation.
- Reason selection.
- Evidence upload.
- Administrative review.
- Resolution status.
- Resolution history.

---

# 6. Non-Functional Requirements

## NFR-01: Consistency

The platform must prevent:

- Double booking.
- Duplicate active listings.
- Duplicate order creation.
- Duplicate payment processing.
- Invalid state transitions.

PostgreSQL is the source of truth for all transactional data.

## NFR-02: Availability

The application should recover from:

- Application worker crashes.
- Message broker interruptions.
- Delayed payment responses.
- Temporary database connection failures.
- Background worker failures.

## NFR-03: Performance

Initial targets:

- Normal read API p95 latency below 300 ms.
- Reservation API p95 latency below 500 ms under the defined test workload.
- No duplicate reservations under concurrent testing.
- Paginated listing queries should remain performant as records increase.

These are targets, not guaranteed performance characteristics.

## NFR-04: Security

Implement:

- Secure password hashing.
- JWT authentication.
- Role-based authorization.
- Input validation.
- SQL injection protection.
- Rate limiting.
- CORS restrictions.
- Secure document access.
- Payment webhook verification.
- Secrets management.
- Audit logging.

Never store payment card details.

## NFR-05: Maintainability

- Modular code structure.
- Clear separation of business logic and infrastructure.
- Dependency injection.
- Database migrations.
- Consistent error responses.
- Automated tests.
- API documentation.

## NFR-06: Observability

The system must expose:

- Application logs.
- Request latency.
- Request throughput.
- Error rates.
- Database connection metrics.
- Queue depth.
- Worker failures.
- Payment processing failures.
- Reservation expiry metrics.

---

# 7. Proposed Architecture

## 7.1 Architectural Approach

Start with a modular monolith.

All core business modules initially reside in one FastAPI application but maintain strict boundaries.

The objective is to avoid premature microservice complexity while preserving the ability to extract modules later.

## 7.2 Technology Stack

| Layer | Technology |
|---|---|
| Frontend | React + TypeScript |
| Backend | Python + FastAPI |
| Database | PostgreSQL |
| ORM | SQLAlchemy |
| Migrations | Alembic |
| Cache | Redis |
| Message broker | RabbitMQ |
| Background workers | Celery |
| Object storage | MinIO / S3 |
| Testing | Pytest |
| Load testing | Locust or k6 |
| Containers | Docker |
| Orchestration | Docker Compose |
| CI/CD | GitHub Actions |
| Metrics | Prometheus |
| Dashboards | Grafana |
| Tracing | OpenTelemetry |

## 7.3 Logical Modules

### Identity Module
Authentication, users, permissions and account management.

### Event Module
Event metadata, venues, categories and organizers.

### Ticket Module
Ticket inventory, eligibility and ownership records.

### Listing Module
Marketplace listings, verification and pricing.

### Discovery Module
Search, filters, pagination and caching.

### Reservation Module
Temporary inventory allocation and expiry.

### Order Module
Order creation and lifecycle management.

### Payment Module
Payment intents, callbacks, reconciliation and refunds.

### Transfer Module
External ticket-transfer workflow.

### Notification Module
Asynchronous notifications.

### Audit Module
Immutable-style transaction history and administrative records.

---

# 8. Database Design

## 8.1 Core Tables

### users
- id (UUID, PK)
- email (UNIQUE)
- password_hash
- full_name
- role
- account_status
- created_at
- updated_at

### events
- id (UUID, PK)
- name
- description
- category
- venue
- city
- event_date
- organizer_id
- provider_id
- status
- created_at

### tickets
- id (UUID, PK)
- event_id (FK)
- original_owner_id (FK)
- provider_reference
- ticket_category
- seat_details
- original_price
- verification_status
- transfer_eligible
- status
- created_at

### listings
- id (UUID, PK)
- ticket_id (FK)
- seller_id (FK)
- asking_price
- status
- expires_at
- created_at
- updated_at

Enforce a partial unique index to prevent multiple active listings for the same ticket.

### interests
- id (UUID, PK)
- listing_id (FK)
- buyer_id (FK)
- status
- created_at

### reservations
- id (UUID, PK)
- listing_id (FK)
- buyer_id (FK)
- status
- expires_at
- created_at

### orders
- id (UUID, PK)
- reservation_id (FK)
- buyer_id (FK)
- seller_id (FK)
- amount
- currency
- status
- idempotency_key
- created_at
- updated_at

### payments
- id (UUID, PK)
- order_id (FK)
- provider_transaction_id
- amount
- currency
- status
- created_at
- updated_at

### payment_events
- id (UUID, PK)
- provider_event_id (UNIQUE)
- payment_id (FK)
- event_type
- processing_status
- received_at
- processed_at

### transfers
- id (UUID, PK)
- order_id (FK)
- ticket_id (FK)
- provider_transfer_reference
- status
- initiated_at
- completed_at

### outbox_events
- id (UUID, PK)
- aggregate_type
- aggregate_id
- event_type
- payload
- created_at
- published_at
- retry_count

### notifications
- id (UUID, PK)
- user_id (FK)
- notification_type
- payload
- read_at
- created_at

### audit_logs
- id (UUID, PK)
- actor_id
- entity_type
- entity_id
- action
- metadata
- created_at

## 8.2 Database Design Principles

- Use UUID identifiers for externally exposed resources.
- Use foreign keys.
- Add indexes to frequently queried columns.
- Use unique constraints for idempotency.
- Use database transactions for state changes.
- Store timestamps in UTC.
- Use decimal or integer minor currency units instead of floating-point monetary values.
- Avoid storing sensitive ticket QR data in publicly accessible fields.
- Use migrations for every schema change.

Important indexes:

- listings(event_id, status)
- listings(seller_id, status)
- reservations(listing_id, status)
- orders(buyer_id, created_at)
- payments(order_id)
- payment_events(provider_event_id)
- interests(listing_id, created_at)

The actual index strategy should be validated using EXPLAIN ANALYZE.

---

# 9. Critical Engineering Workflows

## 9.1 Concurrent Ticket Reservation

Scenario:

Two buyers attempt to reserve the same ticket simultaneously.

Expected behaviour:

Only one buyer should successfully reserve it.

Proposed implementation:

1. Buyer sends a reservation request.
2. Application begins a database transaction.
3. Execute an atomic conditional UPDATE.
4. Verify exactly one row was updated.
5. Create reservation record.
6. Update listing state.
7. Commit transaction.
8. Return reservation details.

Example:

UPDATE tickets
SET status = 'RESERVED'
WHERE id = :ticket_id
AND status = 'AVAILABLE'
RETURNING id;

In the actual schema, ensure ticket and listing state transitions are coordinated transactionally.

The implementation must be tested using concurrent requests.

## 9.2 Payment Workflow

1. Buyer creates a reservation.
2. Order is created.
3. Payment intent is generated.
4. Simulated gateway processes payment.
5. Gateway sends a webhook.
6. Backend verifies the webhook.
7. Duplicate event detection is performed.
8. Payment and order state are updated transactionally.
9. An outbox event is created.
10. Background publisher sends the event to RabbitMQ.
11. Transfer worker processes the transfer.
12. Buyer and seller receive notifications.

## 9.3 Transactional Outbox

Problem:

The database transaction succeeds, but publishing an event to RabbitMQ fails.

Solution:

Persist the business update and outbox event in the same PostgreSQL transaction.

A background publisher:

1. Reads unpublished events.
2. Publishes to RabbitMQ.
3. Waits for broker confirmation.
4. Marks successfully published events.
5. Retries failed publishing operations.

Consumers must be idempotent because messages can be delivered more than once.

## 9.4 Reservation Expiry

A background worker periodically identifies expired reservations.

Before releasing a ticket:

1. Verify reservation expiry.
2. Verify current reservation status.
3. Check whether payment is pending or confirmed.
4. Check whether an unresolved payment attempt exists.
5. Perform a conditional state transition.
6. Release inventory only when safe.

Use conditional updates and transactions to prevent stale workers from modifying newer states.

## 9.5 Payment Reconciliation

A scheduled reconciliation job compares:

- Local payment records.
- Gateway payment records.
- Order states.

It identifies inconsistencies such as:

- Gateway reports success, application reports pending.
- Gateway reports failure, application reports pending.
- Local payment exists without an associated completed order.
- Refund initiated but not confirmed.

The reconciliation process must be idempotent and produce an auditable record.

---

# 10. API Design

Use REST APIs initially.

Base path: /api/v1

## Authentication

POST /auth/register

POST /auth/login

POST /auth/refresh

POST /auth/logout

## Users

GET /users/me

PATCH /users/me

## Events

GET /events

GET /events/{event_id}

GET /events/{event_id}/listings

## Tickets

POST /tickets

GET /tickets/{ticket_id}

GET /tickets/me

## Listings

POST /listings

GET /listings

GET /listings/{listing_id}

PATCH /listings/{listing_id}

DELETE /listings/{listing_id}

## Interests

POST /listings/{listing_id}/interests

GET /listings/{listing_id}/interests

DELETE /interests/{interest_id}

## Reservations

POST /listings/{listing_id}/reservations

GET /reservations/{reservation_id}

POST /reservations/{reservation_id}/cancel

## Orders

POST /orders

GET /orders/{order_id}

GET /orders/me

## Payments

POST /payments/intents

POST /payments/webhooks/{provider}

GET /payments/{payment_id}

## Transfers

GET /transfers/{transfer_id}

POST /transfers/{transfer_id}/retry

The retry endpoint must be restricted to authorized operations and safe retryable states.

## Notifications

GET /notifications

PATCH /notifications/{notification_id}/read

## Administration

GET /admin/listings/pending

POST /admin/listings/{listing_id}/approve

POST /admin/listings/{listing_id}/reject

GET /admin/disputes

All endpoints must implement authorization, validation, pagination and consistent error responses.

---

# 11. Caching Strategy

Redis should be introduced only after the basic application works.

Potential cache candidates:

- Event information.
- Public listing search results.
- Popular event metadata.
- Frequently accessed public information.

Do not treat Redis cache data as authoritative inventory state.

Cache invalidation should occur when listings change state.

Use TTLs and cache-aside patterns.

## Rate Limiting

Implement rate limits for:

- Login attempts.
- Listing creation.
- Reservation attempts.
- Payment initiation.
- Search endpoints.

Use Redis-backed counters or a suitable rate-limiting algorithm.

---

# 12. Reliability and Failure Handling

The project must deliberately simulate failures.

| Failure scenario | Expected recovery |
|---|---|
| Backend crashes after order creation | Order remains recoverable |
| Payment succeeds but webhook is delayed | Reconciliation detects success |
| Duplicate webhook arrives | No duplicate processing |
| RabbitMQ becomes unavailable | Outbox events remain pending |
| Worker crashes during processing | Message is retried safely |
| Reservation expires | Inventory released only if safe |
| Database connection drops | Transaction rollback and controlled retry |
| Ticket transfer fails | Order enters recoverable failure state |
| Duplicate purchase request | Idempotency prevents duplicate orders |

Do not assume that distributed systems provide exactly-once message delivery.

Design around at-least-once delivery and idempotent processing.

---

# 13. Testing Strategy

Testing is a major deliverable, not an afterthought.

## Unit Tests

Test:

- Price validation.
- Listing eligibility.
- State transitions.
- Reservation expiry.
- Payment state transitions.
- Authorization rules.

## Integration Tests

Test:

- API and database interaction.
- Transaction rollback.
- Unique constraints.
- Payment webhook handling.
- Outbox publishing.
- RabbitMQ consumers.
- Redis integration.

## Concurrency Tests

Test:

1. 100 buyers attempting to reserve one ticket.
2. Multiple requests using the same idempotency key.
3. Duplicate webhook deliveries.
4. Reservation expiry racing with payment success.
5. Concurrent listing cancellation and reservation.

Expected outcomes must be asserted against the actual database state.

## Failure Injection Tests

Simulate:

- Application termination.
- Database connection failures.
- RabbitMQ outages.
- Worker termination.
- Delayed webhooks.
- Duplicate events.
- Payment timeouts.

## Load Testing

Use Locust or k6.

Test stages:

- 100 concurrent users.
- 500 concurrent users.
- 1,000 concurrent users.
- 5,000 concurrent users.
- 10,000 concurrent users.

Measure:

- Requests per second.
- p50 latency.
- p95 latency.
- p99 latency.
- Error percentage.
- Database connections.
- CPU and memory usage.
- Redis hit ratio.
- Queue processing delay.

Record the hardware and deployment configuration used for each test.

---

# 14. Observability

## Metrics

Expose:

- HTTP request count.
- HTTP request duration.
- HTTP error count.
- Reservation success rate.
- Reservation conflict count.
- Payment success and failure count.
- Webhook processing latency.
- Outbox backlog.
- RabbitMQ queue depth.
- Worker retry count.
- Database connection utilization.

## Logging

Use structured JSON logs.

Every important workflow should include:

- Request ID.
- Correlation ID.
- Order ID.
- Reservation ID.
- Event type.
- Operation status.

Never log passwords, access tokens, payment secrets or full ticket credentials.

## Distributed Tracing

Integrate OpenTelemetry.

Trace important operations across:

API → Database → Outbox → RabbitMQ → Worker → Transfer Provider

The objective is to identify where latency or failure occurs.

---

# 15. Security Considerations

- Use Argon2id or another appropriately configured password hashing algorithm.
- Enforce authorization at the resource level.
- Validate all incoming requests.
- Verify webhook signatures.
- Protect against replayed webhook events.
- Use secure document storage.
- Avoid exposing ticket QR codes.
- Use HTTPS in deployment.
- Store secrets in environment-specific secret management.
- Implement rate limiting.
- Protect against IDOR vulnerabilities.
- Maintain audit trails.
- Never trust client-provided payment amounts or ticket ownership information.

Payment processing and settlement must use an appropriately authorized provider and comply with applicable Indian payment regulations.

---

# 16. Deployment Architecture

Initial deployment should use Docker Compose.

Containers:

1. FastAPI application.
2. PostgreSQL.
3. Redis.
4. RabbitMQ.
5. Celery worker.
6. Celery scheduler.
7. Prometheus.
8. Grafana.
9. MinIO.

Later, add:

- Nginx or another reverse proxy.
- Multiple API replicas.
- Multiple worker replicas.
- Managed PostgreSQL.
- Managed Redis.
- Production object storage.

Kubernetes is deliberately excluded from the initial scope.

It can be explored after the application demonstrates a real need for orchestration.

---

# 17. Development Roadmap

## Phase 0: Requirements and Design

Deliverables:

- Requirements document.
- Use-case diagrams.
- Entity relationship diagram.
- Database schema.
- State machine diagrams.
- API contract.
- Architecture diagram.

Exit criteria:

Core workflows and failure scenarios are understood before implementation.

## Phase 1: Backend Foundation

Implement:

- FastAPI project setup.
- PostgreSQL connection.
- SQLAlchemy models.
- Alembic migrations.
- Authentication.
- Authorization.
- User management.
- Event CRUD.
- Ticket CRUD.

Exit criteria:

Core APIs work with integration tests.

## Phase 2: Marketplace

Implement:

- Listing creation.
- Ticket verification workflow.
- Listing discovery.
- Search and pagination.
- Interest registration.
- Listing lifecycle.

Exit criteria:

Users can list and discover eligible tickets.

## Phase 3: Reservation and Concurrency

Implement:

- Reservation API.
- Atomic ticket allocation.
- Database locking.
- Reservation expiry.
- State transition validation.
- Concurrent request tests.

Exit criteria:

100 concurrent buyers attempting to reserve one ticket result in exactly one successful reservation.

## Phase 4: Orders and Payments

Implement:

- Order creation.
- Idempotency.
- Payment intent.
- Simulated gateway.
- Webhook processing.
- Duplicate event detection.
- Payment reconciliation.
- Refund simulation.

Exit criteria:

Payment workflows recover correctly from delayed, duplicated and failed events.

## Phase 5: Messaging and Asynchronous Processing

Implement:

- RabbitMQ.
- Celery workers.
- Transactional outbox.
- Retry policies.
- Dead-letter queues.
- Notification workers.

Exit criteria:

Business events are processed reliably despite broker and worker failures.

## Phase 6: Performance Engineering

Implement:

- Redis caching.
- Rate limiting.
- Database indexes.
- Query optimization.
- Connection pooling.
- Load tests.

Exit criteria:

Performance metrics are recorded and bottlenecks are documented.

## Phase 7: Production Readiness

Implement:

- Docker Compose deployment.
- CI/CD.
- Structured logging.
- Prometheus.
- Grafana.
- OpenTelemetry.
- Security testing.
- Failure injection.

Exit criteria:

The system can be deployed reproducibly, monitored and debugged.

---

# 18. Stretch Goals

These should only be attempted after the core system is reliable.

1. Extract payment processing into an independent service.
2. Extract notification processing.
3. Introduce a dedicated search engine.
4. Implement WebSocket-based real-time listing updates.
5. Implement a transparent demand-allocation queue.
6. Add provider-specific ticket transfer integrations.
7. Implement an administrative fraud-review dashboard.
8. Experiment with event sourcing for selected transaction histories.
9. Introduce Kubernetes and autoscaling.
10. Experiment with multi-region read replicas.

Do not implement these simply to increase the technology count.

Each architectural addition must solve an identifiable problem.

---

# 19. Repository Structure

Proposed backend structure:

ticketloop/
│
├── app/
│   ├── main.py
│   ├── core/
│   │   ├── config.py
│   │   ├── security.py
│   │   ├── logging.py
│   │   └── exceptions.py
│   │
│   ├── modules/
│   │   ├── identity/
│   │   ├── events/
│   │   ├── tickets/
│   │   ├── listings/
│   │   ├── discovery/
│   │   ├── reservations/
│   │   ├── orders/
│   │   ├── payments/
│   │   ├── transfers/
│   │   ├── notifications/
│   │   └── audit/
│   │
│   ├── infrastructure/
│   │   ├── database/
│   │   ├── redis/
│   │   ├── messaging/
│   │   ├── storage/
│   │   └── external_providers/
│   │
│   └── workers/
│
├── migrations/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── concurrency/
│   ├── failure/
│   └── load/
│
├── scripts/
├── docker/
├── docs/
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── .env.example
└── README.md

The frontend should live in a separate directory or repository.

---

# 20. Definition of Done

The project should not be considered complete merely because all CRUD endpoints work.

The following criteria define a meaningful completion.

### Functional

- Users can register and authenticate.
- Eligible tickets can be listed.
- Buyers can discover listings.
- Buyers can express interest.
- Reservations can be created and expired.
- Orders can be created and tracked.
- Payments can be simulated and reconciled.
- Ticket transfers can be simulated.
- Users can inspect their transaction history.

### Engineering

- Concurrent purchases cannot result in double booking.
- Duplicate requests do not create duplicate orders.
- Duplicate webhooks do not cause duplicate payment processing.
- Outbox events survive broker outages.
- Failed background jobs can be retried safely.
- Reservation expiry cannot release paid inventory.
- Important workflows have automated tests.

### Operations

- Application can be deployed with Docker Compose.
- CI runs tests.
- Logs are structured.
- Metrics are exposed.
- Dashboards are available.
- Load-test reports are documented.

---

# 21. Resume Positioning

Potential project description:

**TicketLoop — Distributed Peer-to-Peer Ticket Exchange Platform**

Python, FastAPI, PostgreSQL, Redis, RabbitMQ, Celery, Docker, Prometheus, Grafana.

Potential resume bullets after implementation:

- Engineered a concurrency-safe ticket reservation system using atomic PostgreSQL transactions, preventing duplicate allocations under simultaneous purchase requests.
- Implemented idempotent payment workflows and webhook deduplication to handle duplicate and delayed payment events.
- Designed an asynchronous event-processing pipeline using RabbitMQ and the transactional outbox pattern to ensure reliable business-event publishing.
- Implemented reservation expiry, payment reconciliation and retryable background workflows to recover from partial failures.
- Evaluated backend performance through concurrent load testing, measuring throughput, p95 latency and database utilization.

Only include measured performance numbers and genuinely implemented capabilities.

---

# 22. Learning and Documentation Deliverables

For every major engineering feature, maintain a corresponding technical note.

Suggested documents:

1. `requirements.md` — Functional and non-functional requirements.
2. `architecture.md` — Architecture and component responsibilities.
3. `database-design.md` — ER diagram, indexes and constraints.
4. `reservation-design.md` — Concurrency and locking decisions.
5. `payment-design.md` — Payment state machine and failure handling.
6. `messaging-design.md` — RabbitMQ and transactional outbox.
7. `testing-strategy.md` — Unit, integration and concurrency tests.
8. `load-test-results.md` — Actual benchmark results.
9. `deployment.md` — Docker and deployment instructions.
10. `architecture-decisions/` — ADRs explaining important technical choices.

For each major architectural decision, document:

- The problem.
- Possible solutions.
- Chosen solution.
- Alternatives rejected.
- Trade-offs.
- Evidence from testing.

---

# 23. Final Project Philosophy

The goal is not to build the largest possible application.

The goal is to build a system where the important engineering decisions are justified, implemented and tested.

A strong TicketLoop implementation should allow its developer to confidently explain:

- Why PostgreSQL is the source of truth.
- How concurrent requests are handled.
- How duplicate payments are prevented.
- How the application recovers from partial failures.
- Why asynchronous messaging is necessary.
- How the system behaves under high traffic.
- Where its architectural bottlenecks exist.
- What would need to change to support significantly more traffic.

**The final measure of success is not the number of technologies used. It is whether the developer understands the system deeply enough to defend every important design decision.**