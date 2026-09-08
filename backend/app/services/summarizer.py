"""Post-call summarization.

The second and last place an LLM appears. High volume and easy, so it runs on
the small/fast model — a different tier from config generation, which is
once-per-tenant and quality-critical (docs/00_DECISIONS.md section 3).

Unlike config generation there is no template fallback: a summary invented
deterministically from a transcript would be worse than none, and the call
record itself is already safe in the database. A failure here is retried and,
if it keeps failing, recorded — never papered over.
"""

from __future__ import annotations

from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import TerminalError
from app.core.logging import get_logger
from app.prompts.loader import CALL_SUMMARY_PROMPT, load_prompt, split_sections
from app.providers.protocols import LLMProvider
from app.schemas.agent_config import CallSummary
from app.services.config_generator import extract_json
from app.services.normalization import normalize_phone

logger = get_logger(__name__)

#: Bounded so one very long call cannot blow the context window or the bill.
MAX_TRANSCRIPT_CHARS = 20_000


class Summarizer:
    def __init__(self, llm: LLMProvider, settings: Settings) -> None:
        self.llm = llm
        self.settings = settings

    async def summarize(
        self,
        *,
        transcript: str,
        business_name: str,
        business_type: str,
        called_number: str | None,
        caller_number: str | None,
        duration_s: int | None,
    ) -> CallSummary:
        if not transcript.strip():
            # Terminal: an empty transcript will still be empty on retry.
            raise TerminalError("cannot summarize an empty transcript", code="empty_transcript")

        system, user_template = split_sections(load_prompt(CALL_SUMMARY_PROMPT))
        user = user_template.format(
            business_name=business_name,
            business_type=business_type,
            called_number=called_number or "unknown",
            caller_number=caller_number or "unknown",
            duration_s=duration_s if duration_s is not None else "unknown",
            transcript=transcript[:MAX_TRANSCRIPT_CHARS],
        )

        reply = await self.llm.complete(
            system=system,
            user=user,
            model=self.settings.llm_summary_model,
            max_tokens=1024,
            temperature=self.settings.llm_temperature,
        )

        try:
            summary = CallSummary.model_validate(extract_json(reply.text))
        except (ValueError, ValidationError) as exc:
            # Retryable: a differently-sampled reply may well validate, and the
            # attempt budget stops it looping.
            raise _RetryableSummaryError(str(exc)[:500]) from exc

        return _verify_callback(summary, caller_number)


class _RetryableSummaryError(TerminalError):
    """A malformed reply. Retryable despite the base class default."""

    code = "summary_schema_rejected"
    retryable = True


def _verify_callback(summary: CallSummary, caller_number: str | None) -> CallSummary:
    """Normalize the claimed callback number, and note when it matches caller ID.

    The number is whatever the caller said out loud, so it is never trusted
    enough to dial automatically. Normalizing it makes the comparison against
    Twilio's caller id meaningful; an unparseable claim is dropped rather than
    shown as if it were a phone number.
    """
    claimed = summary.callback_number
    if not claimed:
        return summary

    try:
        normalized = normalize_phone(claimed)
    except Exception:  # noqa: BLE001 - a bad claim is data, not an error
        logger.info("dropping an unparseable callback number claimed on a call")
        return summary.model_copy(update={"callback_number": None})

    if caller_number and normalized == caller_number:
        logger.debug("claimed callback number matches the caller id")
    return summary.model_copy(update={"callback_number": normalized})
