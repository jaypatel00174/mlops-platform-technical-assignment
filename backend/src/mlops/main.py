"""ASGI entry point:  uvicorn mlops.main:app

Settings are validated when this module is imported, so an unsafe configuration stops the
process before it serves a single request.
"""

from __future__ import annotations

from mlops.app import create_app
from mlops.platform.config import get_settings
from mlops.platform.db import make_engine, make_session_factory
from mlops.platform.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level)
app = create_app(
    settings=settings, session_factory=make_session_factory(make_engine(settings.database_url))
)
