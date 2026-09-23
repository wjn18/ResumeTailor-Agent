from typing import Any, TypedDict
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.schemas.jds import ParsedJD
from app.schemas.resumes import ParsedResume
from app.schemas.tailoring import (
    FactCheckReport,
    RequirementMatchReport,
    TailoredResumeDraft,
)


class TailoringState(TypedDict, total=False):
    jd: dict[str, Any]
    resume: dict[str, Any]
    match_report: dict[str, Any]
    initial_draft: dict[str, Any]
    fact_check_report: dict[str, Any]
    revised_draft: dict[str, Any]
    final_fact_check_report: dict[str, Any]
    revision_count: int
    status: str
    review_only: bool
    audited_draft: dict[str, Any]
    latest_report: dict[str, Any]
    draft_version: int
    audit_version: int
    formal_resume: dict[str, Any]
    content_version: int
    reviewed_content_version: int
    content_hash: str
    reviewed_content_hash: str
    content_report: dict[str, Any]
    last_action_id: str
    decision: str


def build_tailoring_graph(client=None, checkpointer=None, before_node=None, *, human_loop=False):
    # Imported lazily to avoid making the existing domain service depend on graph setup.
    from app.services.tailoring import (
        SUPPORTED,
        fact_check_resume,
        match_requirements,
        rank_and_filter_draft,
        revise_after_fact_check,
        rewrite_resume,
        assemble_formal_resume,
    )
    from app.schemas.tailoring import FormalResumeDocument
    from app.services.formal_resume_review import content_hash, review_formal_resume
    from app.storage.workflow_base import RunConflict

    def models(state: TailoringState):
        return ParsedJD.model_validate(state["jd"]), ParsedResume.model_validate(state["resume"])

    def match_node(state: TailoringState):
        jd, resume = models(state)
        report = match_requirements(jd, resume, client=client)
        return {"match_report": report.model_dump(mode="json")}

    def draft_node(state: TailoringState):
        jd, resume = models(state)
        report = RequirementMatchReport.model_validate(state["match_report"])
        draft = rewrite_resume(jd, resume, match_report=report, client=client)
        return {
            "initial_draft": draft.model_dump(mode="json"),
            "revision_count": 0,
            "status": "initial_ready",
            "draft_version": 1,
            "audit_version": 0,
        }

    def initial_fact_check_node(state: TailoringState):
        jd, resume = models(state)
        draft = TailoredResumeDraft.model_validate(state["initial_draft"])
        report = fact_check_resume(jd.jd_id, resume, draft, client=client)
        return {
            "fact_check_report": report.model_dump(mode="json"),
            "audited_draft": state["initial_draft"],
            "latest_report": report.model_dump(mode="json"),
            "status": "reviewing",
            "audit_version": state.get("draft_version", 1),
        }

    def needs_revision(state: TailoringState):
        report = FactCheckReport.model_validate(state["fact_check_report"])
        return "revise" if any(item.support_status != SUPPORTED for item in report.checks) else "keep"

    def revise_node(state: TailoringState):
        jd, resume = models(state)
        match_report = RequirementMatchReport.model_validate(state["match_report"])
        is_follow_up = state.get("revision_count", 0) > 0
        current = TailoredResumeDraft.model_validate(
            state["revised_draft"] if is_follow_up else state["initial_draft"]
        )
        report = FactCheckReport.model_validate(
            state["final_fact_check_report"] if is_follow_up else state["fact_check_report"]
        )
        revised = revise_after_fact_check(jd, resume, current, report, client=client)
        revised = rank_and_filter_draft(revised, match_report, jd, resume)
        return {
            "revised_draft": revised.model_dump(mode="json"),
            "revision_count": state.get("revision_count", 0) + 1,
            "draft_version": state.get("draft_version", 1) + (revised != current),
        }

    def keep_node(state: TailoringState):
        return {
            "revised_draft": state["initial_draft"],
            "final_fact_check_report": state["fact_check_report"],
            "revision_count": 0,
        }

    def final_fact_check_node(state: TailoringState):
        # An unchanged draft has the same audit; do not pay for another LLM call.
        if state["revised_draft"] == state["audited_draft"]:
            return {
                "final_fact_check_report": state["latest_report"],
                "audit_version": state.get("draft_version", 1),
            }
        jd, resume = models(state)
        revised = TailoredResumeDraft.model_validate(state["revised_draft"])
        report = fact_check_resume(jd.jd_id, resume, revised, client=client)
        return {
            "final_fact_check_report": report.model_dump(mode="json"),
            "audited_draft": state["revised_draft"],
            "latest_report": report.model_dump(mode="json"),
            "audit_version": state.get("draft_version", 1),
        }

    def needs_follow_up_revision(state: TailoringState):
        report = FactCheckReport.model_validate(state["final_fact_check_report"])
        unsupported = any(item.support_status != SUPPORTED for item in report.checks)
        if not unsupported:
            return "approved"
        return "revise" if state.get("revision_count", 0) < 2 else "needs_attention"

    def prepare_document(state):
        jd, resume = models(state)
        document = assemble_formal_resume(jd, resume, TailoredResumeDraft.model_validate(state["revised_draft"]))
        digest = content_hash(document)
        return {
            "formal_resume": document.model_dump(mode="json"), "content_version": 1,
            "reviewed_content_version": 1, "content_hash": digest,
            "reviewed_content_hash": digest, "content_report": state["final_fact_check_report"],
        }

    def human_decision(state):
        decision = interrupt({
            "kind": "review_resume", "content_version": state["content_version"],
            "content_hash": state["content_hash"], "status": state["status"],
            "can_confirm": state["status"] == "awaiting_confirmation",
        })
        if decision["expected_version"] != state["content_version"]:
            raise RunConflict("正文版本已变更，请刷新后操作。")
        updates = {"last_action_id": decision["request_id"], "decision": decision["action"]}
        if decision["action"] == "edit":
            document = FormalResumeDocument.model_validate(decision["formal_resume"])
            updates.update(
                formal_resume=document.model_dump(mode="json"),
                content_version=state["content_version"] + 1, content_hash=content_hash(document),
                reviewed_content_version=0, reviewed_content_hash="", content_report={},
                status="reviewing",
            )
        elif decision["action"] != "confirm" or not can_confirm(state):
            raise RunConflict("当前正文尚未通过审核，无法确认。")
        return updates

    def can_confirm(state):
        return (state["status"] == "awaiting_confirmation"
                and state["content_version"] == state["reviewed_content_version"]
                and content_hash(state["formal_resume"]) == state["reviewed_content_hash"]
                and all(item["support_status"] == SUPPORTED for item in state["content_report"]["checks"]))

    def review_edit(state):
        jd, resume = models(state)
        report = review_formal_resume(jd, resume, FormalResumeDocument.model_validate(state["formal_resume"]), client)
        return {
            "content_report": report.model_dump(mode="json"),
            "reviewed_content_version": state["content_version"],
            "reviewed_content_hash": state["content_hash"],
            "status": "awaiting_confirmation" if all(c.support_status == SUPPORTED for c in report.checks) else "needs_attention",
        }

    def confirm_document(state):
        if not can_confirm(state):
            raise RunConflict("当前正文尚未通过审核，无法确认。")
        return {"status": "completed"}

    builder = StateGraph(TailoringState)

    def add_node(name, function):
        def guarded(state):
            if before_node is not None:
                before_node(name)
            return function(state)
        builder.add_node(name, guarded)

    add_node("match_requirements", match_node)
    add_node("generate_initial_draft", draft_node)
    add_node("fact_check_initial", initial_fact_check_node)
    add_node("revise_draft", revise_node)
    add_node("keep_draft", keep_node)
    add_node("fact_check_final", final_fact_check_node)
    add_node("approved", lambda state: {"status": "awaiting_confirmation"})
    add_node("needs_attention", lambda state: {"status": "needs_attention"})
    # The review-only entry is used by legacy Python service callers. HTTP callers
    # resume the existing checkpoint instead of supplying draft state again.
    builder.add_conditional_edges(
        START,
        lambda state: "review" if state.get("review_only") else "build",
        {"review": "fact_check_initial", "build": "match_requirements"},
    )
    builder.add_edge("match_requirements", "generate_initial_draft")
    builder.add_edge("generate_initial_draft", "fact_check_initial")
    builder.add_conditional_edges(
        "fact_check_initial", needs_revision, {"revise": "revise_draft", "keep": "keep_draft"}
    )
    builder.add_edge("revise_draft", "fact_check_final")
    builder.add_edge("keep_draft", "approved")
    builder.add_conditional_edges(
        "fact_check_final",
        needs_follow_up_revision,
        {"revise": "revise_draft", "approved": "approved", "needs_attention": "needs_attention"},
    )
    if human_loop:
        add_node("prepare_document", prepare_document)
        add_node("human_decision", human_decision)
        add_node("review_edited_document", review_edit)
        add_node("confirm_document", confirm_document)
        builder.add_edge("approved", "prepare_document")
        builder.add_edge("needs_attention", "prepare_document")
        builder.add_edge("prepare_document", "human_decision")
        builder.add_conditional_edges("human_decision", lambda state: state["decision"],
                                      {"edit": "review_edited_document", "confirm": "confirm_document"})
        builder.add_edge("review_edited_document", "human_decision")
        builder.add_edge("confirm_document", "human_decision")
    else:
        builder.add_edge("approved", END)
        builder.add_edge("needs_attention", END)
    return builder.compile(checkpointer=checkpointer)


