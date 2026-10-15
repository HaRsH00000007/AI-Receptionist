"""The background half of website-based setup: crawl, then structure.

A crawl plus an LLM call is tens of seconds on a good day, so this cannot live
in a request. It is a claimed job, with the same shape as every other
background job here — ``claim_jobs`` leases rows, the worker loops, the
processor does one job per call — so a worker that dies mid-crawl releases its
work when the lease lapses rather than stranding a customer on a spinner.

Nothing in this module provisions anything. It produces a *draft*: knowledge
items, suggested questions, suggested messages. No tenant, no agent, no number,
no money. Those happen only when a customer confirms, through the existing
signup pipeline.

The URL is re-validated on every attempt rather than trusted because it is
stored. A row is data we wrote, but the hostname in it came from a stranger,
and DNS answers change between the first attempt and the retry.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError, InvalidInputError
from app.core.logging import get_logger
from app.models.enums import WEBSITE_ONBOARDING_CLAIMABLE, WebsiteOnboardingStatus
from app.models.website_onboarding import WebsiteOnboarding
from app.providers.registry import Providers
from app.provisioning.retry import next_attempt_at, should_retry
from app.website import guard
from app.website.crawler import CrawlLimits, CrawlReport, crawl
from app.website.knowledge import WebsiteKnowledge, generate

logger = get_logger(__name__)

#: Fewer than provisioning's five. A crawl that fails three times is failing
#: for a reason a retry will not fix — the site is down, or it needs
#: JavaScript — and the customer is better served by being told so and offered
#: the manual form.
MAX_ATTEMPTS = 3

#: What the customer is told when something goes wrong. Never the exception
#: text: that can carry a hostname or an internal address (see
#: ``app.website.guard`` on not leaking the network).
FAILURE_MESSAGES = {
    "unreadable": (
        "We couldn't read that website. Check it opens in a browser, or set your "
        "receptionist up manually instead."
    ),
    "thin": (
        "We read that website but couldn't find enough about the business. You can add "
        "details yourself, or set your receptionist up manually."
    ),
    "generation": (
        "We read your website but couldn't turn it into receptionist notes. Trying again "
        "usually works."
    ),
    "unknown": "Something went wrong reading that website. Trying again usually works.",
}

#: The caller details every receptionist can collect, offered as toggles rather
#: than generated: they are the same for every business, and two of them are
#: how a message gets back to the owner at all.
BUILTIN_QUESTIONS: tuple[dict[str, Any], ...] = (
    {"key": "name", "label": "Name", "prompt": "Ask the caller for their name.", "builtin": True},
    {
        "key": "email",
        "label": "Email",
        "prompt": "Ask for an email address if they want written follow-up.",
        "builtin": True,
    },
    {
        "key": "phone",
        "label": "Phone number",
        "prompt": "Confirm the best callback number.",
        "builtin": True,
    },
)


@dataclass(frozen=True, slots=True)
class JobOutcome:
    """What one pass of the processor did, for the worker's counters."""

    job_id: uuid.UUID
    status: WebsiteOnboardingStatus
    pages: int = 0
    items: int = 0


