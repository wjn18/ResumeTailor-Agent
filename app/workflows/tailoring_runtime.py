"""Process-local graph sessions for the staged API.

Use one server worker until a durable checkpointer and cross-process task
coordination are introduced. Inactive sessions expire after one hour.
"""

from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Lock
from time import monotonic
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver

from app.workflows.tailoring_graph import build_tailoring_graph


class TailoringRunNotFound(LookupError):
    pass


class TailoringRunBusy(RuntimeError):
    pass


class TailoringCapacityExceeded(RuntimeError):
    pass


@dataclass
class TailoringRun:
    thread_id: str
    lock: Lock = field(default_factory=Lock)
    touched_at: float = field(default_factory=monotonic)
    response: dict | None = None
    status: str = "running"

    @property
    def config(self):
        return {"configurable": {"thread_id": self.thread_id}}


class TailoringRuntime:
    def __init__(self, client=None, *, ttl_seconds=3600, max_runs=256):
        self.checkpointer = InMemorySaver()
        self.graph = build_tailoring_graph(client=client, checkpointer=self.checkpointer)
        self.ttl_seconds = ttl_seconds
        self.max_runs = max_runs
        self._runs: dict[str, TailoringRun] = {}
        self._registry_lock = Lock()

    def _expire(self):
        now = monotonic()
        for thread_id, run in list(self._runs.items()):
            if not run.lock.locked() and now - run.touched_at >= self.ttl_seconds:
                self.checkpointer.delete_thread(thread_id)
                del self._runs[thread_id]

    @contextmanager
    def create(self):
        with self._registry_lock:
            self._expire()
            if len(self._runs) >= self.max_runs:
                raise TailoringCapacityExceeded("生成任务已满，请稍后重试。")
            run = TailoringRun(thread_id=f"tailoring_{uuid4().hex}")
            run.lock.acquire()
            self._runs[run.thread_id] = run
        try:
            yield run
        except Exception:
            # A failed creation has not returned an ID to the caller.
            with self._registry_lock:
                self.checkpointer.delete_thread(run.thread_id)
                del self._runs[run.thread_id]
            raise
        finally:
            run.touched_at = monotonic()
            run.lock.release()

    @contextmanager
    def acquire(self, thread_id):
        with self._registry_lock:
            self._expire()
            run = self._runs.get(thread_id)
            if run is None:
                raise TailoringRunNotFound("生成任务不存在或已过期，请重新生成。")
            if not run.lock.acquire(blocking=False):
                raise TailoringRunBusy("该任务正在处理，请勿重复提交。")
        try:
            yield run
        except Exception:
            run.status = "failed"
            raise
        finally:
            run.touched_at = monotonic()
            run.lock.release()

    def start(self, run, jd, resume, *, initial_only=False):
        state = self.graph.invoke(
            {"jd": jd.model_dump(mode="json"), "resume": resume.model_dump(mode="json")},
            config=run.config,
            interrupt_after=["generate_initial_draft"] if initial_only else None,
        )
        run.status = state["status"]
        return state

    def finish(self, run):
        snapshot = self.graph.get_state(run.config)
        state = (
            self.graph.invoke(None, config=run.config)
            if snapshot.next else snapshot.values
        )
        run.status = state["status"]
        return state


_runtime: TailoringRuntime | None = None
_runtime_lock = Lock()


def get_tailoring_runtime() -> TailoringRuntime:
    # FastAPI sync endpoints may arrive on different threads during startup.
    # Construct exactly one runtime so no first request loses its checkpoints.
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = TailoringRuntime()
        return _runtime
