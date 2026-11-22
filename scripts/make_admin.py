"""Make an existing account an admin, or take the role away. Run by an operator, not over HTTP.

    uv run python -m scripts.make_admin asha@example.in          # USER -> ADMIN
    uv run python -m scripts.make_admin asha@example.in --revoke # ADMIN -> USER

There is deliberately no API route that grants ADMIN: a bug in one would let anyone promote
themselves. Register the account first (POST /auth/register), then run this against the
database in DATABASE_URL. The change applies to the user's very next request, because
get_current_user reads the role from the database, not from the token.
"""

import argparse
import sys

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.infrastructure.database.engine import create_db_engine
from app.infrastructure.database.session import create_session_factory
from app.modules.identity.models import User, UserRole


def set_role(session: Session, email: str, role: UserRole | str) -> bool:
    """True if an account with that email exists (its role is now `role`)."""
    result = session.execute(
        update(User).where(User.email == email.strip().lower()).values(role=role)
    )
    return result.rowcount == 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Grant or revoke the ADMIN role.")
    parser.add_argument("email")
    parser.add_argument("--revoke", action="store_true", help="make the account a USER again")
    args = parser.parse_args(argv)
    role = UserRole.USER if args.revoke else UserRole.ADMIN

    engine = create_db_engine(get_settings())
    try:
        with create_session_factory(engine)() as session, session.begin():
            found = set_role(session, args.email, role)
    finally:
        engine.dispose()

    if not found:
        print(f"No account uses {args.email!r}. Register it first.", file=sys.stderr)
        return 1
    print(f"{args.email} is now {role}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
