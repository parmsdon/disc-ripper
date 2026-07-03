"""
Shared encode-job queue logic used by both ripper_service (to populate
encode_jobs after a successful rip) and encoder_service (to fetch the next
batch of work).
"""

import logging
from sqlalchemy import select, exists, or_
from sqlalchemy.orm import aliased

from common.models import CDTrack, EncodeJob, EncodeProfile, JobStatus, naive_utcnow

logger = logging.getLogger(__name__)


def create_encode_jobs(session, disc_id: int, media_type: str) -> int:
    """
    Creates EncodeJobs for all enabled EncodeProfiles matching media_type.

    DVD: one job per profile (no track_id).
    CD:  one job per profile per CDTrack.

    Idempotent: skips any disc+profile(+track) combination that already has a
    non-error job (queued, running, or done). Error jobs can be superseded by
    a new queued job on re-call.

    Commits the session and returns the count of new jobs created.
    """
    profiles = session.scalars(
        select(EncodeProfile)
        .where(EncodeProfile.media_type == media_type, EncodeProfile.enabled == True)
        .order_by(EncodeProfile.display_order)
    ).all()

    if not profiles:
        return 0

    created = 0
    now = naive_utcnow()

    if media_type == "dvd":
        for profile in profiles:
            already = session.scalar(
                select(EncodeJob.id).where(
                    EncodeJob.disc_id == disc_id,
                    EncodeJob.profile_id == profile.id,
                    EncodeJob.status != JobStatus.error,
                ).limit(1)
            )
            if already is not None:
                continue
            session.add(EncodeJob(
                disc_id=disc_id,
                profile_id=profile.id,
                status=JobStatus.queued,
                created_at=now,
            ))
            created += 1

    elif media_type == "cd":
        tracks = session.scalars(
            select(CDTrack)
            .where(CDTrack.disc_id == disc_id)
            .order_by(CDTrack.track_number)
        ).all()
        for profile in profiles:
            for track in tracks:
                already = session.scalar(
                    select(EncodeJob.id).where(
                        EncodeJob.disc_id == disc_id,
                        EncodeJob.profile_id == profile.id,
                        EncodeJob.track_id == track.id,
                        EncodeJob.status != JobStatus.error,
                    ).limit(1)
                )
                if already is not None:
                    continue
                session.add(EncodeJob(
                    disc_id=disc_id,
                    profile_id=profile.id,
                    track_id=track.id,
                    status=JobStatus.queued,
                    created_at=now,
                ))
                created += 1

    else:
        logger.warning("create_encode_jobs called with unknown media_type %r", media_type)
        return 0

    session.commit()
    logger.info("Created %d encode job(s) for disc #%s (%s)", created, disc_id, media_type)
    return created


def get_next_encode_jobs(
    session, media_type: str, max_jobs: int, running_job_ids: list[int]
) -> list:
    """
    Returns up to max_jobs queued EncodeJobs for the given media_type where:
    - status = queued
    - id NOT IN running_job_ids
    - dependency satisfied: depends_on_profile_id is NULL, OR the dependency
      profile's EncodeJob on the same disc has status = done

    Results are ordered oldest-first (by created_at).
    """
    if max_jobs <= 0:
        return []

    dep_job = aliased(EncodeJob)

    dep_satisfied = or_(
        EncodeProfile.depends_on_profile_id.is_(None),
        exists(
            select(dep_job.id).where(
                dep_job.disc_id == EncodeJob.disc_id,
                dep_job.profile_id == EncodeProfile.depends_on_profile_id,
                dep_job.status == JobStatus.done,
            )
        ),
    )

    filters = [
        EncodeJob.status == JobStatus.queued,
        EncodeProfile.media_type == media_type,
        dep_satisfied,
    ]
    if running_job_ids:
        filters.append(EncodeJob.id.not_in(running_job_ids))

    stmt = (
        select(EncodeJob)
        .join(EncodeProfile, EncodeJob.profile_id == EncodeProfile.id)
        .where(*filters)
        .order_by(EncodeJob.created_at)
        .limit(max_jobs)
    )

    return list(session.scalars(stmt).all())
