from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends

from backend.consts.notes import PROJECTS_ROOT
from backend.errors import ClientError
from backend.models import LinkKind, ProjectLink
from backend.repositories.link import ProjectLinkRepository, projectLinkRepositoryDI
from backend.schemas import ProjectLinkPublic, ProjectLinkWrite, ProjectMerge
from backend.services import ServiceImpl
from backend.services.projects import projectServiceDI

OVERVIEW_STEM = "overview"

if TYPE_CHECKING:
    from collections.abc import Sequence

    from backend.models import Job
    from backend.schemas import LinkVerdict, SessionNote


class LinkService(ServiceImpl[ProjectLinkRepository]):
    repository: projectLinkRepositoryDI
    projects: projectServiceDI

    async def write(self, payload: ProjectLinkWrite) -> ProjectLinkPublic:
        source = await self.projects.resolve(payload.source)
        target = await self.projects.resolve(payload.target)
        if payload.kind is LinkKind.PART_OF and await self.repository.would_cycle(source, target):
            ClientError.LINK_WOULD_CYCLE.format_msg(source=source, target=target).raise_()
        existing = await self.repository.find(source, target)
        link = existing or ProjectLink(kind=payload.kind, source=source, target=target)
        link.kind = payload.kind
        link.source, link.target = source, target
        link.description = payload.description or link.description
        link.stated_by_user = payload.stated_by_user or link.stated_by_user
        link.confirmed = payload.confirmed
        link.unrelated = False
        link.summarizer = payload.summarizer or link.summarizer
        return ProjectLinkPublic.model_validate(await self.repository.save(link), from_attributes=True)

    async def listed(self, project: str | None) -> Sequence[ProjectLink]:
        found = await self.repository.all_links() if project is None else await self.repository.around(await self.projects.resolve(project))
        return [link for link in found if not link.unrelated]

    async def standing(self, project: str) -> list[ProjectLink]:
        return [link for link in await self.repository.around(project) if not link.unrelated]

    async def drop(self, source: str, target: str) -> bool:
        link = await self.repository.find(await self.projects.resolve(source), await self.projects.resolve(target))
        if link is None:
            return False
        await self.repository.session.delete(link)
        await self.repository.session.commit()
        return True

    async def absorb(self, job: Job, notes: list[SessionNote], summarizer: str) -> int:
        stored = 0
        for note in notes:
            owner = await self.projects.resolve(note.project or job.project)
            for link in (candidate for item in note.items for candidate in item.links):
                other = await self.projects.resolve(link.project)
                if other == owner or await self.repository.find(owner, other) is not None:
                    continue
                kind = await self.repository.settled(LinkKind(link.relation), owner, other)
                saved = ProjectLink(kind=kind, source=owner, target=other, description=link.description, summarizer=summarizer)
                await self.repository.save(saved)
                stored += 1
        return stored

    async def judged(self, pair: str, verdict: LinkVerdict, summarizer: str) -> bool:
        left, _, right = pair.partition("~")
        if not left or not right:
            return False
        existing = await self.repository.find(left, right)
        if existing is not None and existing.confirmed:
            return False
        unrelated = verdict.verdict == "unrelated"
        source, target = (right, left) if verdict.part == "b" and not unrelated else (left, right)
        link = existing or ProjectLink(kind=LinkKind.RELATED, source=source, target=target)
        link.kind = LinkKind.RELATED if unrelated else await self.repository.settled(LinkKind(verdict.verdict), source, target)
        link.source, link.target = source, target
        link.description = "" if unrelated else verdict.description
        link.unrelated = unrelated
        link.confirmed = False
        link.summarizer = summarizer
        await self.repository.save(link)
        return not unrelated

    async def apply(self, source: str, target: str, *, reverse: bool = False) -> ProjectLinkPublic:
        link = await self.repository.find(await self.projects.resolve(source), await self.projects.resolve(target))
        if link is None:
            ClientError.RESOURCE_NOT_FOUND.format_msg(resource=self.repository.resource).raise_()
        if link.kind is LinkKind.SAME:
            held, holder = (link.target, link.source) if reverse else (link.source, link.target)
            await self.projects.merge(ProjectMerge(source=held, target=holder))
            await self.repository.session.delete(link)
            await self.repository.session.commit()
            return ProjectLinkPublic.model_validate(link, from_attributes=True)
        if reverse:
            if link.kind is LinkKind.PART_OF and await self.repository.would_cycle(link.target, link.source):
                ClientError.LINK_WOULD_CYCLE.format_msg(source=link.target, target=link.source).raise_()
            link.source, link.target = link.target, link.source
        link.confirmed = True
        await self.projects.request_overview(link.source)
        await self.projects.request_overview(link.target)
        return ProjectLinkPublic.model_validate(await self.repository.save(link), from_attributes=True)

    async def statements(self, source: str, target: str) -> str:
        link = await self.repository.find(source, target)
        return link.stated_by_user if link else ""

    @staticmethod
    def reads(link: ProjectLink, project: str, other: str) -> str:
        if link.kind is not LinkKind.PART_OF:
            return f"{other} is the same work" if link.kind is LinkKind.SAME else f"{other} is related"
        return f"this project is part of {other}" if link.source == project else f"{other} is part of this project"

    async def neighbours(self, project: str) -> list[str]:
        lines = []
        for link in await self.standing(project):
            other = link.target if link.source == project else link.source
            where = f"{PROJECTS_ROOT}/{other}/{OVERVIEW_STEM}"
            lines.append(f"- {self.reads(link, project, other)}, summarized in `{where}`: {link.description}")
        return lines

    async def holders(self, project: str) -> list[str]:
        found = await self.standing(project)
        return sorted({link.target for link in found if link.kind is LinkKind.PART_OF and link.source == project})

    async def linked(self, project: str) -> list[str]:
        found = await self.standing(project)
        return sorted({link.target if link.source == project else link.source for link in found})


linkServiceDI = Annotated[LinkService, Depends(LinkService)]  # noqa: N816
