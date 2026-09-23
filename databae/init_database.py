"""Compatibility CLI; backend selection and initialization live in app.storage."""

from app.storage.factory import get_database_url, initialize_database
from app.storage.postgres_schema import SCHEMA  # Backwards-compatible schema import.


if __name__ == "__main__":
    initialize_database()
    print("Database initialized.")
