"""Finding out from the gateway: reconciliation (hard problem 6) and the holds whose buyer went to
pay and never came back (hard problem 5). Both run here on the test's own transaction; the races
are in tests/concurrency/."""

import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

import scripts.expire_reservations
import scripts.reconcile_payments
from app.infrastructure.payments import FakepayGateway
from app.modules.idempotency.models import IdempotencyKeyRow
from app.modules.payments.models import Payment, PaymentEvent, ReconciliationRun
from app.modules.payments.reconcile import reconcile, resolve_overdue
from app.modules.reservations.models import Reservation
from tests.integration.conftest import new_account
from tests.integration.payments import (
    Fakepay,
    event,
    fakepay,  # noqa: F401
    ordered,
    paying,
    post_intent,
    post_webhook,
    reserve,
)
from tests.integration.test_orders import audit
from tests.integration.test_payment_intents import count, statuses

pytestmark = pytest.mark.integration

WAITING = ("PAYMENT_PENDING", "PAYMENT_PENDING", "RESERVED")
PAID = ("PAID", "CONFIRMED", "RESERVED", "SUCCEEDED")
FAILED = ("PAYMENT_FAILED", "FAILED", "ACTIVE", "FAILED")
TIMED_OUT = ("CANCELLED", "EXPIRED", "ACTIVE", "FAILED")

Run = Callable[[], ReconciliationRun]


@pytest.fixture
def run(db_app: FastAPI, db_session: Session, fakepay: Fakepay) -> Iterator[Run]:  # noqa: F811
    """One reconciliation run, seen from the test's session."""

    def one_run() -> ReconciliationRun:
        db_session.commit()
        done = reconcile(db_app.state.session_factory, db_app.state.payment_gateway)
        return db_session.get_one(ReconciliationRun, done.id)

    yield one_run


def age(db: Session, minutes: int = 6) -> None:
    """Nothing has touched any payment for this long."""
    db.execute(update(Payment).values(updated_at=func.now() - timedelta(minutes=minutes)))
    db.commit()
    db.expire_all()


def overdue(db: Session, order: dict[str, Any], minutes: int = 16) -> None:
    """The order's hold ended this long ago, and nothing has touched its payment since."""
    db.execute(
        update(Reservation)
        .where(Reservation.id == uuid.UUID(order["reservation_id"]))
        .values(expires_at=func.now() - timedelta(minutes=minutes))
    )
    age(db)


# ---------------------------------------------------------------- reconciliation


def test_a_payment_whose_webhook_never_came_is_repaired(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
    run: Run,
) -> None:
    _, order, intent = paying(api, db_session)
    fakepay.choose(intent["provider_transaction_id"], "no_webhook")
    age(db_session)

    done = run()

    assert statuses(db_session, order) == PAID
    assert (done.status, done.checked_count, done.fixed_count) == ("SUCCEEDED", 1, 1)
    assert done.findings == [
        {
            "payment_id": intent["payment_id"],
            "local": "PENDING",
            "gateway": "succeeded",
            "action": "marked it paid",
            "fixed": True,
        }
    ]
    assert done.finished_at is not None and done.error is None
    assert audit(db_session, order["id"])[-1] == ("order.paid", None)


def test_a_second_run_fixes_nothing(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
    run: Run,
) -> None:
    _, order, intent = paying(api, db_session)
    fakepay.choose(intent["provider_transaction_id"], "no_webhook")
    age(db_session)
    run()

    again = run()

    assert (again.checked_count, again.fixed_count, again.findings) == (0, 0, [])
    assert statuses(db_session, order) == PAID


def test_a_webhook_that_comes_after_the_repair_changes_nothing(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
    run: Run,
) -> None:
    _, order, intent = paying(api, db_session)
    fakepay.choose(intent["provider_transaction_id"], "succeed", deliver=False)
    age(db_session)
    run()

    fakepay.deliver()

    assert fakepay.webhooks[0].status_code == 200
    assert db_session.scalar(select(PaymentEvent.processing_status)) == "IGNORED"
    assert statuses(db_session, order) == PAID
    assert [a for a, _ in audit(db_session, order["id"])].count("order.paid") == 1


