"""Dependencies every module can use."""

from typing import Annotated

from fastapi import Depends, Request

from app.core.config import Settings


def get_app_settings(request: Request) -> Settings:
    """The Settings this app was built with. Not get_settings(): tests build apps with their
    own Settings, and a route must see those, not the process-wide defaults."""
    return request.app.state.settings


AppSettings = Annotated[Settings, Depends(get_app_settings)]
