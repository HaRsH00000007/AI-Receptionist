"""ASGI entrypoint.

    uv run uvicorn app.asgi:app --reload

Importing this module reads the environment and will fail loudly if the process
is misconfigured — which is the intended behaviour for a server entrypoint, and
the reason the factory in :mod:`app.main` stays import-safe.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.main import create_app

app = create_app(get_settings())