def test_a_forged_webhook_is_made_good_by_the_next_run(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
    run: Run,
) -> None:
    _, order, intent = paying(api, db_session)
    fakepay.choose(intent["provider_transaction_id"], "bad_signature")
    assert fakepay.webhooks[0].status_code == 401
    assert statuses(db_session, order) == (*WAITING, "PENDING")
    age(db_session)
    run()
    assert statuses(db_session, order) == PAID


def test_a_declined_payment_is_failed(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
    run: Run,
) -> None:
    _, order, intent = paying(api, db_session)
    fakepay.choose(intent["provider_transaction_id"], "fail", deliver=False)
    age(db_session)
    assert run().findings[0]["action"] == "failed the payment"
    assert statuses(db_session, order) == FAILED


def test_an_intent_nobody_paid_in_time_is_timed_out(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
    run: Run,
) -> None:
    _, order, _ = paying(api, db_session)
    fakepay.now += 15 * 60
    age(db_session, 15)
    assert run().findings[0]["action"] == "timed it out"
    assert statuses(db_session, order) == TIMED_OUT
    assert db_session.scalar(select(Payment.failure_reason)) == "timeout"


def test_an_intent_still_open_is_left_alone(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
    run: Run,
) -> None:
    _, order, _ = paying(api, db_session)
    age(db_session)
    done = run()
    assert (done.checked_count, done.fixed_count, done.findings) == (1, 0, [])
    assert statuses(db_session, order) == (*WAITING, "PENDING")


def test_a_payment_touched_a_moment_ago_is_not_even_asked_about(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
    run: Run,
) -> None:
    paying(api, db_session)
    age(db_session, 4)
    fakepay.calls.clear()
    assert run().checked_count == 0
    assert fakepay.calls == []


def test_an_intent_whose_answer_was_lost_is_found_by_its_key(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
    run: Run,
) -> None:
    buyer, order = ordered(api, db_session)
    fakepay.answers_lost = True
    post_intent(api, order["id"], buyer)
    fakepay.answers_lost = False
    age(db_session)

    done = run()

    assert done.findings[0]["action"] == "kept its transaction id"
    assert statuses(db_session, order) == (*WAITING, "PENDING")
    # And so the webhook finds it.
    made = fakepay.client.get(f"/v1/intents?idempotency_key={done.findings[0]['payment_id']}")
    fakepay.choose(made.json()["transaction_id"], "succeed")
    assert statuses(db_session, order) == PAID


def test_one_that_was_also_paid_is_settled_in_the_same_run(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
    run: Run,
) -> None:
    buyer, order = ordered(api, db_session)
    fakepay.answers_lost = True
    post_intent(api, order["id"], buyer)
    fakepay.answers_lost = False
    payment_id = db_session.scalar(select(Payment.id))
    made = fakepay.client.get(f"/v1/intents?idempotency_key={payment_id}").json()
    # Its webhook has no payment to go with: we never learned the transaction id.
    fakepay.choose(made["transaction_id"], "succeed")
    assert db_session.scalar(select(PaymentEvent.processing_status)) == "FAILED"
    age(db_session)

    assert run().fixed_count == 1

    assert statuses(db_session, order) == PAID


def test_a_payment_the_gateway_never_heard_of_is_failed(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
    run: Run,
) -> None:
    buyer, order = ordered(api, db_session)
    fakepay.down = True
    post_intent(api, order["id"], buyer)
    fakepay.down = False
    overdue(db_session, order, minutes=1)

    assert run().findings[0] | {"payment_id": ""} == {
        "payment_id": "",
        "local": "CREATED",
        "gateway": "no intent",
        "action": "failed the payment",
        "fixed": True,
    }
    assert statuses(db_session, order) == FAILED
    assert db_session.scalar(select(Payment.failure_reason)) == "intent_never_created"


def test_not_if_its_buyer_comes_back_while_we_are_asking(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
    run: Run,
) -> None:
    # The race: the gateway says "no intent", and before that is acted on the buyer's next try
    # makes one. Failing the payment now would sell a ticket somebody is paying for.
    buyer, order = ordered(api, db_session)
    fakepay.down = True
    post_intent(api, order["id"], buyer)
    fakepay.down = False
    overdue(db_session, order, minutes=1)
    ask = fakepay.request
    retried: list[int] = []

    def slow(method: str, path: str, **kwargs: object):
        answer = ask(method, path, **kwargs)
        if method == "GET" and not retried:
            retried.append(post_intent(api, order["id"], buyer).status_code)
        return answer

    fakepay.request = slow  # type: ignore[method-assign]

    done = run()

    assert retried == [200]
    assert done.fixed_count == 0
    assert statuses(db_session, order) == (*WAITING, "PENDING")


