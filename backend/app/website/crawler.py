"""Reading a bounded, relevant slice of a business website.

Not a web crawler in the search-engine sense. The goal is the dozen pages that
tell a receptionist what the business does — About, Services, Pricing, Contact,
FAQ — and then to stop. Every limit here exists because the input is a URL a
stranger typed:

* **Pinned connections.** The addresses :mod:`app.website.guard` resolved and
  vetted are the addresses we connect to, via :class:`PinnedTransport`.
  Re-resolving the hostname at connect time would reopen the DNS-rebinding hole
  the guard just closed.
* **No automatic redirects.** Each hop is re-validated by the guard, so a public
  URL cannot bounce us to ``http://localhost:6379``.
* **Budgets, not best effort.** Page count, depth, per-page bytes, total bytes,
  wall-clock time and concurrency are all capped. A site that serves a 2 GB
  "page", or 40,000 paginated URLs, costs us a fixed amount and then stops.
* **robots.txt is honoured**, including when it is unreachable — a site that
  cannot tell us its rules gets the benefit of the doubt only for its homepage.
* **Content is data.** Nothing fetched here is ever executed, interpolated into
  a prompt as an instruction, or trusted; :mod:`app.website.knowledge` wraps it
  explicitly as untrusted material.

The output is text plus provenance: which URL each piece of text came from, so
the review screen can show a customer where a "fact" was found.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from app.core.errors import InvalidInputError
from app.core.logging import get_logger
from app.website.guard import SafeUrl, check_redirect, same_site

logger = get_logger(__name__)

USER_AGENT = "AIReceptionistBot/1.0 (+business onboarding; respects robots.txt)"

#: Paths worth more than the rest of a site. A receptionist needs what the
#: business does, what it charges, when it opens and how to reach it — not the
#: blog archive. Ordered: earlier patterns are crawled first.
PRIORITY_PATTERNS: tuple[str, ...] = (
    r"/(about|about-us|who-we-are|our-story)",
    r"/(services|what-we-do|treatments|practice-areas|solutions)",
    r"/(pricing|prices|rates|plans|fees|menu)",
    r"/(contact|contact-us|locations?|visit|directions)",
    r"/(faq|faqs|questions|help)",
    r"/(hours|opening-hours|schedule|appointments?|book|booking)",
    r"/(products|shop|catalog)",
    r"/(policy|policies|terms|cancellation|privacy)",
    r"/(team|staff|doctors|attorneys|stylists)",
)

#: Never worth fetching: binary, transactional, or endless.
SKIP_PATTERNS = re.compile(
    r"(\.(pdf|docx?|xlsx?|pptx?|zip|gz|rar|dmg|exe|mp[34]|mov|avi|png|jpe?g|gif|svg|webp|ico|css|js|json|xml|rss)$"
    r"|/(cart|checkout|login|signin|sign-in|register|signup|account|admin|wp-admin|wp-login|search)"
    r"|/(tag|tags|category|categories|author|page)/"
    r"|\?(s|q|search|add-to-cart)=)",
    re.IGNORECASE,
)

ALLOWED_CONTENT = ("text/html", "application/xhtml+xml", "text/plain")

#: Tags that carry no readable content, or carry code. Removed before text
#: extraction so a page's JavaScript never reaches the LLM.
DROP_TAGS = (
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "iframe",
    "canvas",
    "form",
    "button",
    "select",
)


@dataclass(frozen=True, slots=True)
class CrawlLimits:
    """Everything that bounds a crawl.

    Defaults are sized for a small-business site and for an onboarding screen
    somebody is watching: a few seconds of work, not a background job that runs
    for an hour.
    """

    max_pages: int = 12
    max_depth: int = 2
    #: Per response. 2 MB of HTML is already an outlier for a business page.
    max_page_bytes: int = 2 * 1024 * 1024
    max_total_bytes: int = 12 * 1024 * 1024
    #: Wall clock for the whole crawl, including robots.txt.
    max_seconds: float = 45.0
    #: Per request, so one hanging server cannot consume the whole budget.
    request_timeout: float = 8.0
    concurrency: int = 4
    max_redirects: int = 3
    #: Characters of extracted text kept per page. Enough for a long services
    #: page; short enough that twelve pages stay inside an LLM context window.
    max_text_chars: int = 12_000


@dataclass(frozen=True, slots=True)
class Page:
    """One page we read, with where it came from."""

    url: str
    title: str
    description: str
    text: str
    headings: tuple[str, ...]
    emails: tuple[str, ...]
    phones: tuple[str, ...]
    #: Absolute, already-filtered candidates for the next depth. Collected
    #: during extraction because that is the only place the HTML still exists.
    links: tuple[str, ...]


@dataclass(slots=True)
class CrawlReport:
    """What happened, in enough detail to show a customer and to debug.

    ``skipped`` and ``failed`` are counts plus reasons rather than silence: a
    crawl that read one page out of twelve should say so on the review screen,
    because the knowledge that follows will be thin and the customer needs to
    know why.
    """

    site: str
    pages: list[Page] = field(default_factory=list)
    fetched: int = 0
    skipped: int = 0
    failed: int = 0
    duplicate: int = 0
    bytes_read: int = 0
    robots_blocked: int = 0
    reasons: dict[str, int] = field(default_factory=dict)
    truncated: bool = False

    def note(self, reason: str) -> None:
        self.reasons[reason] = self.reasons.get(reason, 0) + 1

    @property
    def text_chars(self) -> int:
        return sum(len(page.text) for page in self.pages)


class PinnedTransport(httpx.AsyncHTTPTransport):
    """Connects to a vetted address while speaking to the right hostname.

    The URL's host is swapped for an address the guard already approved. TLS
    still needs the *name* for SNI and certificate verification, so that is
    passed through the ``sni_hostname`` extension and the ``Host`` header is
    preserved. Without this, we validate one address and then let the OS
    resolver pick another — which is the entire DNS-rebinding attack.
    """

    def __init__(self, address: str, hostname: str, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._address = address
        self._hostname = hostname

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        original = request.url
        request.url = original.copy_with(host=self._address)
        request.headers.setdefault("Host", self._hostname)
        if original.scheme == "https":
            request.extensions = {**request.extensions, "sni_hostname": self._hostname}
        return await super().handle_async_request(request)


async def crawl(
    site: SafeUrl,
    limits: CrawlLimits | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> CrawlReport:
    """Read up to ``limits.max_pages`` pages of ``site``.

    ``site`` must have come from :func:`app.website.guard.check`. Raises only
    :class:`InvalidInputError`, and only when the homepage itself cannot be
    read — everything else is recorded in the report, because a site with three
    broken links is still a site we can learn from.
    """
    budget = limits or CrawlLimits()
    report = CrawlReport(site=site.url)
    deadline = time.monotonic() + budget.max_seconds

    async with _client(site, budget, transport) as client:
        robots = await _robots(client, site, budget, report)

        homepage = await _fetch(client, site, budget, report, robots=robots, transport=transport)
        if homepage is None:
            # Nothing to review and nothing to generate from: this one is worth
            # telling the customer about directly.
            raise InvalidInputError(
                "We couldn't read that website. Check the address opens in a browser, "
                "then try again — or set your receptionist up manually.",
                details={"field": "website_url"},
            )
        report.pages.append(homepage)
        seen_text = {_fingerprint(homepage.text)}
        seen_urls = {_key(site.url)}

        queue = _rank(_links(homepage, site), depth=1)
        semaphore = asyncio.Semaphore(budget.concurrency)

        while queue and len(report.pages) < budget.max_pages:
            if time.monotonic() > deadline:
                report.truncated = True
                report.note("time_budget")
                break

            # One generation at a time, so priority ordering is respected and
            # concurrency stays bounded.
            batch, queue = queue[: budget.concurrency], queue[budget.concurrency :]
            batch = [(url, depth) for url, depth in batch if _key(url) not in seen_urls]
            for url, _ in batch:
                seen_urls.add(_key(url))

            async def one(target: str, depth: int) -> tuple[Page | None, int]:
                async with semaphore:
                    safe = _revalidate(target, site, report)
                    if safe is None:
                        return None, depth
                    page = await _fetch(
                        client, safe, budget, report, robots=robots, transport=transport
                    )
                    return page, depth

            results = await asyncio.gather(
                *(one(url, depth) for url, depth in batch), return_exceptions=True
            )
            for result in results:
                if isinstance(result, BaseException):
                    report.failed += 1
                    report.note("unexpected_error")
                    continue
                page, depth = result
                if page is None:
                    continue
                mark = _fingerprint(page.text)
                if mark in seen_text:
                    # Template pages and ?utm= duplicates: same words, new URL.
                    report.duplicate += 1
                    continue
                seen_text.add(mark)
                report.pages.append(page)
                if len(report.pages) >= budget.max_pages:
                    report.truncated = True
                    break
                if depth < budget.max_depth:
                    queue.extend(_rank(_links(page, site), depth=depth + 1))

            if report.bytes_read >= budget.max_total_bytes:
                report.truncated = True
                report.note("byte_budget")
                break

    logger.info(
        "crawled a customer website",
        extra={
            "site": site.host,
            "pages": len(report.pages),
            "skipped": report.skipped,
            "failed": report.failed,
            "duplicates": report.duplicate,
            "robots_blocked": report.robots_blocked,
            "truncated": report.truncated,
        },
    )
    return report


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


def _client(
    site: SafeUrl, budget: CrawlLimits, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    # ``transport`` exists so tests can serve a fake website from
    # ``httpx.MockTransport``, the same seam ``ProviderHTTPClient`` uses. It is
    # never set in production, where the pinned transport is the whole point.
    return httpx.AsyncClient(
        transport=transport or PinnedTransport(site.addresses[0], site.host, retries=0),
        timeout=httpx.Timeout(budget.request_timeout, connect=min(4.0, budget.request_timeout)),
        follow_redirects=False,  # every hop is re-validated by hand
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en",
        },
        limits=httpx.Limits(max_connections=budget.concurrency),
    )


async def _robots(
    client: httpx.AsyncClient, site: SafeUrl, budget: CrawlLimits, report: CrawlReport
) -> RobotFileParser | None:
    """Fetch and parse robots.txt. ``None`` means "no rules found".

    A missing or unreadable robots.txt is treated as permissive, which is the
    convention. A robots.txt that *is* readable is obeyed, including for the
    homepage — if a business tells crawlers to stay out, we are a crawler.
    """
    url = f"{site.origin}/robots.txt"
    try:
        response = await client.get(url)
    except httpx.HTTPError:
        report.note("robots_unreachable")
        return None
    if response.status_code != 200:
        return None
    parser = RobotFileParser()
    try:
        parser.parse(response.text[: 512 * 1024].splitlines())
    except (ValueError, UnicodeError):  # pragma: no cover - robotparser is forgiving
        report.note("robots_unparseable")
        return None
    return parser


def _allowed(robots: RobotFileParser | None, url: str) -> bool:
    if robots is None:
        return True
    try:
        return bool(robots.can_fetch(USER_AGENT, url))
    except (ValueError, UnicodeError):  # pragma: no cover
        return True


async def _fetch(
    client: httpx.AsyncClient,
    target: SafeUrl,
    budget: CrawlLimits,
    report: CrawlReport,
    *,
    robots: RobotFileParser | None,
    hop: int = 0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Page | None:
    if not _allowed(robots, target.url):
        report.robots_blocked += 1
        report.skipped += 1
        return None
    if report.bytes_read >= budget.max_total_bytes:
        report.skipped += 1
        report.note("byte_budget")
        return None

    try:
        async with client.stream("GET", target.url) as response:
            if response.is_redirect:
                if hop >= budget.max_redirects:
                    report.skipped += 1
                    report.note("too_many_redirects")
                    return None
                location = response.headers.get("location", "")
                await response.aclose()
                return await _follow(
                    client, location, target, budget, report, robots, hop, transport
                )

            if response.status_code != 200:
                report.failed += 1
                report.note(f"http_{response.status_code // 100}xx")
                return None

            media = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
            if media and media not in ALLOWED_CONTENT:
                report.skipped += 1
                report.note("content_type")
                return None

            declared = response.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > budget.max_page_bytes:
                report.skipped += 1
                report.note("too_large")
                return None

            # Streamed with a running total: a server that lies in
            # Content-Length, or omits it, still cannot hand us an endless body.
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes(64 * 1024):
                size += len(chunk)
                if size > budget.max_page_bytes:
                    report.skipped += 1
                    report.note("too_large")
                    return None
                chunks.append(chunk)
            body = b"".join(chunks)
            report.bytes_read += size
            encoding = response.encoding or "utf-8"
    except httpx.HTTPError:
        report.failed += 1
        report.note("network_error")
        return None

    report.fetched += 1
    html = body.decode(encoding, errors="replace")
    page = extract(html, url=target.url, max_chars=budget.max_text_chars)
    if not page.text.strip():
        # A JavaScript-rendered page: fetched fine, said nothing. Counted so the
        # review screen can explain the thin result instead of blaming the user.
        report.note("no_readable_text")
        return None
    return page


async def _follow(
    client: httpx.AsyncClient,
    location: str,
    came_from: SafeUrl,
    budget: CrawlLimits,
    report: CrawlReport,
    robots: RobotFileParser | None,
    hop: int,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Page | None:
    try:
        hopped = check_redirect(location, came_from=came_from)
    except InvalidInputError:
        # A redirect into private space, or to a non-HTTP scheme.
        report.skipped += 1
        report.note("unsafe_redirect")
        return None
    if hopped.addresses[0] != came_from.addresses[0] or hopped.host != came_from.host:
        # A different host needs its own pinned connection; one client is bound
        # to one address. Common and harmless: example.com -> www.example.com.
        async with _client(hopped, budget, transport) as other:
            return await _fetch(
                other, hopped, budget, report, robots=robots, hop=hop + 1, transport=transport
            )
    return await _fetch(
        client, hopped, budget, report, robots=robots, hop=hop + 1, transport=transport
    )


def _revalidate(url: str, site: SafeUrl, report: CrawlReport) -> SafeUrl | None:
    from app.website.guard import check  # local: avoids a cycle at import time

    try:
        safe = check(url)
    except InvalidInputError:
        report.skipped += 1
        report.note("unsafe_link")
        return None
    if not same_site(safe, site=site):
        report.skipped += 1
        report.note("off_site")
        return None
    return safe


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")
WHITESPACE_RE = re.compile("[ \t\u00a0]+")
BLANKS_RE = re.compile(r"\n{3,}")


def extract(html: str, *, url: str, max_chars: int = 12_000) -> Page:
    """Turn HTML into readable text, a title, headings and contact details.

    Scripts and styles are dropped before anything else, so page code cannot
    reach the LLM. Headings are kept separately because they are the cheapest
    signal of what a page is *about* — "Our Prices" above a table of numbers is
    what makes those numbers mean something.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(list(DROP_TAGS)):
        tag.decompose()
    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
    description = ""
    meta = soup.find("meta", attrs={"name": "description"}) or soup.find(
        "meta", attrs={"property": "og:description"}
    )
    if meta is not None:
        description = str(meta.get("content") or "").strip()

    headings = tuple(
        text
        for text in (
            WHITESPACE_RE.sub(" ", tag.get_text(" ", strip=True))
            for tag in soup.find_all(["h1", "h2", "h3"])
        )
        if text
    )[:40]

    # Links are read before the body text, because mailto:/tel: carry the
    # contact details a receptionist needs and they vanish in plain text.
    emails: list[str] = []
    phones: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        if href.lower().startswith("mailto:"):
            found = EMAIL_RE.search(href)
            if found and found.group(0) not in emails:
                emails.append(found.group(0))
        elif href.lower().startswith("tel:"):
            number = href.split(":", 1)[1].strip()
            if number and number not in phones:
                phones.append(number)

    links: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
            continue
        absolute = urljoin(url, href)
        if urlsplit(absolute).scheme not in ("http", "https"):
            continue
        if SKIP_PATTERNS.search(absolute):
            continue
        if absolute not in links:
            links.append(absolute)
        if len(links) >= 200:
            # A nav menu is tens of links; 200 means a paginated archive, and
            # ranking the rest would cost more than the pages are worth.
            break

    text = soup.get_text("\n", strip=True)
    text = WHITESPACE_RE.sub(" ", text)
    text = BLANKS_RE.sub("\n\n", text).strip()

    for match in EMAIL_RE.findall(text):
        if match not in emails:
            emails.append(match)
    for match in PHONE_RE.findall(text):
        digits = re.sub(r"\D", "", match)
        if 9 <= len(digits) <= 15 and match.strip() not in phones:
            phones.append(match.strip())

    return Page(
        url=url,
        title=title[:300],
        description=description[:600],
        text=text[:max_chars],
        headings=headings,
        emails=tuple(emails[:10]),
        phones=tuple(phones[:10]),
        links=tuple(links),
    )


