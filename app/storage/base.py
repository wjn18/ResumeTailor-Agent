"""Database-independent persistence contract used by application services."""

from abc import ABC, abstractmethod
from typing import Any


class StorageError(RuntimeError):
    """A persistence operation failed; driver details remain in the cause."""


class StorageConflictError(StorageError):
    """A write violates a uniqueness, reference, or other integrity constraint."""


class Storage(ABC):
    """Adapters return plain JSON-compatible values, never driver objects.

    Each write is atomic. Missing rows return None (or False for deletion);
    missing documents raise FileNotFoundError. Invalid table/field requests
    raise ValueError. Driver failures are translated into StorageError, with
    integrity violations translated into StorageConflictError.
    """

    @abstractmethod
    def initialize(self) -> None:
        """Create any missing schema; preserve existing data."""

    @abstractmethod
    def list_rows(self, table_name: str, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        """Return rows ordered by ascending id."""

    @abstractmethod
    def get_row(self, table_name: str, item_id: int) -> dict[str, Any] | None:
        """Return one row, or None when missing."""

    @abstractmethod
    def create_row(self, table_name: str, values: dict[str, Any]) -> dict[str, Any]:
        """Create a row using the supported writable fields."""

    @abstractmethod
    def patch_row(self, table_name: str, item_id: int, values: dict[str, Any]) -> dict[str, Any] | None:
        """Update supplied writable fields, or return None when missing."""

    @abstractmethod
    def delete_row(self, table_name: str, item_id: int) -> bool:
        """Delete a row; return whether it existed."""

    @abstractmethod
    def upsert_json_document(self, table: str, id_column: str, document_id: str, payload: dict) -> None:
        """Insert or replace a JSON document by its business identifier."""

    @abstractmethod
    def load_json_document(self, table: str, id_column: str, document_id: str) -> dict:
        """Load a document, raising FileNotFoundError when missing."""

    @abstractmethod
    def list_tailored_resume_documents(self) -> list[dict]:
        """Return saved resume payloads ordered by generated_at descending."""

    @abstractmethod
    def save_tailored_resume_document(self, payload: dict) -> None:
        """Upsert a saved resume and its status/time metadata.

        Payload includes tailored_resume_id, status, and generated_at.
        Updating an existing document preserves its original ordering timestamp.
        """

    @abstractmethod
    def create_tailored_resume_document(self, payload: dict) -> dict:
        """Atomically insert once by ID, returning the existing document on conflict.

        Replayed workflow saves must never overwrite subsequent user edits.
        """

    @abstractmethod
    def project_tailored_resume_document(self, payload: dict) -> dict:
        """Advance a workflow document monotonically by (content_version, confirmed).

        Return the stored payload; an older or equal projection never overwrites it.
        """
