"""Read database errors precisely, so code reacts to the rule that fired and nothing else."""

from sqlalchemy.exc import IntegrityError


def violated_constraint(error: IntegrityError) -> str | None:
    """The name of the constraint Postgres says was broken, e.g. "uq_users_email".

    Every constraint has a stable name (see NAMING_CONVENTION in base.py), so matching on the
    name is exact. Matching on the message text would break when Postgres rewords it.
    """
    diag = getattr(error.orig, "diag", None)
    return getattr(diag, "constraint_name", None)
