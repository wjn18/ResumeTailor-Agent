from typing import Any, TypedDict
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

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


def build_tailoring_graph(client=None, checkpointer=None):
    # Imported lazily to avoid making the existing domain service depend on graph setup.
    from app.services.tailoring import (
        SUPPORTED,
        fact_check_resume,
        match_requirements,
        rank_and_filter_draft,
        revise_after_fact_check,
        rewrite_resume,
    )

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
        return {"initial_draft": draft.model_dump(mode="json")}

    def initial_fact_check_node(state: TailoringState):
        jd, resume = models(state)
        draft = TailoredResumeDraft.model_validate(state["initial_draft"])
        report = fact_check_resume(jd.jd_id, resume, draft, client=client)
        return {"fact_check_report": report.model_dump(mode="json")}

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
        }

    def keep_node(state: TailoringState):
        return {"revised_draft": state["initial_draft"], "revision_count": 0}

    def final_fact_check_node(state: TailoringState):
        jd, resume = models(state)
        revised = TailoredResumeDraft.model_validate(state["revised_draft"])
        report = fact_check_resume(jd.jd_id, resume, revised, client=client)
        return {"final_fact_check_report": report.model_dump(mode="json")}

    def needs_follow_up_revision(state: TailoringState):
        report = FactCheckReport.model_validate(state["final_fact_check_report"])
        unsupported = any(item.support_status != SUPPORTED for item in report.checks)
        return "revise" if unsupported and state.get("revision_count", 0) < 2 else "done"

    builder = StateGraph(TailoringState)
    builder.add_node("match_requirements", match_node)
    builder.add_node("generate_initial_draft", draft_node)
    builder.add_node("fact_check_initial", initial_fact_check_node)
    builder.add_node("revise_draft", revise_node)
    builder.add_node("keep_draft", keep_node)
    builder.add_node("fact_check_final", final_fact_check_node)
    builder.add_edge(START, "match_requirements")
    builder.add_edge("match_requirements", "generate_initial_draft")
    builder.add_edge("generate_initial_draft", "fact_check_initial")
    builder.add_conditional_edges(
        "fact_check_initial", needs_revision, {"revise": "revise_draft", "keep": "keep_draft"}
    )
    builder.add_edge("revise_draft", "fact_check_final")
    builder.add_edge("keep_draft", "fact_check_final")
    builder.add_conditional_edges(
        "fact_check_final",
        needs_follow_up_revision,
        {"revise": "revise_draft", "done": END},
    )
    return builder.compile(checkpointer=checkpointer)


def run_tailoring_graph(jd: ParsedJD, resume: ParsedResume, client=None):
    graph = build_tailoring_graph(client=client, checkpointer=InMemorySaver())
    result = graph.invoke(
        {"jd": jd.model_dump(mode="json"), "resume": resume.model_dump(mode="json")},
        config={"configurable": {"thread_id": f"tailoring_{uuid4().hex}"}},
    )
    return (
        RequirementMatchReport.model_validate(result["match_report"]),
        TailoredResumeDraft.model_validate(result["initial_draft"]),
        FactCheckReport.model_validate(result["fact_check_report"]),
        TailoredResumeDraft.model_validate(result["revised_draft"]),
        FactCheckReport.model_validate(result["final_fact_check_report"]),
    )