# ---------------------------------------------------------------------------
# Link discovery
# ---------------------------------------------------------------------------


def _links(page: Page, site: SafeUrl) -> list[str]:
    """The page's links that are worth following: same site, not junk.

    Host-matched here as a cheap filter; the guard re-validates every one of
    them before a request is made, because a hostname that merely *looks* like
    the site is still a hostname a stranger supplied.
    """
    keep: list[str] = []
    for candidate in page.links:
        host = (urlsplit(candidate).hostname or "").lower()
        if _canonical(host) != _canonical(site.host):
            continue
        keep.append(candidate)
    return keep


def _canonical(host: str) -> str:
    return host[4:] if host.startswith("www.") else host


def _rank(urls: list[str], *, depth: int) -> list[tuple[str, int]]:
    """Priority first, then everything else, deduplicated.

    Ordering is the whole point: with twelve pages of budget, reading
    ``/services`` and ``/contact`` matters far more than reading two blog posts.
    """
    scored: list[tuple[int, str]] = []
    for url in urls:
        path = urlsplit(url).path.lower() or "/"
        score = len(PRIORITY_PATTERNS) + 1
        for index, pattern in enumerate(PRIORITY_PATTERNS):
            if re.search(pattern, path):
                score = index
                break
        scored.append((score, url))
    scored.sort(key=lambda pair: pair[0])
    seen: set[str] = set()
    ordered: list[tuple[str, int]] = []
    for _, url in scored:
        key = _key(url)
        if key in seen:
            continue
        seen.add(key)
        ordered.append((url, depth))
    return ordered


def _key(url: str) -> str:
    """A URL's identity for deduplication.

    Fragments and tracking parameters make the same page look new; a trailing
    slash does too. Query strings that matter (``?page=2``) are kept.
    """
    parts = urlsplit(url)
    query = "&".join(
        piece
        for piece in sorted(parts.query.split("&"))
        if piece and not piece.lower().startswith(("utm_", "fbclid", "gclid", "ref="))
    )
    path = parts.path.rstrip("/") or "/"
    host = parts.hostname or ""
    if host.startswith("www."):
        host = host[4:]
    return urlunsplit((parts.scheme, host, path, query, ""))


def _fingerprint(text: str) -> str:
    """Content identity, so two URLs serving one page are read once."""
    collapsed = re.sub(r"\s+", " ", text).strip().lower()
    return hashlib.sha256(collapsed.encode("utf-8")).hexdigest()
