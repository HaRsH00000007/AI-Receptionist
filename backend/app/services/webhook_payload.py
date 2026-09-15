"""What a stored webhook payload is allowed to contain.

``webhook_events`` exists so a delivery can be audited and replayed. It does not
exist to be a second copy of every customer's transcripts, and that distinction
matters more than it first appears:

* A transcript stored here is PII in a *second* place, with its own retention
  question, its own access path, and its own export surface. M12 gives calls a
  retention policy; a duplicate hiding in a webhook row would quietly outlive it.
* The admin panel renders these rows. Provider payloads are attacker-influenced
  whenever a signature check is skipped, and the less of one we keep, the less
  there is to mis-render later.
* A credential can arrive inside a payload. Twilio's number-import flow echoes
  account identifiers; a provider changing its payload shape can start
  including things we never asked for. Redacting by key name means a *new*
  sensitive field is caught by the pattern rather than by someone noticing.

So the row keeps the shape of the delivery — enough to tell what happened, to
correlate it, and to replay the decision — and drops the bodies. The full
content still reaches the application; it is simply not persisted twice.
"""

from __future__ import annotations

from typing import Any

#: Keys whose values are replaced wholesale. Matched case-insensitively on a
#: substring, so ``auth_token``, ``authToken`` and ``x-auth-token`` all match.
_SENSITIVE_KEY_PARTS: tuple[str, ...] = (
    "secret",
    "token",
    "password",
    "api_key",
    "apikey",
    "authorization",
    "auth_token",
    "signature",
    "credential",
    "private_key",
)

#: Keys holding conversational content: kept on ``calls``, not duplicated here.
_BULK_CONTENT_KEYS: tuple[str, ...] = (
    "transcript",
    "transcript_text",
    "messages",
    "turns",
    "conversation",
    "analysis",
    "full_transcript",
    "audio",
    "recording",
)

#: Longer strings are truncated. A payload field this size is either content or
#: base64, and neither belongs in an audit row.
_MAX_STRING = 512

#: Depth beyond which we stop descending. Provider payloads nest; an unbounded
#: walk over a hostile document is a denial-of-service waiting to happen.
_MAX_DEPTH = 6

REDACTED = "[redacted]"


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _is_bulk_content(key: str) -> bool:
    return key.lower() in _BULK_CONTENT_KEYS


def redact_payload(payload: Any, *, depth: int = 0) -> Any:
    """A storable copy of a provider payload.

    Structure is preserved so the row still answers "what did they send us?".
    Secrets become ``[redacted]``; bulk content becomes a summary of what was
    dropped, which is what makes the omission visible rather than mysterious.
    """
    if depth >= _MAX_DEPTH:
        return "[truncated: too deeply nested]"

    if isinstance(payload, dict):
        result: dict[str, Any] = {}
        for key, value in payload.items():
            name = str(key)
            if _is_sensitive(name):
                result[name] = REDACTED
            elif _is_bulk_content(name):
                result[name] = _summarize(value)
            else:
                result[name] = redact_payload(value, depth=depth + 1)
        return result

    if isinstance(payload, list):
        # Long arrays are almost always content. Keep a prefix for shape.
        head = [redact_payload(item, depth=depth + 1) for item in payload[:5]]
        if len(payload) > 5:
            head.append(f"[truncated: {len(payload) - 5} more items]")
        return head

    if isinstance(payload, str) and len(payload) > _MAX_STRING:
        return payload[:_MAX_STRING] + f"[truncated: {len(payload) - _MAX_STRING} more characters]"

    return payload


def _summarize(value: Any) -> str:
    """Record that content was present and how much, without keeping it."""
    if isinstance(value, list):
        return f"[omitted: {len(value)} entries of conversational content]"
    if isinstance(value, str):
        return f"[omitted: {len(value)} characters of conversational content]"
    if isinstance(value, dict):
        return f"[omitted: conversational content with {len(value)} fields]"
    return "[omitted: conversational content]"
