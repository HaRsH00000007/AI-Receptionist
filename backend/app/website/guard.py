"""Deciding whether a URL a customer typed is safe for *us* to fetch.

This is the security boundary of the website-onboarding feature. Everything
else in :mod:`app.website` assumes a URL that has been through here.

The threat is server-side request forgery. Our crawler runs inside the VPC with
a security group, an instance role, and neighbours — Postgres, Redis, the
metadata service — that trust anything arriving from inside. A customer who can
make us fetch a URL of their choosing can ask us to fetch
``http://169.254.169.254/latest/meta-data/iam/security-credentials/`` and, if
the response is ever shown back to them, read our credentials out of the
"knowledge" we extracted from "their website".

So the rule here is not "block a list of bad hosts". It is **resolve the name
ourselves, check every address it resolves to, and connect to the address we
checked**:

* Only ``http`` and ``https``. No ``file:``, ``gopher:``, ``ftp:``, no
  ``javascript:``.
* Every resolved address must be globally routable. Loopback, private ranges,
  link-local (which is where the cloud metadata endpoint lives), unique-local
  IPv6, multicast, reserved and unspecified addresses are all refused.
* A hostname that resolves to a mix of public and private addresses is refused
  outright rather than filtered down to the public ones — that shape is a DNS
  rebinding attempt, not a misconfiguration.
* The check returns the vetted addresses, and the crawler pins its connection
  to them. Checking a name and then letting httpx resolve it again is a
  time-of-check/time-of-use bug: the second lookup can return ``127.0.0.1``.
* Redirects are not followed automatically anywhere in this feature. Each hop
  comes back here first, because a public URL redirecting to
  ``http://localhost:6379`` is the cheapest version of this attack.

Errors are deliberately vague about *why* a host was refused. "Private address"
versus "DNS failure" versus "connection refused" tells an attacker whether
``internal-admin.vpc.local`` exists, which is network mapping we do not have to
hand out.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from app.core.errors import InvalidInputError
from app.core.logging import get_logger

logger = get_logger(__name__)

ALLOWED_SCHEMES = frozenset({"http", "https"})

#: Ports we will connect to. A business website lives on 80 or 443; a URL
#: pointing at 6379 or 5432 is asking us to talk to infrastructure, and the
#: hostname in front of it is irrelevant.
ALLOWED_PORTS = frozenset({80, 443, 8080, 8443})

#: What a refused URL says to the customer. One sentence for every cause, so
#: the error cannot be used to probe our network.
REFUSED = (
    "That address can't be reached. Enter your public website address, like https://example.com."
)

MAX_URL_LENGTH = 2048
MAX_HOST_LENGTH = 253


@dataclass(frozen=True, slots=True)
class SafeUrl:
    """A URL that passed the checks, with the addresses it resolved to.

    ``addresses`` is what the crawler must actually connect to. Keeping them
    together is what makes the time-of-check/time-of-use gap closeable.
    """

    url: str
    scheme: str
    host: str
    port: int
    addresses: tuple[str, ...]

    @property
    def origin(self) -> str:
        return f"{self.scheme}://{self.netloc}"

    @property
    def netloc(self) -> str:
        default = 443 if self.scheme == "https" else 80
        return self.host if self.port == default else f"{self.host}:{self.port}"


def normalize_input(raw: str) -> str:
    """Turn what someone typed into a URL, without guessing at intent.

    ``example.com`` becomes ``https://example.com`` because that is what a
    person means by a website. A scheme that is present is never rewritten:
    silently upgrading ``http://`` to ``https://`` would make the crawl report
    a page the customer cannot actually serve.
    """
    value = raw.strip()
    if not value:
        raise InvalidInputError("Enter your website address.", details={"field": "website_url"})
    if len(value) > MAX_URL_LENGTH:
        raise InvalidInputError(REFUSED, details={"field": "website_url"})
    # A bare host, or a host:port, with no scheme. "//example.com" too.
    if "://" not in value:
        value = f"https://{value.lstrip('/')}"
    return value


def check(raw: str) -> SafeUrl:
    """Validate a URL and resolve it, or raise :class:`InvalidInputError`.

    Every rejection raises the same message. The specific reason is logged for
    us and never returned to the caller.
    """
    candidate = normalize_input(raw)
    parts = urlsplit(candidate)

    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        return _refuse("scheme", candidate)
    scheme = parts.scheme.lower()

    # Credentials in the URL are a redirect-laundering trick and have no place
    # in a business homepage.
    if parts.username or parts.password:
        return _refuse("credentials_in_url", candidate)

    try:
        host = (parts.hostname or "").strip().lower()
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError:
        # urlsplit raises on a malformed port such as "example.com:not-a-port".
        return _refuse("malformed_port", candidate)

    if not host or len(host) > MAX_HOST_LENGTH:
        return _refuse("no_host", candidate)
    if port not in ALLOWED_PORTS:
        return _refuse("port", candidate)
    if host.endswith("."):
        # "example.com." and "example.com" are the same name to DNS but
        # different strings to every same-origin check downstream.
        host = host.rstrip(".")

    # An IP typed directly still goes through the same address check — this is
    # the path that blocks http://169.254.169.254 and http://127.0.0.1 .
    literal = _as_ip(host)
    if literal is not None:
        if not _is_public(literal):
            return _refuse("non_public_literal", candidate)
        addresses: tuple[str, ...] = (str(literal),)
    else:
        if _is_internal_name(host):
            return _refuse("internal_name", candidate)
        addresses = _resolve(host, port, candidate)

    normalized = urlunsplit(
        (scheme, _netloc(host, port, scheme), parts.path or "/", parts.query, "")
    )
    return SafeUrl(url=normalized, scheme=scheme, host=host, port=port, addresses=addresses)


def check_redirect(location: str, *, came_from: SafeUrl) -> SafeUrl:
    """Validate a redirect target, resolving it relative to its source.

    A redirect gets the full check again — the same address rules, the same
    resolution — because "the first hop was public" says nothing about the
    second. Cross-origin redirects are allowed (``example.com`` to
    ``www.example.com`` is normal); the crawler decides separately whether a
    hop leaves the site it is permitted to read.
    """
    target = location.strip()
    if not target:
        return _refuse("empty_redirect", came_from.url)
    if "://" not in target:
        # Relative redirect. Resolve against the source origin rather than
        # guessing a scheme, so "/en/" stays on the same site.
        if target.startswith("//"):
            target = f"{came_from.scheme}:{target}"
        elif target.startswith("/"):
            target = f"{came_from.origin}{target}"
        else:
            base = came_from.url.rsplit("/", 1)[0]
            target = f"{base}/{target}"
    return check(target)


def same_site(url: SafeUrl, *, site: SafeUrl) -> bool:
    """Whether ``url`` is within the site we were given permission to read.

    Host-based, not origin-based, so ``http`` to ``https`` on the same host is
    still the same site. A subdomain is *not* the same site: ``blog.example.com``
    often belongs to a hosting provider, and crawling it is not what the
    customer agreed to. ``www`` is the one exception, because it is the same
    website in every practical sense.
    """
    return _canonical_host(url.host) == _canonical_host(site.host)


def _canonical_host(host: str) -> str:
    return host[4:] if host.startswith("www.") else host


def _netloc(host: str, port: int, scheme: str) -> str:
    default = 443 if scheme == "https" else 80
    return host if port == default else f"{host}:{port}"


def _as_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    # urlsplit leaves IPv6 hosts without their brackets.
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _is_public(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Whether an address is one we are willing to connect to.

    ``is_global`` does most of the work; the rest is named explicitly because
    relying on one property for a security decision makes it invisible when a
    library changes its mind about an edge case. 0.0.0.0 and ::  are rejected
    by ``is_unspecified``; 169.254.0.0/16 and fe80::/10 by ``is_link_local``,
    which is what keeps the cloud metadata endpoint out of reach.
    """
    if (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        return False
    # An IPv4 address wrapped in IPv6 (::ffff:127.0.0.1) is private twice over,
    # but only if we look through the wrapper.
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None and not _is_public(mapped):
        return False
    sixto4 = getattr(address, "sixtofour", None)
    if sixto4 is not None and not _is_public(sixto4):
        return False
    teredo = getattr(address, "teredo", None)
    if teredo is not None and any(not _is_public(part) for part in teredo):
        return False
    return bool(address.is_global)


#: Suffixes that never belong to a public website. ``.internal`` is GCP and AWS,
#: ``.local`` is mDNS, and a bare name with no dot is a VPC-internal hostname.
INTERNAL_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".intranet",
    ".lan",
    ".home",
    ".corp",
    ".private",
    ".test",
    ".example",
    ".invalid",
)


