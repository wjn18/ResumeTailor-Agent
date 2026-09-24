"""Project URLs and conservative migration of historical contact links."""

import re

from pydantic import BaseModel


class ProjectLink(BaseModel):
    url: str
    label: str | None = None


URL_PATTERN = re.compile(r"(?:https?://|www\.)[^\s<>\"，。；、]+", re.IGNORECASE)
LINK_CONTACT_TYPES = {"website", "github", "linkedin", "url", "link", "portfolio"}


def is_link_contact(contact) -> bool:
    return (
        contact.contact_type.strip().casefold() in LINK_CONTACT_TYPES
        or bool(URL_PATTERN.search(contact.contact_value))
    )


def extract_links(text: str) -> list[str]:
    return [match.group().rstrip(".,;)]）】") for match in URL_PATTERN.finditer(text)]


def merge_links(*collections: list[ProjectLink]) -> list[ProjectLink]:
    result = {}
    for collection in collections:
        for link in collection:
            key = link.url.strip()
            if key and key not in result:
                result[key] = link.model_copy(update={"url": key})
    return list(result.values())


def _name_key(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.casefold())


def relocate_project_contacts(projects, contacts):
    """Only assign URLs when an explicit project name identifies one owner.

    Unmatched/ambiguous contacts remain in data; presentation hides web links.
    Never infer project ownership from a URL hostname or a repository slug.
    """
    remaining = []
    for contact in contacts:
        urls = extract_links(contact.contact_value)
        context = _name_key(contact.label or "")
        matches = [
            project for project in projects
            if _name_key(project.name) and _name_key(project.name) in context
        ]
        if matches:
            longest = max(len(_name_key(project.name)) for project in matches)
            matches = [p for p in matches if len(_name_key(p.name)) == longest]
        if not urls or len(matches) != 1:
            remaining.append(contact)
            continue
        project = matches[0]
        project.links = merge_links(project.links, [
            ProjectLink(url=url, label=contact.label) for url in urls
        ])
    return remaining
