"""Identity business logic: accounts, login and the refresh-token lifecycle.

Functions take the request's Session and never commit; get_db commits once the route returns
(app/infrastructure/database/session.py). The one exception is refresh-token reuse, below.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import NoReturn

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import (
    burn_password_check,
    create_access_token,
    hash_opaque_token,
    hash_password,
    new_opaque_token,
    password_needs_rehash,
    verify_password,
)
from app.infrastructure.database.errors import violated_constraint
from app.infrastructure.external_providers.email import EmailMessage, EmailSender
from app.modules.identity.errors import (
    AccountSuspendedError,
    EmailTakenError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    InvalidResetTokenError,
    WrongPasswordError,
)
from app.modules.identity.models import AccountStatus, PasswordResetToken, RefreshToken, User

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class IssuedTokens:
    """A new access token and refresh token. The router puts the refresh token in a cookie."""

    access_token: str
    refresh_token: str
    expires_in: int  # seconds until the access token expires


# ---------------------------------------------------------------- accounts


def register(db: Session, *, email: str, password: str, full_name: str) -> User:
    user = User(email=email, password_hash=hash_password(password), full_name=full_name)
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        # No "does this email exist?" query first: two sign-ups for one email can both pass
        # that check at the same moment. The unique constraint decides, race-free.
        if violated_constraint(exc) == "uq_users_email":
            raise EmailTakenError() from exc
        raise
    log.info("auth.registered", user_id=str(user.id))
    return user


# ---------------------------------------------------------------- login


def login(db: Session, settings: Settings, *, email: str, password: str) -> IssuedTokens:
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        burn_password_check(password)  # take as long as a real check would
        log.info("auth.login_failed", reason="unknown_email")
        raise InvalidCredentialsError()
    if not verify_password(user.password_hash, password):
        log.info("auth.login_failed", reason="wrong_password", user_id=str(user.id))
        raise InvalidCredentialsError()
    # Checked only after the password, so a stranger cannot learn an account is suspended.
    if user.account_status == AccountStatus.SUSPENDED:
        log.info("auth.login_refused", reason="suspended", user_id=str(user.id))
        raise AccountSuspendedError()

    if password_needs_rehash(user.password_hash):
        # The only moment we hold the plaintext, so the only moment we can upgrade the hash.
        user.password_hash = hash_password(password)

    pair, _ = _issue_token_pair(db, settings, user, family_id=uuid.uuid4())
    log.info("auth.login_succeeded", user_id=str(user.id))
    return pair


# ---------------------------------------------------------------- passwords


def change_password(db: Session, user: User, *, current_password: str, new_password: str) -> None:
    # Asking for the current password again means a stolen access token alone cannot be
    # turned into a stolen account.
    if not verify_password(user.password_hash, current_password):
        raise WrongPasswordError()
    user.password_hash = hash_password(new_password)
    revoked = _revoke_all_sessions(db, user.id)
    log.info("auth.password_changed", user_id=str(user.id), sessions_revoked=revoked)


def request_password_reset(
    db: Session, settings: Settings, mailer: EmailSender, *, email: str
) -> None:
    """Email a single-use reset token, if the account exists and may log in.

    The route always answers 202 with the same body, so this endpoint cannot be used to find
    out which emails have accounts.
    """
    user = db.scalar(select(User).where(User.email == email))
    if user is None or user.account_status == AccountStatus.SUSPENDED:
        log.info("auth.password_reset_skipped", reason="unknown" if user is None else "suspended")
        return

    token = new_opaque_token()
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hash_opaque_token(token),
            expires_at=datetime.now(UTC) + timedelta(minutes=settings.password_reset_minutes),
        )
    )
    db.flush()
    # Sent before get_db commits. If the commit then fails, the email holds a token that
    # does not exist and simply will not work. The M11 outbox sends only after a commit.
    mailer.send(
        EmailMessage(
            to=user.email,
            subject="Reset your TicketLoop password",
            body=(
                # After "#", so the token never leaves the browser: no server, access log or
                # Referer header sees it. The page reads it and posts it to /confirm.
                f"Choose a new password: {settings.frontend_url}/reset-password#token={token}\n"
                f"The link works once and expires in {settings.password_reset_minutes} minutes. "
                "If you did not ask for this, ignore this email; your password is unchanged."
            ),
        )
    )
    log.info("auth.password_reset_requested", user_id=str(user.id))


def confirm_password_reset(db: Session, *, token: str, new_password: str) -> None:
    # Spend the token and learn whose it is in one conditional UPDATE, so two requests with
    # the same token cannot both succeed.
    user_id = db.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.token_hash == hash_opaque_token(token),
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > func.now(),
        )
        .values(used_at=func.now())
        .returning(PasswordResetToken.user_id)
    ).scalar_one_or_none()
    if user_id is None:
        raise InvalidResetTokenError()

    user = db.get_one(User, user_id)
    user.password_hash = hash_password(new_password)
    # Any other reset email still in the inbox stops working too.
    db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user_id, PasswordResetToken.used_at.is_(None))
        .values(used_at=func.now())
    )
    # Whoever knew the old password may be logged in somewhere. Not any more.
    revoked = _revoke_all_sessions(db, user_id)
    log.info("auth.password_reset_completed", user_id=str(user_id), sessions_revoked=revoked)


# ---------------------------------------------------------------- refresh and logout


def refresh(db: Session, settings: Settings, *, refresh_token: str) -> IssuedTokens:
    """Swap a live refresh token for a new pair. The presented token is revoked.

    The swap is one conditional UPDATE, the same pattern as the M2 lock experiment and the
    M8 reservation: "revoke this token if it is still live". If two requests present the same
    token at once, Postgres makes the second wait for the first to commit, then re-checks
    `revoked_at IS NULL`, finds it false and updates nothing. Exactly one wins.
    """
    token_hash = hash_opaque_token(refresh_token)
    claimed = db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > func.now(),
        )
        .values(revoked_at=func.now())
        .returning(RefreshToken.id, RefreshToken.user_id, RefreshToken.family_id)
    ).one_or_none()
    if claimed is None:
        _refuse_refresh(db, token_hash)

    user = db.get_one(User, claimed.user_id)
    if user.account_status == AccountStatus.SUSPENDED:
        raise AccountSuspendedError()  # get_db rolls back, so the token is not even spent

    pair, successor = _issue_token_pair(db, settings, user, family_id=claimed.family_id)
    db.execute(
        update(RefreshToken).where(RefreshToken.id == claimed.id).values(replaced_by=successor.id)
    )
    log.info("auth.refreshed", user_id=str(user.id), family_id=str(claimed.family_id))
    return pair


def _refuse_refresh(db: Session, token_hash: str) -> NoReturn:
    """Work out why a token could not be claimed, act on it, and raise 401."""
    row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    if row is None:
        log.info("auth.refresh_refused", reason="unknown")
        raise InvalidRefreshTokenError()
    if row.revoked_at is None:
        log.info("auth.refresh_refused", reason="expired", user_id=str(row.user_id))
        raise InvalidRefreshTokenError()

    # A revoked token came back. Its legitimate holder swapped it for a new one already, so
    # two parties hold tokens from this family and we cannot tell which one is the thief.
    # End the session for both: revoke every live token in the family.
    revoked = _revoke_family(db, row.family_id)
    log.warning(
        "auth.refresh_token_reused",
        user_id=str(row.user_id),
        family_id=str(row.family_id),
        revoked=revoked,
    )
    # The one commit outside get_db. get_db rolls back when the route raises, and the 401
    # below would undo this revocation, leaving the thief's token alive.
    db.commit()
    raise InvalidRefreshTokenError()


def logout(db: Session, *, refresh_token: str) -> None:
    """End the session the token belongs to: revoke every live token in its family.

    The token is the credential, as it is for refresh: whoever holds it could already use it,
    so ending its session needs nothing more. An unknown token has no session to end.
    """
    # FOR UPDATE: if a refresh of this token is in flight, wait for it to commit, so the
    # family UPDATE below also sees, and revokes, the token that refresh just issued.
    row = db.scalar(
        select(RefreshToken)
        .where(RefreshToken.token_hash == hash_opaque_token(refresh_token))
        .with_for_update()
    )
    if row is None:
        log.info("auth.logout_without_session")
        return
    _revoke_family(db, row.family_id)  # already revoked? Then nothing changes: still a 204
    log.info("auth.logged_out", user_id=str(row.user_id), family_id=str(row.family_id))


def _revoke_all_sessions(db: Session, user_id: uuid.UUID) -> int:
    """Revoke every live refresh token the user has. Uses ix_refresh_tokens_user_id_live.

    Access tokens already issued keep working until they expire, at most 15 minutes later.
    That is the price of tokens whose signature needs no database to check.
    """
    result = db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=func.now())
    )
    return result.rowcount


def _revoke_family(db: Session, family_id: uuid.UUID) -> int:
    result = db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=func.now())
    )
    return result.rowcount


# ---------------------------------------------------------------- tokens


def _issue_token_pair(
    db: Session, settings: Settings, user: User, *, family_id: uuid.UUID
) -> tuple[IssuedTokens, RefreshToken]:
    """A new access token, and a new refresh token whose hash is saved in `family_id`."""
    refresh_token = new_opaque_token()
    row = RefreshToken(
        user_id=user.id,
        family_id=family_id,
        token_hash=hash_opaque_token(refresh_token),
        expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_days),
    )
    db.add(row)
    db.flush()
    pair = IssuedTokens(
        access_token=create_access_token(user.id, settings),
        refresh_token=refresh_token,
        expires_in=settings.access_token_minutes * 60,
    )
    return pair, row
