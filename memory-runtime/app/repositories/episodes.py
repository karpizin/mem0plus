from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, case, literal, or_, select
from sqlalchemy.orm import Session

from app.models.episode import Episode
from app.models.memory_space import MemorySpace


@dataclass(slots=True, frozen=True)
class RecallEpisodeRow:
    id: str
    summary: str
    raw_text: str
    importance_hint: str
    created_at: datetime
    session_id: str | None


RECALL_FETCH_LIMIT = 256


class EpisodeRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(
        self,
        *,
        namespace_id: str,
        agent_id: str | None,
        space_id: str | None,
        session_id: str | None,
        start_event_id: str,
        end_event_id: str,
        summary: str,
        raw_text: str,
        token_count: int,
        importance_hint: str,
    ) -> Episode:
        episode = Episode(
            namespace_id=namespace_id,
            agent_id=agent_id,
            space_id=space_id,
            session_id=session_id,
            start_event_id=start_event_id,
            end_event_id=end_event_id,
            summary=summary,
            raw_text=raw_text,
            token_count=token_count,
            importance_hint=importance_hint,
        )
        self.session.add(episode)
        self.session.flush()
        return episode

    def list_for_recall(
        self,
        *,
        namespace_id: str,
        agent_id: str | None,
        session_id: str | None,
        space_types: list[str],
        query_tokens: list[str] | None = None,
        limit: int = RECALL_FETCH_LIMIT,
    ) -> list[tuple[RecallEpisodeRow, str]]:
        stmt: Select[tuple[str, str, str, str, datetime, str | None, str]] = (
            select(
                Episode.id,
                Episode.summary,
                Episode.raw_text,
                Episode.importance_hint,
                Episode.created_at,
                Episode.session_id,
                MemorySpace.space_type,
            )
            .join(MemorySpace, Episode.space_id == MemorySpace.id)
            .where(Episode.namespace_id == namespace_id)
            .where(MemorySpace.space_type.in_(space_types))
        )

        if agent_id is not None:
            stmt = stmt.where(
                (Episode.agent_id == agent_id)
                | (Episode.agent_id.is_(None))
                | (MemorySpace.space_type == "shared-space")
            )

        overlap_score = self._query_overlap_expression(query_tokens or [])

        if session_id is not None:
            stmt = stmt.order_by(
                (Episode.session_id == session_id).desc(),
                overlap_score.desc(),
                Episode.created_at.desc(),
            )
        else:
            stmt = stmt.order_by(overlap_score.desc(), Episode.created_at.desc())

        stmt = stmt.limit(limit)

        return [
            (
                RecallEpisodeRow(
                    id=episode_id,
                    summary=summary,
                    raw_text=raw_text,
                    importance_hint=importance_hint,
                    created_at=created_at,
                    session_id=row_session_id,
                ),
                space_type,
            )
            for episode_id, summary, raw_text, importance_hint, created_at, row_session_id, space_type in self.session.execute(stmt).all()
        ]

    @staticmethod
    def _query_overlap_expression(query_tokens: list[str]):
        normalized_tokens = sorted({token.lower() for token in query_tokens if len(token) >= 4})[:8]
        overlap_score = literal(0)
        for token in normalized_tokens:
            pattern = f"%{token}%"
            overlap_score = overlap_score + case(
                (
                    or_(
                        Episode.summary.ilike(pattern),
                        Episode.raw_text.ilike(pattern),
                    ),
                    1,
                ),
                else_=0,
            )
        return overlap_score

    def list_by_session(
        self,
        *,
        namespace_id: str,
        agent_id: str | None,
        session_id: str,
    ) -> list[tuple[Episode, str]]:
        stmt: Select[tuple[Episode, str]] = (
            select(Episode, MemorySpace.space_type)
            .join(MemorySpace, Episode.space_id == MemorySpace.id)
            .where(Episode.namespace_id == namespace_id)
            .where(Episode.session_id == session_id)
            .order_by(Episode.created_at.desc())
        )
        if agent_id is not None:
            stmt = stmt.where(Episode.agent_id == agent_id)
        return list(self.session.execute(stmt).all())

    def list_by_namespace(
        self,
        *,
        namespace_id: str,
        agent_id: str | None,
    ) -> list[tuple[Episode, str]]:
        stmt: Select[tuple[Episode, str]] = (
            select(Episode, MemorySpace.space_type)
            .join(MemorySpace, Episode.space_id == MemorySpace.id)
            .where(Episode.namespace_id == namespace_id)
            .order_by(Episode.created_at.desc())
        )
        if agent_id is not None:
            stmt = stmt.where(Episode.agent_id == agent_id)
        return list(self.session.execute(stmt).all())

    def get_by_id(self, episode_id: str) -> Episode | None:
        return self.session.get(Episode, episode_id)

    def get_by_event_id(self, event_id: str) -> Episode | None:
        stmt = (
            select(Episode)
            .where(Episode.start_event_id == event_id)
            .where(Episode.end_event_id == event_id)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def update_content(
        self,
        episode: Episode,
        *,
        raw_text: str,
        summary: str,
        token_count: int,
    ) -> Episode:
        episode.raw_text = raw_text
        episode.summary = summary
        episode.token_count = token_count
        self.session.flush()
        return episode

    def delete(self, episode: Episode) -> None:
        self.session.delete(episode)
        self.session.flush()