async def claim_jobs(
    session: AsyncSession, *, batch_size: int, lease_s: int, now: datetime | None = None
) -> list[uuid.UUID]:
    """Lease up to ``batch_size`` jobs that are due.

    ``SKIP LOCKED`` so two workers never take the same job, and the lease is
    written as ``next_attempt_at`` — the same trick the provisioning engine
    uses — so a crashed worker's job becomes claimable again on its own.
    """
    moment = now or datetime.now(UTC)
    rows = (
        (
            await session.execute(
                select(WebsiteOnboarding)
                .where(WebsiteOnboarding.status.in_(WEBSITE_ONBOARDING_CLAIMABLE))
                .where(
                    (WebsiteOnboarding.next_attempt_at.is_(None))
                    | (WebsiteOnboarding.next_attempt_at <= moment)
                )
                .order_by(
                    WebsiteOnboarding.next_attempt_at.nulls_first(),
                    WebsiteOnboarding.created_at,
                )
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    claimed: list[uuid.UUID] = []
    for job in rows:
        job.next_attempt_at = moment + timedelta(seconds=lease_s)
        claimed.append(job.id)
    await session.commit()
    return claimed


class WebsiteOnboardingProcessor:
    """Runs one website-setup job: read the site, then structure what it said."""

    def __init__(self, settings: Settings, providers: Providers) -> None:
        self.settings = settings
        self.providers = providers

    async def process(self, session: AsyncSession, job_id: uuid.UUID) -> JobOutcome:
        job = await session.get(WebsiteOnboarding, job_id)
        if job is None:  # pragma: no cover - claimed by id a moment ago
            raise LookupError(f"website onboarding job {job_id} is gone")
        if job.status not in WEBSITE_ONBOARDING_CLAIMABLE:
            # Confirmed or failed while we were queuing: nothing to do.
            return JobOutcome(job_id=job.id, status=job.status)

        job.attempt += 1
        job.status = WebsiteOnboardingStatus.CRAWLING
        job.last_error = None
        await session.commit()

        try:
            # Re-checked every attempt. The row is ours; the hostname in it is
            # not, and what it resolves to can change between attempts.
            site = guard.check(job.website_url)
            report = await crawl(site, CrawlLimits())

            job.crawl_json = _crawl_summary(report)
            job.status = WebsiteOnboardingStatus.GENERATING
            await session.commit()

            knowledge = await generate(report, llm=self.providers.llm, settings=self.settings)
        except AppError as exc:
            return await self._failed(session, job, exc)
        except Exception:  # a bug here must not strand the job on a spinner
            logger.exception(
                "website onboarding job raised unexpectedly", extra={"job_id": str(job.id)}
            )
            return await self._failed(session, job, None)

        job.knowledge_json = draft_from(knowledge)
        job.status = WebsiteOnboardingStatus.READY
        job.next_attempt_at = None
        job.finished_at = datetime.now(UTC)
        await session.commit()

        logger.info(
            "website onboarding ready for review",
            extra={
                "job_id": str(job.id),
                "pages": len(report.pages),
                "items": len(knowledge.items),
            },
        )
        return JobOutcome(
            job_id=job.id,
            status=job.status,
            pages=len(report.pages),
            items=len(knowledge.items),
        )

    async def _failed(
        self, session: AsyncSession, job: WebsiteOnboarding, error: AppError | None
    ) -> JobOutcome:
        """Record a failure, and decide whether a retry is worth it."""
        await session.rollback()
        fresh = await session.get(WebsiteOnboarding, job.id)
        if fresh is None:  # pragma: no cover
            raise LookupError(f"website onboarding job {job.id} is gone")

        retryable = error is None or should_retry(
            error, attempt=fresh.attempt, max_attempts=MAX_ATTEMPTS
        )
        # An unreadable site is terminal however many attempts are left: asking
        # a dead URL twice more just makes the customer wait longer.
        if isinstance(error, InvalidInputError):
            retryable = False

        fresh.last_error = _message_for(error)
        if retryable and fresh.attempt < MAX_ATTEMPTS:
            fresh.status = WebsiteOnboardingStatus.PENDING
            fresh.next_attempt_at = next_attempt_at(fresh.attempt, self.settings.backoff_schedule_s)
        else:
            fresh.status = WebsiteOnboardingStatus.FAILED
            fresh.next_attempt_at = None
            fresh.finished_at = datetime.now(UTC)
        await session.commit()

        logger.info(
            "website onboarding attempt failed",
            extra={
                "job_id": str(fresh.id),
                "attempt": fresh.attempt,
                "status": fresh.status.value,
                "code": getattr(error, "code", "unexpected"),
            },
        )
        return JobOutcome(job_id=fresh.id, status=fresh.status)


def _message_for(error: AppError | None) -> str:
    if isinstance(error, InvalidInputError):
        # The guard and the crawler both phrase their own refusals for a
        # customer already, and neither names an address.
        return str(error)
    code = getattr(error, "code", "")
    if code == "knowledge_invalid":
        return FAILURE_MESSAGES["generation"]
    return FAILURE_MESSAGES["unknown"]


def _crawl_summary(report: CrawlReport) -> dict[str, Any]:
    """What to show a customer about the crawl, and nothing more.

    Page URLs are included because they are the customer's own site; the
    reasons map is included because "we read one page" needs an explanation.
    """
    return {
        "site": report.site,
        "pages_read": len(report.pages),
        "page_urls": [page.url for page in report.pages][:20],
        "fetched": report.fetched,
        "skipped": report.skipped,
        "failed": report.failed,
        "duplicates": report.duplicate,
        "robots_blocked": report.robots_blocked,
        "truncated": report.truncated,
        "reasons": report.reasons,
        "text_chars": report.text_chars,
    }


def draft_from(knowledge: WebsiteKnowledge) -> dict[str, Any]:
    """Turn generated knowledge into the editable draft the UI works on.

    Every item arrives enabled with a stable id, because the review screen
    toggles and reorders them and needs to say *which* one changed. The built-in
    caller questions are prepended here rather than generated: they are the
    same for every business.
    """
    return {
        "business_name": knowledge.business_name,
        "business_summary": knowledge.business_summary,
        "operating_hours": knowledge.operating_hours,
        "services": knowledge.services,
        "items": [
            {
                "id": f"item-{index}",
                "category": item.category,
                "title": item.title,
                "content": item.content,
                "source_url": item.source_url,
                "needs_review": item.needs_review,
                "enabled": True,
            }
            for index, item in enumerate(knowledge.items)
        ],
        "questions": [
            *({**question, "enabled": True} for question in BUILTIN_QUESTIONS),
            *(
                {
                    "key": question.key,
                    "label": question.label,
                    "prompt": question.prompt,
                    "builtin": False,
                    "enabled": True,
                }
                for question in knowledge.questions
            ),
        ],
        "sms_templates": [
            {
                "key": template.key,
                "label": template.label,
                "body": template.body,
                # Off by default: there is no sending path yet, and an enabled
                # template would imply the receptionist can text a caller.
                "enabled": False,
            }
            for template in knowledge.sms_templates
        ],
    }
