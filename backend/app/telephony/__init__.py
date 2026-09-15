"""The production call path: TwiML construction and inbound routing.

Split from the API layer so the decision a caller hears can be tested as a pure
function, without a request, a database or a vendor.
"""

from __future__ import annotations

from app.telephony import routing, twiml

__all__ = ["routing", "twiml"]
