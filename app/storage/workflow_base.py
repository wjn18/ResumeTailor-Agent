"""Task persistence is separate from business document storage."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol


class RunNotFound(LookupError):
    pass


class RunBusy(RuntimeError):
    pass


class RunCancelled(RuntimeError):
    pass


class RunConflict(RuntimeError):
    pass


@dataclass
class RunLease:
    checkpointer: Any
    read: Callable[[], dict]
    update: Callable[..., dict]
    begin_finalizing: Callable[[], bool]


class RunStore(Protocol):
    def create(self, thread_id: str, payload: dict, target: str) -> dict: ...
    def get(self, thread_id: str) -> dict: ...
    def pending(self, limit: int = 32) -> list[str]: ...
    def resume(self, thread_id: str) -> dict: ...
    def cancel(self, thread_id: str) -> dict: ...
    def lease(self, thread_id: str, *, wait: bool = False): ...
    def close(self) -> None: ...
