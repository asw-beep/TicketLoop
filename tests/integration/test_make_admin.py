"""scripts/make_admin.py: the only way to create an admin. No API route can grant the role."""

import pytest
from sqlalchemy.orm import Session

from app.modules.identity.models import User
from scripts.make_admin import set_role
from tests.integration.factories import make_user

pytestmark = pytest.mark.integration


def test_set_role_promotes_a_user_found_by_email_in_any_case(db_session: Session) -> None:
    user = make_user(db_session, email="asha@example.in")

    changed = set_role(db_session, " Asha@Example.IN ", "ADMIN")

    assert changed is True
    assert db_session.get(User, user.id).role == "ADMIN"


def test_set_role_can_demote_an_admin(db_session: Session) -> None:
    user = make_user(db_session, role="ADMIN")

    assert set_role(db_session, user.email, "USER") is True
    assert db_session.get(User, user.id).role == "USER"


def test_set_role_reports_an_unknown_email(db_session: Session) -> None:
    assert set_role(db_session, "nobody@example.in", "ADMIN") is False
