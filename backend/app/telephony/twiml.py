"""TwiML construction.

Built with :mod:`xml.etree` rather than string formatting, and that choice is a
security control rather than a stylistic one. Every value that reaches this
module is attacker-influenced in some path — a business name comes from a signup
form, a caller id comes from the PSTN — and an f-string would let a ``<`` in any
of them restructure the document. Twilio then executes whatever the document
says: dial a number, post to a URL, read out text. XML injection here is remote
control of a phone call.

``ElementTree`` escapes text and attribute values on serialization, so the same
input becomes inert text instead.

There is a second, quieter rule: **this module never raises.** It sits on the
call path, and an exception here is a caller hearing dead air. Anything it
cannot represent is dropped or replaced, never allowed to propagate.
"""

from __future__ import annotations

from xml.etree.ElementTree import Element, SubElement, tostring

#: Twilio caps <Say> at a few thousand characters. Long before that it is a bad
#: experience, so the text is truncated at a length a caller will actually hear.
_MAX_SAY = 600


def _clean(text: str, *, limit: int = _MAX_SAY) -> str:
    """Collapse control characters and truncate.

    Escaping is ElementTree's job; this only removes what would be meaningless
    or hostile *after* escaping — newlines that a TTS engine reads as pauses,
    and lengths nobody will sit through.
    """
    flattened = " ".join(str(text).split())
    return flattened[:limit]


def _document(root: Element) -> str:
    return '<?xml version="1.0" encoding="UTF-8"?>' + tostring(root, encoding="unicode")


def connect_stream(
    *,
    websocket_url: str,
    parameters: dict[str, str] | None = None,
) -> str:
    """Hand the media stream to the voice agent.

    ``<Connect><Stream>`` is bidirectional — the agent both hears the caller and
    speaks back — which is what makes this the production path rather than
    ``<Start><Stream>``, whose stream is listen-only.

    Custom parameters ride along to the far end. They carry *identifiers*, never
    configuration and never anything secret: this document is handed to Twilio
    and the values appear in the websocket handshake, so a prompt or a token
    placed here would be sprayed across two vendors' logs. The far end looks
    the tenant up by id instead.
    """
    root = Element("Response")
    connect = SubElement(root, "Connect")
    stream = SubElement(connect, "Stream", {"url": websocket_url})

    for name, value in (parameters or {}).items():
        SubElement(stream, "Parameter", {"name": str(name), "value": _clean(value, limit=256)})

    return _document(root)


def say_and_record(
    *,
    message: str,
    recording_callback_url: str | None = None,
    max_length_s: int = 120,
    voice: str = "Polly.Joanna",
    transcribe: bool = False,
) -> str:
    """Speak a message, then take a voicemail.

    The vendor-outage path. When the voice agent cannot be reached, the caller
    is told so and offered a recording rather than silence or a dropped line —
    "we lost your call" is a far worse outcome for the business than "the AI was
    briefly unavailable", and the message still reaches them.
    """
    root = Element("Response")
    SubElement(root, "Say", {"voice": voice}).text = _clean(message)

    record_attributes = {
        "maxLength": str(max(1, max_length_s)),
        # Callers pause before speaking; ending on the first silence cuts people
        # off mid-thought.
        "timeout": "5",
        "playBeep": "true",
        "transcribe": "true" if transcribe else "false",
    }
    if recording_callback_url:
        record_attributes["recordingStatusCallback"] = recording_callback_url
    SubElement(root, "Record", record_attributes)

    return _document(root)


def say_and_hangup(*, message: str, voice: str = "Polly.Joanna") -> str:
    """Speak, then end the call.

    For a number we own but cannot serve — provisioning unfinished, tenant
    cancelled. Taking a voicemail nobody will ever read would be worse than
    saying so plainly.
    """
    root = Element("Response")
    SubElement(root, "Say", {"voice": voice}).text = _clean(message)
    SubElement(root, "Hangup")
    return _document(root)


def reject(*, reason: str = "rejected") -> str:
    """Refuse the call without answering it.

    Used only where answering would be wrong — a number that resolves to no
    tenant at all. Answering costs a billed minute and tells a scanner the
    number is live; rejecting does neither.
    """
    root = Element("Response")
    SubElement(root, "Reject", {"reason": reason})
    return _document(root)