def test_a_transaction_the_gateway_has_lost_is_reported_and_not_called_unpaid(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
    run: Run,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, order, _ = paying(api, db_session)
    db_session.execute(update(Payment).values(provider_transaction_id="txn_lost"))
    age(db_session)
    done = run()
    assert (done.fixed_count, done.findings[0]["gateway"]) == (0, "unknown")
    assert statuses(db_session, order) == (*WAITING, "PENDING")
    assert "payment.unknown_at_gateway" in caplog.text


def test_another_amount_at_the_gateway_is_reported_and_changes_nothing(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
    run: Run,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, order, intent = paying(api, db_session)
    fakepay.choose(intent["provider_transaction_id"], "no_webhook")
    db_session.execute(update(Payment).values(amount_paise=1))
    age(db_session)
    done = run()
    assert (done.fixed_count, done.findings[0]["action"]) == (0, "none: the amounts differ")
    assert statuses(db_session, order) == (*WAITING, "PENDING")
    assert "payment.amount_mismatch" in caplog.text


def test_a_silent_gateway_changes_nothing_and_the_run_says_so(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
    run: Run,
) -> None:
    orders = [paying(api, db_session)[1] for _ in range(4)]
    age(db_session)
    fakepay.down = True
    fakepay.calls.clear()

    done = run()

    assert (done.status, done.checked_count, done.fixed_count) == ("FAILED", 0, 0)
    assert "did not answer" in done.error
    assert len(fakepay.calls) == 3  # three in a row: asking about the fourth would time out too
    assert [f["gateway"] for f in done.findings] == ["no answer"] * 3
    for order in orders:
        assert statuses(db_session, order) == (*WAITING, "PENDING")


def test_one_payment_that_breaks_does_not_stop_the_others(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
    run: Run,
) -> None:
    broken, good = (paying(api, db_session) for _ in range(2))
    for _, _, intent in (broken, good):
        fakepay.choose(intent["provider_transaction_id"], "no_webhook")
    db_session.execute(
        update(Reservation)
        .where(Reservation.id == uuid.UUID(broken[1]["reservation_id"]))
        .values(status="EXPIRED")
    )
    age(db_session)

    done = run()

    assert (done.status, done.checked_count, done.fixed_count) == ("SUCCEEDED", 2, 1)
    assert statuses(db_session, good[1]) == PAID
    assert statuses(db_session, broken[1])[0] == "PAYMENT_PENDING"
    assert any("error" in finding["action"] for finding in done.findings)


def test_a_run_whose_process_died_is_closed_by_the_next(db_session: Session, run: Run) -> None:
    dead = ReconciliationRun(started_at=datetime.now(UTC) - timedelta(minutes=31))
    going = ReconciliationRun(started_at=datetime.now(UTC) - timedelta(minutes=1))
    db_session.add_all([dead, going])
    db_session.flush()

    run()

    db_session.expire_all()
    assert (dead.status, dead.error) == ("FAILED", "the run died")
    assert going.status == "RUNNING"


# ---------------------------------------------------------------- overdue holds


def resolve(db_app: FastAPI, db: Session):
    db.commit()
    return resolve_overdue(db_app.state.session_factory, db_app.state.payment_gateway)


def test_a_buyer_who_never_paid_loses_the_hold_once_the_gateway_says_so(
    db_app: FastAPI,
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    _, order, _ = paying(api, db_session)
    overdue(db_session, order)
    fakepay.now += 25 * 60

    assert resolve(db_app, db_session).fixed == 1

    assert statuses(db_session, order) == TIMED_OUT
    assert audit(db_session, order["reservation_id"])[-1] == ("reservation.expired", None)
    reserve(api, order["listing_id"], new_account(api))  # and it is on sale again


def test_a_buyer_who_paid_keeps_the_ticket_however_late_we_ask(
    db_app: FastAPI,
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    _, order, intent = paying(api, db_session)
    fakepay.choose(intent["provider_transaction_id"], "no_webhook")
    overdue(db_session, order, minutes=600)
    fakepay.now += 10 * 3600
    assert resolve(db_app, db_session).fixed == 1
    assert statuses(db_session, order) == PAID


def test_no_answer_means_leave_it(
    db_app: FastAPI,
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    _, order, _ = paying(api, db_session)
    overdue(db_session, order, minutes=600)
    fakepay.down = True
    report = resolve(db_app, db_session)
    assert (report.fixed, report.checked) == (0, 0)
    assert statuses(db_session, order) == (*WAITING, "PENDING")


def test_not_before_fifteen_minutes_after_the_holds_end(
    db_app: FastAPI,
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    _, order, _ = paying(api, db_session)
    overdue(db_session, order, minutes=14)
    fakepay.calls.clear()
    assert resolve(db_app, db_session).checked == 0
    assert fakepay.calls == []


def test_not_while_its_buyer_is_back_at_the_till(
    db_app: FastAPI,
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    # Its request may be asking the gateway for the intent we would not find.
    buyer, order = ordered(api, db_session)
    fakepay.down = True
    post_intent(api, order["id"], buyer)
    overdue(db_session, order)
    post_intent(api, order["id"], buyer)  # touches the payment, and fails again
    fakepay.down = False

    assert resolve(db_app, db_session).checked == 0
    assert statuses(db_session, order) == (*WAITING, "CREATED")


def test_a_hold_nobody_started_paying_for_is_the_expiry_jobs_own(
    db_app: FastAPI,
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    _, order = ordered(api, db_session)
    overdue(db_session, order)
    assert resolve(db_app, db_session).checked == 0


# ---------------------------------------------------------------- the scripts


@pytest.fixture
def scripted(
    monkeypatch: pytest.MonkeyPatch,
    db_app: FastAPI,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    """The scripts' own engine and gateway, replaced by the test's."""
    for script in (scripts.expire_reservations, scripts.reconcile_payments):
        monkeypatch.setattr(
            script, "create_session_factory", lambda engine: db_app.state.session_factory
        )
        monkeypatch.setattr(
            script.FakepayGateway, "at", classmethod(lambda cls, url: FakepayGateway(fakepay))
        )


def test_the_expiry_script_resolves_overdue_payments_too(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
    scripted: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, order, intent = paying(api, db_session)
    fakepay.choose(intent["provider_transaction_id"], "no_webhook")
    overdue(db_session, order)

    assert scripts.expire_reservations.main(["--once"]) == 0

    assert statuses(db_session, order) == PAID
    assert "Settled 1 overdue payment(s)." in capsys.readouterr().out


def test_the_expiry_script_still_expires_when_the_gateway_is_down(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
    scripted: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, waiting, _ = paying(api, db_session)
    overdue(db_session, waiting)
    _, unpaid = ordered(api, db_session)
    db_session.execute(
        update(Reservation)
        .where(Reservation.id == uuid.UUID(unpaid["reservation_id"]))
        .values(expires_at=func.now() - timedelta(seconds=1))
    )
    db_session.commit()
    fakepay.down = True

    assert scripts.expire_reservations.main(["--once"]) == 0

    assert statuses(db_session, unpaid)[:3] == ("CANCELLED", "EXPIRED", "ACTIVE")
    assert statuses(db_session, waiting) == (*WAITING, "PENDING")
    assert "Expired 1 reservation(s)." in capsys.readouterr().out


def test_the_reconciliation_script(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
    scripted: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, order, intent = paying(api, db_session)
    fakepay.choose(intent["provider_transaction_id"], "no_webhook")
    age(db_session)
    db_session.execute(
        update(IdempotencyKeyRow).values(expires_at=func.now() - timedelta(seconds=1))
    )
    db_session.commit()

    assert scripts.reconcile_payments.main([]) == 0

    assert statuses(db_session, order) == PAID
    out = capsys.readouterr().out
    assert "SUCCEEDED: checked 1, fixed 1" in out and "marked it paid" in out
    assert count(db_session, IdempotencyKeyRow) == 0


def test_the_reconciliation_script_fails_loudly_when_the_gateway_is_down(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
    scripted: None,
) -> None:
    paying(api, db_session)
    age(db_session)
    fakepay.down = True
    assert scripts.reconcile_payments.main([]) == 1


def test_an_event_posted_by_hand_matches_what_fakepay_sends(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
) -> None:
    # The hand-made webhooks in the tests are only worth something if they look like real ones.
    _, _, intent = paying(api, db_session)
    fakepay.choose(intent["provider_transaction_id"], "succeed")
    real = db_session.scalar(select(PaymentEvent)).payload
    assert set(real) == set(event(intent))
    assert post_webhook(api, event(intent)).status_code == 200


# ---------------------------------------------------------------- found in review


def test_twenty_forgotten_webhooks_are_fixed_in_one_run_and_none_in_the_next(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
    run: Run,
) -> None:
    orders = []
    for _ in range(20):
        _, order, intent = paying(api, db_session)
        fakepay.choose(intent["provider_transaction_id"], "no_webhook")
        orders.append(order)
    age(db_session)

    first, second = run(), run()

    assert (first.checked_count, first.fixed_count) == (20, 20)
    assert (second.checked_count, second.fixed_count) == (0, 0)
    assert all(statuses(db_session, order) == PAID for order in orders)


def test_a_buyer_back_at_an_open_payment_does_not_hide_it_from_the_run(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
    run: Run,
) -> None:
    # Only a payment with no transaction id yet needs keeping away from the run: that is the
    # one whose intent a request may be creating right now.
    buyer, order, intent = paying(api, db_session)
    age(db_session)
    assert post_intent(api, order["id"], buyer).status_code == 200
    fakepay.choose(intent["provider_transaction_id"], "no_webhook")

    assert run().fixed_count == 1
    assert statuses(db_session, order) == PAID


def test_one_lookup_without_an_answer_does_not_stop_the_others(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
    run: Run,
) -> None:
    stuck, good = (paying(api, db_session) for _ in range(2))
    for _, _, intent in (stuck, good):
        fakepay.choose(intent["provider_transaction_id"], "no_webhook")
    age(db_session)
    ask, bad = fakepay.request, stuck[2]["provider_transaction_id"]

    def flaky(method: str, path: str, **kwargs: object) -> httpx.Response:
        return httpx.Response(500) if path.endswith(bad) else ask(method, path, **kwargs)

    fakepay.request = flaky  # type: ignore[method-assign]

    done = run()

    assert statuses(db_session, good[1]) == PAID
    assert statuses(db_session, stuck[1]) == (*WAITING, "PENDING")
    assert (done.status, done.checked_count, done.fixed_count) == ("FAILED", 1, 1)
    assert "500" in done.error
    assert {f["payment_id"]: f["gateway"] for f in done.findings}[stuck[2]["payment_id"]] == (
        "no answer"
    )


def test_a_payment_the_gateway_never_heard_of_keeps_its_hold_until_the_hold_ends(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
    run: Run,
) -> None:
    # The buyer has ten minutes to try again, and the next try makes the intent.
    buyer, order = ordered(api, db_session)
    fakepay.down = True
    post_intent(api, order["id"], buyer)
    fakepay.down = False
    age(db_session)

    assert run().fixed_count == 0
    assert statuses(db_session, order) == (*WAITING, "CREATED")


def test_a_payment_touched_while_the_run_is_going_is_left_alone(
    api: TestClient,
    db_session: Session,
    fakepay: Fakepay,  # noqa: F811
    run: Run,
) -> None:
    # Both were candidates when the run began; one buyer came back before its turn.
    pairs = [ordered(api, db_session) for _ in range(2)]
    fakepay.down = True
    for buyer, order in pairs:
        post_intent(api, order["id"], buyer)
    fakepay.down = False
    for _, order in pairs:
        overdue(db_session, order, minutes=1)
    fakepay.calls.clear()
    ask = fakepay.request

    def slow(method: str, path: str, **kwargs: object) -> httpx.Response:
        asked = kwargs["params"]["idempotency_key"]  # type: ignore[index]
        if not fakepay.calls:
            db_session.execute(
                update(Payment).where(Payment.id != uuid.UUID(asked)).values(updated_at=func.now())
            )
            db_session.commit()
        return ask(method, path, **kwargs)

    fakepay.request = slow  # type: ignore[method-assign]

    done = run()

    assert len(fakepay.calls) == 1
    assert done.fixed_count == 1
    assert sorted(statuses(db_session, order)[3] for _, order in pairs) == ["CREATED", "FAILED"]
