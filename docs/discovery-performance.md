# Discovery performance

Written by `scripts/explain_discovery.py`; do not edit by hand. Data from `scripts/seed_discovery.py --reset`.

- **Data:** 2,000 events, 100,000 listings (79,825 ACTIVE).
- **Where:** PostgreSQL 17.12 (Debian 17.12-1.pgdg13+1) in Docker on Windows (Intel64 Family 6 Model 183 Stepping 1, GenuineIntel), default Postgres settings, warm cache.
- **Times:** `EXPLAIN (ANALYZE)` execution time, median of 15 runs (101 for the page comparison). A page is 20 rows.
- **Result:** PASS

## Page 1 against page 500

| Sort | Page 1 | Page 500, keyset | Page 500, `OFFSET` | Keyset vs page 1 | OFFSET vs page 1 |
| --- | --- | --- | --- | --- | --- |
| `price` | 0.17 ms | 0.18 ms | 44.57 ms | 1.04x | 254.7x |
| `-price` | 0.19 ms | 0.21 ms | 56.20 ms | 1.12x | 294.3x |
| `created_at` | 0.18 ms | 0.20 ms | 45.57 ms | 1.08x | 253.2x |
| `-created_at` | 0.20 ms | 0.21 ms | 52.32 ms | 1.09x | 268.3x |

## Every query

How each query reaches `listings` (and any M6 index it uses on `events` or `tickets`), its time without and with M6's indexes, and how many listings it reads to fill the page (at most 10,000 to pass). The query is the API's own, joins to `events` and `tickets` included.

