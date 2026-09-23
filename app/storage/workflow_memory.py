"""Explicit test adapter. Production never falls back to memory."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from threading import Lock, RLock

from langgraph.checkpoint.memory import InMemorySaver

from app.storage.workflow_base import RunBusy, RunConflict, RunLease, RunNotFound


class MemoryRunStore:
    def __init__(self):
        self.runs = {}
        self.locks = {}
        self.guard = RLock()
        self.checkpointer = InMemorySaver()

    def create(self, thread_id, payload, target):
        with self.guard:
            if thread_id in self.runs:
                existing = self.get(thread_id)
                if existing["input_hash"] != payload["input_hash"]:
                    raise RunConflict("同一请求 ID 不能用于不同输入。")
                return existing
            now = datetime.now(timezone.utc).isoformat()
            self.runs[thread_id] = {
                **deepcopy(payload), "thread_id": thread_id, "target": target,
                "status": "queued", "cancel_requested": False,
                "created_at": now, "updated_at": now,
            }
            self.locks[thread_id] = Lock()
            return self.get(thread_id)

    def get(self, thread_id):
        with self.guard:
            if thread_id not in self.runs:
                raise RunNotFound("生成任务不存在。")
            return deepcopy(self.runs[thread_id])

    def update(self, thread_id, **changes):
        with self.guard:
            if self.runs[thread_id]["cancel_requested"] and changes.get("status") in {"queued", "failed", "initial_ready"}:
                changes["status"] = "cancelled"
            self.runs[thread_id].update(deepcopy(changes))
            self.runs[thread_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
            return self.get(thread_id)

    def pending(self, limit=32):
        with self.guard:
            return [key for key, run in self.runs.items()
                    if run["status"] in {"queued", "running", "cancelling", "saving"}][:limit]

    def resume(self, thread_id):
        with self.guard:
            run = self.get(thread_id)
            if run["cancel_requested"]:
                raise RunConflict("任务已取消，请重新生成。")
            if run["status"] in {"failed", "initial_ready"}:
                return self.update(thread_id, status="queued", target="full", error=None, failed_node=None)
            return run

    def cancel(self, thread_id):
        with self.guard:
            run = self.get(thread_id)
            if run["status"] in {"saving", "awaiting_confirmation", "needs_attention", "completed"}:
                raise RunConflict("任务已进入结果保存阶段，无法取消。")
            status = "cancelling" if run["status"] in {"running", "cancelling"} else "cancelled"
            return self.update(thread_id, cancel_requested=True, status=status)

    def begin_finalizing(self, thread_id):
        with self.guard:
            if self.get(thread_id)["cancel_requested"]:
                return False
            self.update(thread_id, status="saving")
            return True

    @contextmanager
    def lease(self, thread_id, *, wait=False):
        self.get(thread_id)
        lock = self.locks[thread_id]
        if not lock.acquire(blocking=wait):
            raise RunBusy("该任务正在处理。")
        try:
            yield RunLease(
                self.checkpointer,
                lambda: self.get(thread_id),
                lambda **changes: self.update(thread_id, **changes),
                lambda: self.begin_finalizing(thread_id),
            )
        finally:
            lock.release()

    def close(self):
        pass
