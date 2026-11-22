"""Base classes for request and response bodies."""

from pydantic import BaseModel, ConfigDict


class RequestModel(BaseModel):
    """Every request body. An unknown field is a 422, not silently dropped: a client that
    sends {"role": "ADMIN"} to /auth/register learns it was refused instead of believing it
    worked, and a typo in a field name is caught at once."""

    model_config = ConfigDict(extra="forbid")


class ResponseModel(BaseModel):
    """Every response body. from_attributes lets a route return an ORM object directly; the
    response model then copies only the fields it declares, so a column such as
    password_hash can never leak by accident."""

    model_config = ConfigDict(from_attributes=True)
