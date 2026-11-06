from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.database import get_session_factory
from app.logging_utils import current_request_id, log_event
from app.models.episode import Episode
from app.repositories.jobs import JobRepository
from app.repositories.memory_units import MemoryUnitRepository
from app.services.consolidation import ConsolidationService
from app.services.lifecycle import LifecycleService
from app.telemetry.metrics import increment_metric


logger = logging.getLogger(__name__)


class WorkerRunner:
    @classmethod
    def run_pending_jobs(cls) -> int:
        session_factory = get_session_factory()
        processed = 0
        session: Session = session_factory()
        try:
            jobs = JobRepository(session)
            consolidation = ConsolidationService(session)
            lifecycle = LifecycleService(session)
            memory_units = MemoryUnitRepository(session)

            for job in jobs.list_pending():
                token = current_request_id.set((job.payload_json or {}).get("request_id"))
                jobs.mark_running(job)
                log_event(
                    logger,
                    "worker.job.started",
                    job_id=job.id,
                    job_type=job.job_type,
                    payload=job.payload_json,
                )
                try:
                    if job.job_type == "memory_consolidation":
                        episode = session.get(Episode, job.payload_json["episode_id"])
                        if episode is None:
                            raise LookupError("Episode not found for consolidation job")
                        space_type = job.payload_json["space_type"]
                        consolidation.consolidate_episode(episode, space_type)
                    elif job.job_type in {"memory_decay", "memory_eviction"}:
                        memory_unit = memory_units.get_by_id(job.payload_json["memory_unit_id"])
                        if memory_unit is None:
                            raise LookupError("Memory unit not found for lifecycle job")
                        lifecycle.apply_transition(
                            memory_unit=memory_unit,
                            space_type=job.payload_json["space_type"],
                        )
                    jobs.mark_completed(job)
                    increment_metric("jobs_processed_total")
                    session.commit()
                    processed += 1
                    log_event(
                        logger,
                        "worker.job.completed",
                        job_id=job.id,
                        job_type=job.job_type,
                        processed=processed,
                    )
                except Exception as exc:  # noqa: BLE001
                    jobs.mark_failed(job, str(exc))
                    increment_metric("jobs_failed_total")
                    session.commit()
                    log_event(
                        logger,
                        "worker.job.failed",
                        level=logging.ERROR,
                        job_id=job.id,
                        job_type=job.job_type,
                        payload=job.payload_json,
                        error_type=type(exc).__name__,
                    )
                    raise
                finally:
                    current_request_id.reset(token)
            return processed
        finally:
            session.close()
