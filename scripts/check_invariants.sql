-- The invariants (M12): the rules a double sale would break, checked on the rows themselves.
-- One row per violation, none when they all hold. `uv run python -m scripts.load_test` runs
-- it after every load test; tests/integration/test_invariants.py plants each violation and
-- checks it is found.

-- A ticket in two open listings could be sold twice.
SELECT 'two-open-listings' AS invariant,
       ticket_id::text AS subject,
       count(*) || ' open listings' AS detail
FROM listings
WHERE status IN ('DRAFT', 'PENDING_VERIFICATION', 'ACTIVE', 'RESERVED')
GROUP BY ticket_id
HAVING count(*) > 1

UNION ALL

-- Two buyers holding one listing.
SELECT 'two-live-reservations', listing_id::text, count(*) || ' live reservations'
FROM reservations
WHERE status IN ('CREATED', 'PAYMENT_PENDING', 'CONFIRMED')
GROUP BY listing_id
HAVING count(*) > 1

UNION ALL

-- Money taken for an order that does not know it was paid.
SELECT 'paid-order-not-paid', o.id::text, 'payment ' || p.id || ' SUCCEEDED, order ' || o.status
FROM payments AS p
JOIN orders AS o ON o.id = p.order_id
WHERE p.status = 'SUCCEEDED'
  AND o.status NOT IN ('PAID', 'TRANSFER_PENDING', 'COMPLETED', 'TRANSFER_FAILED')

UNION ALL

-- A finished sale whose ticket is not its buyer's. Only the latest completed order for a
-- ticket counts: a ticket bought, listed again and sold on belongs to its last buyer.
SELECT 'owner-is-not-buyer', t.id::text,
       'order ' || latest.id || ' completed for ' || latest.buyer_id
       || ', owned by ' || t.current_owner_id
FROM (
    SELECT DISTINCT ON (l.ticket_id) l.ticket_id, o.id, o.buyer_id
    FROM orders AS o
    JOIN listings AS l ON l.id = o.listing_id
    WHERE o.status = 'COMPLETED'
    ORDER BY l.ticket_id, o.updated_at DESC, o.id DESC
) AS latest
JOIN tickets AS t ON t.id = latest.ticket_id
WHERE t.current_owner_id <> latest.buyer_id

UNION ALL

-- The oversell itself: two buyers paid for one listing.
SELECT 'sold-twice', listing_id::text, count(*) || ' paid orders'
FROM orders
WHERE status IN ('PAID', 'TRANSFER_PENDING', 'COMPLETED', 'TRANSFER_FAILED')
GROUP BY listing_id
HAVING count(*) > 1
