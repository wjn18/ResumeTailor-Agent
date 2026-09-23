"""Presentation rules shared by resume generation and local fallback."""

import re

from app.schemas.tailoring import TailoredSentence


SKILL_WRITING_RULES = """
Related skill requirements:
- Classify skills by meaning: programming languages, frameworks and tools,
  databases, engineering concepts, AI methods, and other relevant domains.
  Treat stored category as a hint; determine the actual type from cited facts.
- Merge related items into one coherent Chinese sentence per category. Within a
  category, combine items of the same proficiency under one proficiency phrase.
- Use the stored proficiency exactly; never upgrade it. For example, when the
  evidence supports it: "编程语言：熟练使用 Python、Java、C# 进行开发";
  if Python is only 熟悉, use a separate 熟悉 clause, not 熟练 for the whole group.
- Describe concepts with "熟悉" or "了解", not "熟悉使用". For example:
  "工程概念：熟悉面向对象编程、事件与委托、有限状态机".
- Generic fragments such as "接口" are not standalone skills. Omit ambiguous
  fragments, or merge them into a specific capability only when facts support it.
  Business modules such as "用户认证" belong in an evidence-backed backend
  capability sentence, not a standalone "熟悉使用 用户认证" item.
- Consolidate aliases and overlapping skills (e.g. Python and Python 爬虫),
  retaining supported detail without repeating the same proficiency phrase.
- Cover evidence-supported concrete skills and concepts. A skill must still appear here when it is already mentioned
  in personal advantages. Unsupported or ambiguous fragments may be omitted.
- Order categories by JD relevance, finally broadly useful skills such as Excel.
- Use Chinese commas, enumeration commas, semicolons, or separate sentences.
  Never use slash characters ("/" or "／") to separate skills or clauses;
  write "事件与委托" rather than "事件/委托".
- Every grouped sentence must cite the union of supporting source_fact_ids for
  all included items. Do not invent abilities, workflows, or outcomes.
""".strip()


def resume_action_text(text: str, name: str | None) -> str:
    """Remove the candidate's leading subject, leaving other people's names intact."""
    if not name or not name.strip():
        return text.strip()
    name = name.strip()
    boundary = r"(?![A-Za-z])" if name[-1].isascii() else ""
    return re.sub(
        rf"(^|[。！？]\s*){re.escape(name)}{boundary}"
        rf"(?:的(?=项目|工作|职责|任务)|(?!的|与|和|及))[\s，,:：]*",
        r"\1", text.strip(),
    ).strip()


def skill_prose(text: str) -> str:
    text = re.sub(r"事件\s*[/／]\s*委托", "事件与委托", text.strip())
    return re.sub(r"\s*[/／]\s*", "、", text)


def skill_group(name: str, category: str | None) -> str:
    """Conservative categories for offline fallback; the LLM classifies freely."""
    key = name.casefold()
    if key in {"python", "java", "c#", "c++", "c", "javascript", "typescript", "go", "rust", "swift", "kotlin", "ruby", "php"}:
        return "编程语言"
    if any(word in key for word in ("sql", "数据库", "数据表")):
        return "数据库"
    if key in {"unity", "comfyui", "codex", "git", "excel", "fastapi", "langgraph", "pytorch", "requests", "beautifulsoup", "pandas", "cheatengine"}:
        return "框架与工具"
    categories = {
        "programming_language": "编程语言", "language": "编程语言", "编程语言": "编程语言",
        "framework": "框架与工具", "tool": "框架与工具", "工具": "框架与工具", "框架": "框架与工具",
        "database": "数据库", "数据库": "数据库",
    }
    return categories.get((category or "").casefold(), "技术概念与实践")


def group_fallback_skills(skills: list[dict]) -> list[TailoredSentence]:
    groups: dict[str, dict[str, list[dict]]] = {}
    for skill in skills:
        name = skill["name"].strip()
        if name in {"接口", "模块", "系统", "功能", "开发"}:
            continue
        group = skill_group(name, skill.get("category"))
        groups.setdefault(group, {}).setdefault(skill["proficiency"], []).append(skill)
    result = []
    for group, levels in groups.items():
        clauses = []
        evidence = []
        for level, items in levels.items():
            names = "、".join(skill_prose(item["name"]) for item in items)
            prefix = level
            if group in {"编程语言", "框架与工具"} and level in {"熟悉", "熟练"}:
                prefix += "使用"
            suffix = " 进行开发" if group == "编程语言" and level in {"熟悉", "熟练"} else ""
            clauses.append(f"{prefix} {names}{suffix}")
            evidence.extend(fact_id for item in items for fact_id in item["evidence_fact_ids"])
        result.append(TailoredSentence(
            section="related_skills", sentence=f"{group}：{'；'.join(clauses)}。",
            source_fact_ids=list(dict.fromkeys(evidence)),
        ))
    return result
