-- Constraint checks for docs/schema-draft.sql: each numbered step should fail (or match 0 rows)
-- with the constraint named in its message. Predict the result before you run it.
--
--   docker compose exec -T db createdb -U ticketloop schema_check
--   docker compose exec -T db psql -U ticketloop -d schema_check -v ON_ERROR_STOP=1 -q -f - < docs/schema-draft.sql
--   docker compose exec -T db psql -U ticketloop -d schema_check -q -f - < docs/schema-checks.sql
--   docker compose exec -T db dropdb -U ticketloop schema_check
\set ON_ERROR_STOP off
\set u1 '''00000000-0000-0000-0000-000000000001'''
\set u2 '''00000000-0000-0000-0000-000000000002'''
INSERT INTO users (id,email,password_hash,full_name) VALUES (:u1,'s@x.in','h','Seller'),(:u2,'b@x.in','h','Buyer');
INSERT INTO ticket_providers (id,code,name) VALUES ('00000000-0000-0000-0000-0000000000aa','fakeprovider','Fake');
INSERT INTO events (id,name,category,venue,city,starts_at,organizer,provider_id) VALUES ('00000000-0000-0000-0000-0000000000e1','Show','CONCERT','V','Kochi',now()+interval '30 days','Org','00000000-0000-0000-0000-0000000000aa');
INSERT INTO tickets (id,event_id,original_owner_id,current_owner_id,ticket_category,original_price_paise,purchase_reference,provider_reference) VALUES ('00000000-0000-0000-0000-0000000000c1','00000000-0000-0000-0000-0000000000e1',:u1,:u1,'GENERAL',200000,'p','r1');
\echo '1. same provider_reference twice (expect uq_tickets_event_provider_reference)'
INSERT INTO tickets (event_id,original_owner_id,current_owner_id,ticket_category,original_price_paise,purchase_reference,provider_reference) VALUES ('00000000-0000-0000-0000-0000000000e1',:u2,:u2,'GENERAL',200000,'p','r1');
\echo '2. price above original (expect ck_listings_price_cap)'
INSERT INTO listings (ticket_id,event_id,seller_id,asking_price_paise,original_price_paise,expires_at) VALUES ('00000000-0000-0000-0000-0000000000c1','00000000-0000-0000-0000-0000000000e1',:u1,250000,200000,now()+interval '1 day');
INSERT INTO listings (id,ticket_id,event_id,seller_id,asking_price_paise,original_price_paise,status,expires_at) VALUES ('00000000-0000-0000-0000-0000000000d1','00000000-0000-0000-0000-0000000000c1','00000000-0000-0000-0000-0000000000e1',:u1,150000,200000,'ACTIVE',now()+interval '1 day');
\echo '3. second open listing for same ticket (expect uq_listings_one_open_per_ticket)'
INSERT INTO listings (ticket_id,event_id,seller_id,asking_price_paise,original_price_paise,expires_at) VALUES ('00000000-0000-0000-0000-0000000000c1','00000000-0000-0000-0000-0000000000e1',:u1,140000,200000,now()+interval '1 day');
\echo '4. the conditional UPDATE: first claim wins, second matches 0 rows'
UPDATE listings SET status='RESERVED' WHERE id='00000000-0000-0000-0000-0000000000d1' AND status='ACTIVE' RETURNING 'claimed' AS ok_first;
UPDATE listings SET status='RESERVED' WHERE id='00000000-0000-0000-0000-0000000000d1' AND status='ACTIVE' RETURNING 'claimed' AS ok_second;
INSERT INTO reservations (id,listing_id,buyer_id,expires_at) VALUES ('00000000-0000-0000-0000-0000000000f1','00000000-0000-0000-0000-0000000000d1',:u2,now()+interval '10 minutes');
\echo '5. second live reservation on one listing (expect uq_reservations_one_live_per_listing)'
INSERT INTO reservations (listing_id,buyer_id,expires_at) VALUES ('00000000-0000-0000-0000-0000000000d1',:u1,now()+interval '10 minutes');
\echo '6. seller buys own listing (expect ck_orders_not_own_listing)'
INSERT INTO orders (reservation_id,listing_id,buyer_id,seller_id,amount_paise,idempotency_key) VALUES ('00000000-0000-0000-0000-0000000000f1','00000000-0000-0000-0000-0000000000d1',:u1,:u1,150000,'k1');
INSERT INTO orders (reservation_id,listing_id,buyer_id,seller_id,amount_paise,idempotency_key) VALUES ('00000000-0000-0000-0000-0000000000f1','00000000-0000-0000-0000-0000000000d1',:u2,:u1,150000,'k1');
\echo '7. second order for the same reservation (expect uq_orders_reservation)'
INSERT INTO orders (reservation_id,listing_id,buyer_id,seller_id,amount_paise,idempotency_key) VALUES ('00000000-0000-0000-0000-0000000000f1','00000000-0000-0000-0000-0000000000d1',:u2,:u1,150000,'k2');
\echo '8. duplicate webhook event: ON CONFLICT DO NOTHING returns no row the second time'
INSERT INTO payment_events (provider,provider_event_id,event_type,payload) VALUES ('fakepay','evt_1','payment.succeeded','{}') ON CONFLICT (provider,provider_event_id) DO NOTHING RETURNING 'inserted' AS ok_first_delivery;
INSERT INTO payment_events (provider,provider_event_id,event_type,payload) VALUES ('fakepay','evt_1','payment.succeeded','{}') ON CONFLICT (provider,provider_event_id) DO NOTHING RETURNING 'inserted' AS ok_second_delivery;
\echo '9. editing the audit log (expect append-only exception)'
INSERT INTO audit_logs (entity_type,entity_id,action) VALUES ('listing','00000000-0000-0000-0000-0000000000d1','listing.reserved');
UPDATE audit_logs SET action='tampered';
DELETE FROM audit_logs;
\echo '10. status outside the allowed set (expect ck_listings_status)'
UPDATE listings SET status='GONE' WHERE id='00000000-0000-0000-0000-0000000000d1';
