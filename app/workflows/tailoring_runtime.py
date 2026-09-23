"""Durable task execution shared by HTTP requests and background workers."""

import hashlib
import json
import logging
import os
from threading import Event, Lock, Thread
from uuid import uuid4
from langgraph.types import Command

from app.schemas.jds import ParsedJD
from app.schemas.resumes import ParsedResume
from app.schemas.tailoring import TailoringBuildResponse, TailoringInitialBuildResponse, FormalResumeDocument
from app.services.tailoring import assemble_formal_resume
from app.services.tailored_resume_storage import save_tailored_resume, project_workflow_resume
from app.services.formal_resume_review import content_hash
from app.storage.workflow_base import RunBusy, RunCancelled, RunConflict
from app.storage.workflow_factory import create_run_store
from app.storage.workflow_memory import MemoryRunStore
from app.workflows.tailoring_graph import build_tailoring_graph, tailoring_result


GRAPH_VERSION = 3
ACTIVE = {"queued", "running", "cancelling", "saving"}
logger = logging.getLogger(__name__)


class RunPaused(Exception):
    """Shutdown leaves the job queued for another worker."""


class TailoringRuntime:
    def __init__(self, client=None, *, store=None):
        # Memory is explicit in test construction; the production factory below
        # always supplies a PostgreSQL store and never silently falls back.
        self.store = store if store is not None else MemoryRunStore()
        self.client = client
        self.stop_event = Event()
        self.workers = []

    def submit(self, jd, resume, *, initial_only=False, request_id=None):
        inputs = {"jd": jd.model_dump(mode="json"), "resume": resume.model_dump(mode="json")}
        payload = {
            "inputs": inputs,
            "input_hash": hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest(),
            "graph_version": GRAPH_VERSION, "revision_count": 0,
            "draft_version": 0, "audit_version": 0,
            "preview": None, "result": None, "error": None,
            "failed_node": None, "current_node": None,
        }
        return self.store.create(
            f"tailoring_{(request_id or uuid4()).hex}", payload, "initial" if initial_only else "full",
        )

    def get(self, thread_id):
        return self.store.get(thread_id)

    def resume(self, thread_id):
        job = self.get(thread_id)
        if job["graph_version"] == 2 and job["status"] in {"awaiting_confirmation", "needs_attention"}:
            with self.store.lease(thread_id) as lease:
                job = lease.read()
                if job["graph_version"] == 2:
                    from app.services import tailored_resume_storage
                    document = job["result"]["formal_resume"]
                    if job["result"].get("saved_resume"):
                        saved = tailored_resume_storage.load_tailored_resume(job["result"]["saved_resume"]["tailored_resume_id"])
                        document = saved.formal_resume.model_dump(mode="json")
                    # Persist the migration intent BEFORE changing checkpoints.
                    # A crash between these writes can safely repeat the upgrade.
                    return lease.update(graph_version=GRAPH_VERSION, upgrade_document=document,
                                        current_document=document,
                                        status="queued", result=None, error=None, pending_decision=None)
                return job
        return self.store.resume(thread_id)

    def cancel(self, thread_id):
        return self.store.cancel(thread_id)

    def decide(self, thread_id, decision):
        action = decision.model_dump(mode="json")
        with self.store.lease(thread_id) as lease:
            job = lease.read()
            receipts = dict(job.get("action_receipts", {}))
            digest = hashlib.sha256(json.dumps(action, sort_keys=True).encode()).hexdigest()
            previous = receipts.get(action["request_id"])
            if previous:
                if previous != digest:
                    raise RunConflict("同一操作 ID 不能用于不同内容。")
                return job
            receipts[action["request_id"]] = digest
            if job["graph_version"] != GRAPH_VERSION:
                raise RunConflict("这是旧版任务，请先通过 resume 接口升级并重新审核。")
            if job["status"] not in {"awaiting_confirmation", "needs_attention", "completed"}:
                raise RunConflict("任务尚未等待人工操作，请等待或重试失败的任务。")
            graph = build_tailoring_graph(checkpointer=lease.checkpointer, human_loop=True)
            snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})
            state = snapshot.values
            if action["expected_version"] != state.get("content_version"):
                raise RunConflict("正文版本已变更，请刷新后操作。")
            if action["action"] == "confirm":
                if (state.get("content_version") != state.get("reviewed_content_version")
                        or content_hash(state["formal_resume"]) != state.get("reviewed_content_hash")
                        or any(c["support_status"] != "supported" for c in state["content_report"]["checks"])):
                    raise RunConflict("当前正文尚未通过审核，请修改后重新审核。")
                if job["status"] == "completed":
                    return lease.update(action_receipts=receipts)
            interrupts = [item for task in snapshot.tasks for item in task.interrupts]
            if len(interrupts) != 1:
                raise RunConflict("任务没有可恢复的人工确认节点。")
            return lease.update(
                status="queued", accepted_action=action,
                action_receipts=receipts,
                pending_action={"interrupt_id": interrupts[0].id, "value": action},
                pending_decision=None, result=None, error=None,
            )

    def confirmed_document(self, thread_id, saved):
        with self.store.lease(thread_id) as lease:
            job = lease.read()
            if job["status"] != "completed" or job["graph_version"] != GRAPH_VERSION:
                raise RunConflict("当前任务尚未确认，或正文正在重新审核。")
            graph = build_tailoring_graph(checkpointer=lease.checkpointer, human_loop=True)
            state = graph.get_state({"configurable": {"thread_id": thread_id}}).values
            if (state.get("status") != "completed"
                    or saved.tailored_resume_id != "tailored_" + thread_id.removeprefix("tailoring_")
                    or saved.content_version != state["content_version"]
                    or state["content_version"] != state["reviewed_content_version"]
                    or content_hash(saved.formal_resume) != state["reviewed_content_hash"]
                    or any(c["support_status"] != "supported" for c in state["content_report"]["checks"])):
                raise RunConflict("正文与已确认审核版本不一致，请重新审核。")
            return FormalResumeDocument.model_validate(state["formal_resume"])

    def execute(self, thread_id, *, wait=False):
        with self.store.lease(thread_id, wait=wait) as lease:
            job = lease.read()
            if job["status"] not in ACTIVE:
                return job
            config = {"configurable": {"thread_id": thread_id}}

            def before_node(name):
                # The same live DB session owns both this check and graph writes.
                current = lease.read()
                if current["cancel_requested"]:
                    raise RunCancelled("任务已取消。")
                if self.stop_event.is_set():
                    raise RunPaused()
                lease.update(current_node=name)

            graph = build_tailoring_graph(
                client=self.client, checkpointer=lease.checkpointer, before_node=before_node,
                human_loop=job["graph_version"] == GRAPH_VERSION,
            )
            try:
                before_node("restore")
                if job["graph_version"] not in {2, GRAPH_VERSION}:
                    raise RunConflict("任务流程版本不兼容，请重新生成。")
                lease.update(status="running", error=None, failed_node=None)
                snapshot = graph.get_state(config)
                state = snapshot.values
                if job.get("upgrade_document") and "content_version" not in state:
                    document = job["upgrade_document"]
                    graph.update_state(config, {
                        "formal_resume": document, "content_version": 1,
                        "reviewed_content_version": 0, "content_hash": content_hash(document),
                        "reviewed_content_hash": "", "content_report": {},
                        "decision": "edit", "status": "reviewing",
                    }, as_node="human_decision")
                    snapshot = graph.get_state(config)
                    state = snapshot.values
                initial_only = job["target"] == "initial"
                already_has_initial = initial_only and "initial_draft" in state
                if not already_has_initial and (not state or snapshot.next):
                    graph_input = None if state else job["inputs"]
                    pending = job.get("pending_action")
                    if pending and state.get("last_action_id") != pending["value"]["request_id"]:
                        graph_input = Command(resume={pending["interrupt_id"]: pending["value"]})
                    for state in graph.stream(
                        graph_input, config=config, stream_mode="values", durability="sync",
                        interrupt_after=["generate_initial_draft"] if initial_only else None,
                    ):
                        self._progress(lease, thread_id, state)
                # The final values event may be an interrupt-only notification.
                snapshot = graph.get_state(config)
                state = snapshot.values
                before_node("finalize")
                self._progress(lease, thread_id, state)
                if not lease.begin_finalizing():
                    raise RunCancelled("任务已取消。")
                if initial_only:
                    lease.update(status="initial_ready", current_node=None)
                else:
                    response = self._result(job, state)
                    interrupts = [item for task in snapshot.tasks for item in task.interrupts]
                    lease.update(status=state["status"], result=response.model_dump(mode="json"), current_node=None,
                                 pending_action=None, upgrade_document=None,
                                 pending_decision=interrupts[0].value if interrupts else None)
            except RunCancelled:
                lease.update(status="cancelled", current_node=None, error=None)
            except RunPaused:
                lease.update(status="queued")
            except Exception as exc:
                # With a broken lock connection this update also fails. The row
                # stays running and the next worker resumes its checkpoint.
                current = lease.read()
                if current["cancel_requested"]:
                    lease.update(status="cancelled", current_node=None, error=None)
                else:
                    lease.update(status="failed", failed_node=current.get("current_node"), error=str(exc)[:1000])
            return lease.read()

    def _progress(self, lease, thread_id, state):
        if "jd" not in state:
            return
        updates = {key: state.get(key, 0) for key in ("revision_count", "draft_version", "audit_version")}
        updates.update({key: state[key] for key in (
            "content_version", "reviewed_content_version", "content_hash", "reviewed_content_hash",
        ) if key in state})
        if "formal_resume" in state:
            updates["current_document"] = state["formal_resume"]
        if "initial_draft" in state and lease.read().get("preview") is None:
            from app.schemas.tailoring import TailoredResumeDraft
            draft = TailoredResumeDraft.model_validate(state["initial_draft"])
            preview = TailoringInitialBuildResponse(
                thread_id=thread_id, match_report=state["match_report"], draft=draft,
                formal_resume=assemble_formal_resume(
                    ParsedJD.model_validate(state["jd"]), ParsedResume.model_validate(state["resume"]), draft,
                ),
            )
            updates["preview"] = preview.model_dump(mode="json")
        lease.update(**updates)

    def _result(self, job, state):
        jd = ParsedJD.model_validate(state["jd"])
        resume = ParsedResume.model_validate(state["resume"])
        match, initial, first_report, revised, final_report = tailoring_result(state)
        formal = assemble_formal_resume(jd, resume, revised)
        if "formal_resume" in state:
            formal = FormalResumeDocument.model_validate(state["formal_resume"])
            from app.schemas.tailoring import FactCheckReport
            final_report = FactCheckReport.model_validate(state["content_report"])
        saved = None
        if state["status"] in {"awaiting_confirmation", "completed"} or job.get("accepted_action") or job.get("upgrade_document"):
            saved = save_tailored_resume(
                jd, resume, revised, initial_draft=initial, formal_resume=formal,
                match_report=match, fact_check_report=first_report, final_fact_check_report=final_report,
                tailored_resume_id="tailored_" + job["thread_id"].removeprefix("tailoring_"),
                generated_at=job["created_at"],
            )
            if "content_version" in state:
                saved = project_workflow_resume(saved, job["thread_id"], state)
            formal = saved.formal_resume or formal
        return TailoringBuildResponse(
            thread_id=job["thread_id"], status=state["status"], revision_count=state["revision_count"],
            match_report=match, initial_draft=initial, draft=revised,
            fact_check_report=first_report, final_fact_check_report=final_report,
            formal_resume=formal, saved_resume=saved,
        )

    def start_workers(self, count=2, poll_seconds=1):
        if self.workers:
            return
        if not 1 <= count <= 4:
            raise ValueError("TAILORING_WORKERS must be between 1 and 4.")
        self.stop_event.clear()

        def work():
            while not self.stop_event.is_set():
                did_work = False
                try:
                    for thread_id in self.store.pending():
                        if self.stop_event.is_set():
                            break
                        try:
                            self.execute(thread_id)
                            did_work = True
                            break
                        except RunBusy:
                            continue
                except Exception:
                    logger.warning("Workflow executor could not access task storage; retrying.")
                if not did_work:
                    self.stop_event.wait(poll_seconds)

        for index in range(count):
            worker = Thread(target=work, name=f"tailoring-worker-{index}", daemon=True)
            self.workers.append(worker)
            worker.start()

    def close(self):
        self.stop_event.set()
        for worker in self.workers:
            worker.join(timeout=130)
        self.workers.clear()
        self.store.close()


_runtime = None
_runtime_lock = Lock()


def get_tailoring_runtime():
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = TailoringRuntime(store=create_run_store())
        return _runtime


def start_tailoring_runtime():
    runtime = get_tailoring_runtime()
    runtime.start_workers(count=int(os.getenv("TAILORING_WORKERS", "2")))
    return runtime


def close_tailoring_runtime():
    global _runtime
    with _runtime_lock:
        if _runtime is not None:
            _runtime.close()
            _runtime = None
