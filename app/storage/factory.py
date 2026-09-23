"""The composition point for selecting and initializing storage adapters."""

import os
from collections.abc import Callable
from functools import lru_cache

from app.storage.base import Storage


def get_database_url() -> str:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for persistence.")
    return _normalize_url(database_url)


def _normalize_url(database_url: str) -> str:
    database_url = database_url.strip()
    if database_url.startswith("postgres://"):
        return "postgresql://" + database_url[len("postgres://") :]
    return database_url


def _postgresql(database_url: str) -> Storage:
    # Import only the selected driver, so other adapters need not install it.
    from app.storage.postgres import PostgresStorage

    return PostgresStorage(database_url)


# Add new adapters here; services and API handlers depend only on Storage.
BACKENDS: dict[str, Callable[[str], Storage]] = {"postgresql": _postgresql}


def _settings(backend: str | None, database_url: str | None) -> tuple[str, str]:
    backend = (backend if backend is not None else os.getenv("STORAGE_BACKEND", "postgresql")).strip().lower()
    if backend not in BACKENDS:
        raise ValueError(f"Unsupported STORAGE_BACKEND: {backend}")
    database_url = get_database_url() if database_url is None else _normalize_url(database_url)
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for persistence.")
    return backend, database_url


def create_storage(backend: str | None = None, database_url: str | None = None) -> Storage:
    """Construct an adapter from explicit options or environment configuration."""
    backend, database_url = _settings(backend, database_url)
    return BACKENDS[backend](database_url)


@lru_cache(maxsize=8)
def _cached_storage(backend: str, database_url: str) -> Storage:
    return create_storage(backend, database_url)


def get_storage(backend: str | None = None, database_url: str | None = None) -> Storage:
    """Reuse an adapter for a configuration, without holding open connections."""
    return _cached_storage(*_settings(backend, database_url))


def initialize_database(database_url: str | None = None) -> None:
    """Shared initialization entry point for application startup and the CLI."""
    get_storage(database_url=database_url).initialize()