def _is_internal_name(host: str) -> bool:
    if host == "localhost" or "." not in host:
        return True
    return host.endswith(INTERNAL_SUFFIXES)


def _resolve(host: str, port: int, candidate: str) -> tuple[str, ...]:
    """Resolve a hostname and insist that *every* answer is public.

    All-or-nothing on purpose. A name answering with one public address and one
    ``10.x`` address is how DNS rebinding looks from here, and picking the
    public one would mean we connect to whichever address the OS happens to
    choose later.
    """
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError:
        return _refuse("dns_failure", candidate)

    addresses: list[str] = []
    for info in infos:
        raw = info[4][0]
        address = _as_ip(str(raw))
        if address is None or not _is_public(address):
            return _refuse("resolved_non_public", candidate)
        if str(address) not in addresses:
            addresses.append(str(address))

    if not addresses:
        return _refuse("dns_empty", candidate)
    return tuple(addresses)


def _refuse(reason: str, candidate: str) -> ValueError:
    """Log precisely, tell the customer nothing.

    Typed as returning so callers can `return _refuse(...)` and keep mypy
    happy about the branch; it always raises.
    """
    logger.info(
        "refused a website url",
        extra={"reason": reason, "url_host": urlsplit(candidate).hostname or "?"},
    )
    raise InvalidInputError(REFUSED, details={"field": "website_url"})
