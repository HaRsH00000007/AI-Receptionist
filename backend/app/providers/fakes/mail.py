"""An email provider that delivers to a list.

Keeping the sent messages lets a test assert on what a customer would actually
have received — the phone number in the activation mail, the summary in the
post-call mail — rather than only that "send" was called.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.providers.fakes.support import Behaviour
from app.providers.models import EmailMessage, EmailResult

logger = get_logger(__name__)


@dataclass
class FakeEmailProvider:
    name: str = "fake-email"
    behaviour: Behaviour = field(default_factory=Behaviour)
    outbox: list[EmailMessage] = field(default_factory=list)
    _counter: itertools.count[int] = field(default_factory=lambda: itertools.count(1))

    async def send(self, message: EmailMessage) -> EmailResult:
        # Subject and recipient only: the body can contain a call summary, and
        # a summary is customer content that does not belong in logs.
        self.behaviour.record("send", to=message.to, subject=message.subject)
        self.outbox.append(message)
        logger.info(
            "email captured by the fake provider",
            extra={"to": message.to, "subject": message.subject},
        )
        return EmailResult(message_id=f"fake-{next(self._counter):06d}", provider=self.name)

    def last_to(self, recipient: str) -> EmailMessage | None:
        for message in reversed(self.outbox):
            if message.to == recipient:
                return message
        return None