| Query | Before | After | Listings read | Plan before | Plan after |
| --- | --- | --- | --- | --- | --- |
| no filter, sort price | 80.92 ms | 0.15 ms | 21 | Seq Scan | Index Scan using ix_listings_active_by_price |
| no filter, sort -price | 82.36 ms | 0.14 ms | 21 | Seq Scan | Index Scan using ix_listings_active_by_price |
| no filter, sort created_at | 80.78 ms | 0.16 ms | 21 | Seq Scan | Index Scan using ix_listings_active_by_created |
| no filter, sort -created_at | 80.50 ms | 0.17 ms | 21 | Seq Scan | Index Scan using ix_listings_active_by_created |
| q=live (common), sort price | 14.64 ms | 0.98 ms | 393 | Seq Scan | Index Scan using ix_listings_active_by_price |
| q=live (common), sort -created_at | 14.63 ms | 1.14 ms | 403 | Seq Scan | Index Scan using ix_listings_active_by_created |
| q=sharma cup (rare), sort price | 1.01 ms | 0.06 ms | 0 | Bitmap Index Scan using ix_listings_event | Bitmap Index Scan using ix_listings_event, Bitmap Index Scan using ix_events_name_trgm |
| q=sharma cup (rare), sort -created_at | 0.99 ms | 0.07 ms | 0 | Bitmap Index Scan using ix_listings_event | Bitmap Index Scan using ix_listings_event, Bitmap Index Scan using ix_events_name_trgm |
| q=no such event, sort price | 1.12 ms | 1.09 ms | 0 | Bitmap Index Scan using ix_listings_event | Bitmap Index Scan using ix_listings_event |
| q=no such event, sort -created_at | 1.08 ms | 1.08 ms | 0 | Bitmap Index Scan using ix_listings_event | Bitmap Index Scan using ix_listings_event |
| category=SPORTS, sort price | 27.30 ms | 0.30 ms | 67 | Seq Scan | Index Scan using ix_listings_active_by_price |
| category=SPORTS, sort -created_at | 24.26 ms | 0.30 ms | 75 | Seq Scan | Index Scan using ix_listings_active_by_created |
| city=Mumbai (20%), sort price | 59.04 ms | 0.40 ms | 111 | Bitmap Index Scan using ix_listings_event | Index Scan using ix_listings_active_by_price |
| city=Mumbai (20%), sort -created_at | 60.96 ms | 0.39 ms | 101 | Bitmap Index Scan using ix_listings_event | Index Scan using ix_listings_active_by_created |
| city=Shillong (2%), sort price | 7.48 ms | 1.80 ms | 1,003 | Bitmap Index Scan using ix_listings_event | Index Scan using ix_listings_active_by_price |
| city=Shillong (2%), sort -created_at | 7.25 ms | 2.50 ms | 1,676 | Bitmap Index Scan using ix_listings_event | Index Scan using ix_listings_active_by_created |
| city=Bangalore (0%), sort price | 0.59 ms | 0.05 ms | 0 | Bitmap Index Scan using ix_listings_event | Bitmap Index Scan using ix_listings_event, Index Scan using ix_events_city |
| city=Bangalore (0%), sort -created_at | 0.59 ms | 0.05 ms | 0 | Bitmap Index Scan using ix_listings_event | Bitmap Index Scan using ix_listings_event, Index Scan using ix_events_city |
| ticket_category=BACKSTAGE (3%), sort price | 22.08 ms | 2.11 ms | 697 | Index Scan using uq_listings_one_open_per_ticket | Index Scan using ix_listings_active_by_price |
| ticket_category=BACKSTAGE (3%), sort -created_at | 21.04 ms | 2.39 ms | 802 | Index Scan using uq_listings_one_open_per_ticket | Index Scan using ix_listings_active_by_created |
| ticket_category=NONE (0%), sort price | 14.53 ms | 0.05 ms | 0 | Index Scan using uq_listings_one_open_per_ticket | Index Scan using uq_listings_one_open_per_ticket, Index Scan using ix_tickets_category |
| ticket_category=NONE (0%), sort -created_at | 14.33 ms | 0.05 ms | 0 | Index Scan using uq_listings_one_open_per_ticket | Index Scan using uq_listings_one_open_per_ticket, Index Scan using ix_tickets_category |
| min_price=15,000, sort price | 8.59 ms | 0.16 ms | 21 | Seq Scan | Index Scan using ix_listings_active_by_price |
| min_price=15,000, sort -created_at | 7.87 ms | 0.33 ms | 333 | Seq Scan | Index Scan using ix_listings_active_by_created |
| max_price=500, sort price | 6.83 ms | 0.16 ms | 21 | Seq Scan | Index Scan using ix_listings_active_by_price |
| max_price=500, sort -created_at | 6.77 ms | 0.67 ms | 1,078 | Seq Scan | Index Scan using ix_listings_active_by_created |
| price 5,000 to 6,000, sort price | 9.78 ms | 0.19 ms | 21 | Seq Scan | Index Scan using ix_listings_active_by_price |
| price 5,000 to 6,000, sort -created_at | 8.31 ms | 0.34 ms | 273 | Seq Scan | Index Scan using ix_listings_active_by_created |
| event_id, sort price | 0.28 ms | 0.26 ms | 52 | Bitmap Index Scan using ix_listings_event | Bitmap Index Scan using ix_listings_event |
| event_id, sort -created_at | 0.21 ms | 0.24 ms | 52 | Bitmap Index Scan using ix_listings_event | Bitmap Index Scan using ix_listings_event |
| q, city and price, sort price | 1.30 ms | 0.39 ms | 100 | Bitmap Index Scan using ix_listings_event | Bitmap Index Scan using ix_listings_event, Bitmap Index Scan using ix_events_city, Bitmap Index Scan using ix_events_name_trgm |
| q, city and price, sort -created_at | 1.36 ms | 0.40 ms | 100 | Bitmap Index Scan using ix_listings_event | Bitmap Index Scan using ix_listings_event, Bitmap Index Scan using ix_events_city, Bitmap Index Scan using ix_events_name_trgm |
| events, no filter | 1.01 ms | 0.03 ms | 0 | Seq Scan | Index Scan using ix_events_status_starts |
| events, q=cup | 1.02 ms | 0.27 ms | 0 | Seq Scan | Index Scan using ix_events_status_starts |