def run_tailoring_graph(jd: ParsedJD, resume: ParsedResume, client=None):
    graph = build_tailoring_graph(client=client, checkpointer=InMemorySaver())
    result = graph.invoke(
        {"jd": jd.model_dump(mode="json"), "resume": resume.model_dump(mode="json")},
        config={"configurable": {"thread_id": f"tailoring_{uuid4().hex}"}},
    )
    return tailoring_result(result)


def tailoring_result(result: TailoringState):
    return (
        RequirementMatchReport.model_validate(result["match_report"]),
        TailoredResumeDraft.model_validate(result["initial_draft"]),
        FactCheckReport.model_validate(result["fact_check_report"]),
        TailoredResumeDraft.model_validate(result["revised_draft"]),
        FactCheckReport.model_validate(result["final_fact_check_report"]),
    )


def run_initial_graph(jd: ParsedJD, resume: ParsedResume, client=None):
    graph = build_tailoring_graph(client=client, checkpointer=InMemorySaver())
    result = graph.invoke(
        {"jd": jd.model_dump(mode="json"), "resume": resume.model_dump(mode="json")},
        config={"configurable": {"thread_id": uuid4().hex}},
        interrupt_after=["generate_initial_draft"],
    )
    return (
        RequirementMatchReport.model_validate(result["match_report"]),
        TailoredResumeDraft.model_validate(result["initial_draft"]),
    )


def run_review_graph(jd, resume, match_report, initial_draft, client=None):
    graph = build_tailoring_graph(client=client)
    result = graph.invoke({
        "jd": jd.model_dump(mode="json"),
        "resume": resume.model_dump(mode="json"),
        "match_report": match_report.model_dump(mode="json"),
        "initial_draft": initial_draft.model_dump(mode="json"),
        "revision_count": 0,
        "review_only": True,
    })
    return tailoring_result(result)[2:]
