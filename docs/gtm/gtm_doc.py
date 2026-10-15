"""Builds the 8-page go-to-market document as a PDF.

Every number that is measured (AWS task sizes, test counts, what the product
does) comes from the repository. Every number that is a market price is marked
as an assumption, because vendor pricing was not verifiable from this machine —
the AWS session had expired and no billing API was reachable.
"""

from __future__ import annotations

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

OUT = "Evenline_GTM_and_Cost_Management.pdf"

INK = colors.HexColor("#14181F")
MUTED = colors.HexColor("#5B6472")
ACCENT = colors.HexColor("#2743E3")
SOFT = colors.HexColor("#EEF1FE")
LINE = colors.HexColor("#D8DDE6")
BAND = colors.HexColor("#F6F8FB")
WARN = colors.HexColor("#B45309")

styles = getSampleStyleSheet()


def style(name: str, **kwargs: object) -> ParagraphStyle:
    kwargs.setdefault("parent", styles["Normal"])
    return ParagraphStyle(name, **kwargs)  # type: ignore[arg-type]


BODY = style(
    "body", fontName="Helvetica", fontSize=9.6, leading=14.0, textColor=INK, spaceAfter=6
)
LEAD = style("lead", parent=BODY, fontSize=11, leading=16, textColor=MUTED, spaceAfter=10)
H1 = style(
    "h1",
    fontName="Helvetica-Bold",
    fontSize=19,
    leading=23,
    textColor=INK,
    spaceAfter=3,
    spaceBefore=0,
)
H2 = style(
    "h2",
    fontName="Helvetica-Bold",
    fontSize=12.5,
    leading=15,
    textColor=INK,
    spaceBefore=10,
    spaceAfter=4,
)
H3 = style(
    "h3", fontName="Helvetica-Bold", fontSize=10, leading=13, textColor=ACCENT, spaceBefore=9,
    spaceAfter=3,
)
EYEBROW = style(
    "eyebrow", fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=ACCENT, spaceAfter=4
)
BULLET = style("bullet", parent=BODY, leftIndent=11, bulletIndent=2, spaceAfter=3)
SMALL = style("small", parent=BODY, fontSize=8.3, leading=11.6, textColor=MUTED)
CELL = style("cell", fontName="Helvetica", fontSize=8.5, leading=11.5, textColor=INK)
CELL_B = style("cellb", parent=CELL, fontName="Helvetica-Bold")
CELL_H = style("cellh", parent=CELL, fontName="Helvetica-Bold", textColor=colors.white)
NOTE = style("note", parent=BODY, fontSize=8.6, leading=12.2, textColor=WARN)


def para(text: str, s: ParagraphStyle = BODY) -> Paragraph:
    return Paragraph(text, s)


def bullets(items: list[str], s: ParagraphStyle = BULLET) -> list[Paragraph]:
    return [Paragraph(f"•&nbsp;&nbsp;{item}", s) for item in items]


def table(rows: list[list[str]], widths: list[float], align: str = "LEFT") -> Table:
    data = [[Paragraph(cell, CELL_H) for cell in rows[0]]]
    for row in rows[1:]:
        data.append([Paragraph(cell, CELL_B if index == 0 else CELL) for index, cell in enumerate(row)])
    t = Table(data, colWidths=widths, hAlign=align, repeatRows=1)
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), INK),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BAND]),
                ("GRID", (0, 0), (-1, -1), 0.4, LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
            ]
        )
    )
    return t


def callout(title: str, text: str) -> Table:
    inner = [
        [Paragraph(f"<b>{title}</b>", style("ct", parent=CELL, fontSize=9, textColor=ACCENT))],
        [Paragraph(text, style("cb", parent=CELL, fontSize=8.8, leading=12.4))],
    ]
    t = Table(inner, colWidths=[165 * mm])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), SOFT),
                ("LINEBEFORE", (0, 0), (0, -1), 2.2, ACCENT),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, 0), 7),
                ("BOTTOMPADDING", (0, -1), (-1, -1), 7),
            ]
        )
    )
    return t


def decorate(canvas, doc) -> None:  # type: ignore[no-untyped-def]
    canvas.saveState()
    page = canvas.getPageNumber()
    if page > 1:
        canvas.setFont("Helvetica", 7.6)
        canvas.setFillColor(MUTED)
        canvas.drawString(20 * mm, 12 * mm, "Evenline — AI Receptionist · Go-to-market and cost management")
        canvas.drawRightString(190 * mm, 12 * mm, f"Page {page} of 8")
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.4)
        canvas.line(20 * mm, 15 * mm, 190 * mm, 15 * mm)
    canvas.restoreState()


story: list[object] = []
A = story.append


# ---------------------------------------------------------------------------
# Page 1 — cover and executive summary
# ---------------------------------------------------------------------------
A(Spacer(1, 10 * mm))
A(para("GO-TO-MARKET DOCUMENT", EYEBROW))
A(para("Evenline: the AI receptionist that<br/>learns your business from your website", style(
    "cover", fontName="Helvetica-Bold", fontSize=25, leading=30, textColor=INK, spaceAfter=8)))
A(para(
    "A phone receptionist for small businesses that answers every call, takes the details that "
    "matter, and texts the owner a summary minutes later — set up in about a minute from a "
    "website address.", LEAD))
A(Spacer(1, 3 * mm))
A(table(
    [
        ["", ""],
        ["Document", "Go-to-market plan and cost management"],
        ["Product status", "Working build; two onboarding paths; 1,043 backend and 177 frontend tests passing"],
        ["Deployment", "AWS us-east-2 · ECS Fargate, ALB, CloudFront, RDS, ElastiCache"],
        ["Prepared", "14 October 2026"],
    ][1:],
    [38 * mm, 127 * mm],
))
A(Spacer(1, 7 * mm))

A(para("Executive summary", H2))
A(para(
    "Small businesses lose revenue to unanswered calls, and the staff who would answer them are "
    "the same staff serving customers in front of them. Evenline answers every call with a voice "
    "agent that already knows the business, captures who called and why, and reports back by email "
    "and dashboard. It is live on AWS today.", BODY))
A(para(
    "The wedge is onboarding. Competing products ask an owner to fill in a long form before they "
    "hear anything work. Evenline reads their existing website, shows them what it learned, and "
    "lets them correct it — the first interaction is a review, not a questionnaire.", BODY))
A(Spacer(1, 2 * mm))
A(callout(
    "The two numbers this document exists to answer",
    "<b>Deployment cost</b> — roughly <b>$3.00 to $4.60 per day</b> ($90–140 per month) to "
    "run the whole platform on AWS at current scale, dominated by three fixed line items. "
    "<b>Tooling and API cost</b> — roughly <b>$1.20 to $3.60 per day</b> during the build, "
    "almost all of it vendor usage rather than software licences. Both are broken down in "
    "sections 6 and 7, with the assumptions stated.",
))
A(Spacer(1, 5 * mm))
A(para(
    "<b>What is ready:</b> both setup paths, provisioning, live call answering, transcripts and "
    "summaries, the customer dashboard, deletion with number release, and calendar integrations. "
    "<b>What is not:</b> published prices, outbound SMS sending, and languages other than English. "
    "Each is called out where it affects the plan rather than left implied.", BODY))

A(PageBreak())

# ---------------------------------------------------------------------------
# Page 2 — the product
# ---------------------------------------------------------------------------
A(para("1. The product", H1))
A(para("What a customer gets, and what happens on a call.", LEAD))

A(para("Two ways to set up", H2))
A(para(
    "Both paths end at the same provisioning pipeline, so there is one way for a receptionist to "
    "be created and one place where money is spent.", BODY))
A(table(
    [
        ["Path", "What the owner does", "Time"],
        ["Use my website", "Enters a website address. The platform reads the public pages, drafts what the receptionist should know, and shows it for review and editing before anything is created.", "~1 minute"],
        ["Set up manually", "Fills in business details, services, hours, greeting and escalation rules across four steps.", "~5 minutes"],
    ],
    [30 * mm, 112 * mm, 23 * mm],
))

A(para("On a call", H2))
A(para(
    "A caller dials the business number. The voice agent answers with the greeting the owner chose, "
    "answers questions from the knowledge the owner approved, collects the details the owner asked "
    "for, and escalates according to the owner's rules. After the call, a transcript and a short "
    "summary with an urgency rating arrive by email and appear on the dashboard.", BODY))

A(para("In the dashboard", H2))
for line in bullets([
    "<b>Home</b> — the number, usage against plan, and the latest calls.",
    "<b>Calls</b> — every call with transcript, summary, intent and callback number.",
    "<b>Agent</b> — what the receptionist knows, edited and republished as a new version; every past call stays pinned to the version that answered it.",
    "<b>Contacts</b> — callers, grouped, with their history.",
    "<b>Integrations</b> — Google Calendar and Microsoft Outlook via real OAuth, filtered by business type.",
    "<b>Settings</b> — business details, notifications, phone, billing, and deleting the receptionist.",
]):
    A(line)

A(para("Verticals supported today", H2))
A(table(
    [
        ["Vertical", "Why it fits"],
        ["Salon &amp; spa", "High call volume during appointments; staff hands are literally full."],
        ["Law firm", "Intake questions are structured and repetitive; missed calls are expensive."],
        ["Medical practice", "Front desk saturation; integrations deliberately exclude general automation tools for privacy reasons."],
        ["Real estate", "Agents are mobile and miss inbound enquiries."],
        ["Something else", "Generic configuration, same pipeline."],
    ],
    [35 * mm, 130 * mm],
))
A(Spacer(1, 3 * mm))
A(para(
    "<b>Honesty note carried into the product:</b> the dashboard shows “Not tracked yet” "
    "for hang-ups, spam and transfers rather than displaying a zero, because those are not measured "
    "yet. That principle should carry into sales collateral too.", NOTE))

A(PageBreak())

# ---------------------------------------------------------------------------
# Page 3 — market and ICP
# ---------------------------------------------------------------------------
A(para("2. Market and ideal customer", H1))
A(para("Who feels this pain enough to pay monthly for it.", LEAD))

A(para("Ideal customer profile", H2))
A(table(
    [
        ["Attribute", "Target"],
        ["Business size", "1–20 staff, single or few locations"],
        ["Call volume", "20–400 inbound calls a month"],
        ["Current solution", "Voicemail, a shared mobile, or an answering service at $200–600/month"],
        ["Buying trigger", "Missed calls during busy periods; a receptionist leaving; opening a second location"],
        ["Decision maker", "Owner or practice manager — one person, no procurement"],
        ["Has a website", "Yes — this is the qualifying signal for the fast onboarding path"],
    ],
    [38 * mm, 127 * mm],
))

A(para("Why they buy", H2))
for line in bullets([
    "<b>A missed call is a lost job.</b> For a salon or a law firm, one booking covers a month of subscription.",
    "<b>Answering services cost more and know less.</b> A human service reads a script; this reads the business's own website.",
    "<b>Voicemail is worse than nothing.</b> Most callers hang up and call the next listing.",
]):
    A(line)

A(para("Competitive position", H2))
A(table(
    [
        ["Alternative", "Their edge", "Our counter"],
        ["Human answering service", "Real people, nuance", "A fraction of the price, answers instantly, never busy, and knows the actual services and prices"],
        ["Upfirst and similar AI receptionists", "Funded, further ahead on polish", "Equivalent website onboarding, plus honest reporting and per-business knowledge the owner edits directly"],
        ["Voicemail / nothing", "Free", "Quantified: every missed call is visible in the dashboard the day they start"],
        ["Generic AI agent builders", "Flexible", "Vertical defaults and real telephony provisioning in one step, not a toolkit"],
    ],
    [36 * mm, 48 * mm, 81 * mm],
))

A(para("Positioning statement", H2))
A(callout(
    "One sentence",
    "For small businesses that lose work to unanswered calls, Evenline is an AI receptionist that "
    "learns the business from its own website in about a minute — so the first thing an owner "
    "sees is their own services read back to them, not a blank form.",
))

A(PageBreak())

# ---------------------------------------------------------------------------
# Page 4 — packaging and pricing
# ---------------------------------------------------------------------------
A(para("3. Packaging and pricing", H1))
A(para("The plans exist in the product. The prices do not, and that is the next decision.", LEAD))

A(para("Plans as built", H2))
A(table(
    [
        ["Plan", "Included minutes", "Intended buyer", "Price"],
        ["Starter", "500 / month", "A single location that wants every call answered", "Not set"],
        ["Pro", "2,000 / month", "Busy teams with a steady stream of calls", "Not set"],
        ["Enterprise", "10,000 / month", "Multi-location, high volume", "Not set"],
    ],
    [24 * mm, 30 * mm, 76 * mm, 35 * mm],
))
A(Spacer(1, 2 * mm))
A(para(
    "Prices are deliberately absent from the codebase so that no figure is shown to a customer "
    "before it is decided. Setting them is a one-line change per plan.", SMALL))

A(para("What the floor has to cover", H2))
A(para(
    "Pricing must clear variable cost per minute plus a share of fixed infrastructure. Using the "
    "per-call economics in section 5, a Starter customer consuming half their included minutes "
    "costs single-digit dollars a month in vendor spend. The constraint is not cost — it is "
    "positioning against the $200–600 answering service the customer is comparing against.", BODY))

A(para("Recommended structure", H2))
for line in bullets([
    "<b>Anchor against the alternative, not against cost.</b> The comparison is a human answering service, not an API bill.",
    "<b>Charge monthly with included minutes and a clear overage rate</b> — the usage meter and plan limits are already built and enforced.",
    "<b>Keep a 14-day trial</b>, which the billing gate already grants, but see the warning below.",
    "<b>Do not discount the first month.</b> The value is proven in week one by the Calls page; a discount invites churn at renewal.",
]):
    A(line)

A(Spacer(1, 2 * mm))
A(callout(
    "Commercial risk to close before launch",
    "The deployed environment currently grants a trial on signup with live vendors enabled, which "
    "means <b>every signup at the public URL buys a real phone number</b> that bills monthly until "
    "released. Before any public launch, either require a payment method at signup or cap trial "
    "provisioning. This is the single largest uncontrolled cost in the product today.",
))

A(para("Expansion revenue, in build order", H2))
A(table(
    [
        ["Lever", "State", "Why it earns its place"],
        ["Calendar booking during a call", "Integrations connected; booking not built", "The most requested capability; turns a message-taker into a booker"],
        ["SMS follow-up", "Templates configurable, sending not built", "Caller asks for a link; agent texts it"],
        ["Multi-location / multi-number", "Data model supports it", "Natural Enterprise upsell"],
        ["Languages beyond English", "Not supported", "Opens whole regions; requires provider work"],
    ],
    [42 * mm, 50 * mm, 73 * mm],
))

A(PageBreak())

# ---------------------------------------------------------------------------
# Page 5 — go-to-market motion
# ---------------------------------------------------------------------------
A(para("4. Go-to-market motion", H1))
A(para("How the first hundred customers arrive.", LEAD))

A(para("Channel plan", H2))
A(table(
    [
        ["Channel", "Motion", "Why it suits this product"],
        ["Founder-led outbound", "Call the business. Let their own receptionist answer. Leave the demo as the voicemail.", "The product demonstrates itself in thirty seconds, in their own words"],
        ["Vertical partnerships", "Salon software, practice management, local bar associations", "One integration or referral reaches hundreds of qualified owners"],
        ["Local search and directories", "Rank for “answering service” intent by city and vertical", "Buying intent is explicit and geographic"],
        ["Referral", "Owners talk to other owners in the same trade", "Trust transfers inside a vertical better than any ad"],
    ],
    [38 * mm, 62 * mm, 65 * mm],
))

A(para("The demo that sells it", H2))
A(para(
    "Take the prospect's website address during the conversation. Paste it in. Thirty seconds later, "
    "read their own services and hours back to them from the review screen. The demo is the "
    "onboarding, which means every demo is already half an installation.", BODY))

A(para("Launch sequence", H2))
A(table(
    [
        ["Phase", "Goal", "Gate to the next phase"],
        ["0 — Close the cost risk", "Payment method or trial cap before public signup", "No signup can provision a number unattended"],
        ["1 — Design partners (5–10)", "One vertical, hand-held onboarding, daily feedback", "Call containment and summary quality judged acceptable by owners"],
        ["2 — Paid pilot (25–50)", "Prices set, trials converting", "Conversion and churn measurable over two cycles"],
        ["3 — Scale one vertical", "Repeatable acquisition in a single trade", "CAC payback under three months"],
    ],
    [42 * mm, 58 * mm, 65 * mm],
))

A(para("Metrics that decide whether this works", H2))
for line in bullets([
    "<b>Setup completion rate</b>, website path versus manual — the core bet of the product.",
    "<b>Time from signup to first answered call.</b>",
    "<b>Calls handled without escalation</b>, by vertical.",
    "<b>Summary accuracy</b>, sampled and read by a human weekly — the thing that destroys trust fastest.",
    "<b>Trial-to-paid conversion</b>, and <b>gross margin per customer</b> once prices exist.",
]):
    A(line)

A(PageBreak())

# ---------------------------------------------------------------------------
# Page 6 — cost management I: deployment
# ---------------------------------------------------------------------------
A(para("5. Cost management I — deployment", H1))
A(para("What it costs to keep the platform running on AWS, per day.", LEAD))

A(para(
    "Built from the deployed task definitions and architecture in <font face='Courier'>infra/ecs/</font>. "
    "Live billing could not be read while preparing this document because the AWS session had expired, "
    "so these are list-price estimates, not invoiced amounts. The verification commands are on page 8.", BODY))

A(para("Running cost by component", H2))
A(table(
    [
        ["Component", "Specification", "Per month", "Per day"],
        ["ECS Fargate — API", "0.5 vCPU, 1 GB", "$18", "$0.59"],
        ["ECS Fargate — frontend", "0.25 vCPU, 0.5 GB", "$9", "$0.30"],
        ["ECS Fargate — worker", "0.25 vCPU, 0.5 GB", "$9", "$0.30"],
        ["ECS Fargate — Temporal worker", "desired count 0", "$0", "$0.00"],
        ["Application Load Balancer", "hourly + ~1 LCU", "$18–22", "$0.60–0.72"],
        ["RDS PostgreSQL", "single-AZ, class to confirm", "$15–28", "$0.49–0.92"],
        ["ElastiCache Serverless", "minimum spend", "$6–60", "$0.20–1.97"],
        ["Public IPv4 × 3 tasks", "$0.005 per hour each", "$11", "$0.36"],
        ["CloudWatch Logs, ECR, Secrets Manager", "1 secret, few images", "$4", "$0.13"],
        ["CloudFront", "low traffic, free tier", "$0–1", "$0.00–0.03"],
        ["<b>Total</b>", "<b>one task per service</b>", "<b>$90–140</b>", "<b>$3.00–4.60</b>"],
    ],
    [50 * mm, 46 * mm, 35 * mm, 34 * mm],
))

A(para("Where the money actually goes", H2))
for line in bullets([
    "<b>ElastiCache Serverless is the one to check first.</b> It bills a minimum whether used or not, and Redis here is only a cache — every read already falls back to PostgreSQL. A small node, or disabling it, is the largest single saving available.",
    "<b>The load balancer is pure fixed cost</b> at roughly $20 a month regardless of traffic. Unavoidable while HTTPS is served through CloudFront.",
    "<b>Public IPv4 addresses cost about $11 a month</b> because tasks run in public subnets. This was the right trade: a NAT Gateway would cost more.",
    "<b>Temporal is costing nothing</b> — the orchestrator runs as a state machine on the existing worker, with the Temporal service scaled to zero.",
]):
    A(line)
A(Spacer(1, 1 * mm))
A(callout(
    "Scaling shape",
    "Of the $3.00–$4.60 a day, roughly <b>$2.70 is fixed</b> — it does not change whether the "
    "platform serves one customer or two hundred. Per-customer marginal infrastructure cost is close "
    "to zero until call volume forces a second API task. That makes early customers almost entirely "
    "gross-margin positive once vendor usage is covered.",
))

A(PageBreak())

# ---------------------------------------------------------------------------
# Page 7 — cost management II: tools and APIs
# ---------------------------------------------------------------------------
A(para("6. Cost management II — tools and APIs", H1))
A(para("What the third-party services cost while building and running the product, per day.", LEAD))

A(para("Build-time cost, per day", H2))
A(para(
    "These are the services consumed during development: model calls to generate agent "
    "configurations and website knowledge, telephony numbers bought for testing, and voice minutes "
    "spent on test calls. Rates are list prices and should be confirmed against each vendor's "
    "invoice.", BODY))
A(table(
    [
        ["Service", "What it is used for", "Basis", "Per day (build)"],
        ["OpenAI (gpt-5.4-mini)", "Agent configuration, call summaries, website knowledge extraction", "Per token; tens to low hundreds of calls a day in development", "$0.30–1.50"],
        ["Twilio", "Phone numbers and inbound voice minutes", "~$1.15 per number per month, plus per-minute voice", "$0.10–0.50"],
        ["ElevenLabs", "The voice agent itself — the dominant per-minute cost", "Per conversation minute", "$0.50–1.50"],
        ["Resend", "Call summary and system email", "Free tier covers development volume", "$0.00"],
        ["Temporal Cloud", "Not in use; state-machine orchestrator instead", "—", "$0.00"],
        ["GitHub, Docker Desktop, ngrok", "Source control, builds, local webhook tunnel", "Free tiers at this team size", "$0.00"],
        ["<b>Total</b>", "<b>build-time tooling and APIs</b>", "", "<b>$1.20–3.60</b>"],
    ],
    [34 * mm, 54 * mm, 42 * mm, 35 * mm],
))
A(Spacer(1, 1 * mm))
A(para(
    "<b>Important:</b> most development runs in dry-run mode, where every vendor is replaced by a "
    "fake and the true cost is zero. The figures above describe days when live vendors were enabled "
    "for end-to-end testing. The entire automated test suite — 1,043 backend tests — runs "
    "against fakes and costs nothing.", NOTE))

A(para("Per-call unit economics, in production", H2))
A(para(
    "This is the number that decides gross margin. A typical answered call runs two to four minutes.", BODY))
A(table(
    [
        ["Cost element", "Driver", "Per 3-minute call"],
        ["Voice agent minutes", "ElevenLabs conversation time", "$0.24–0.36"],
        ["Telephony minutes", "Twilio inbound voice", "$0.03"],
        ["Summarization", "One model call per finished call", "$0.01 or less"],
        ["Number rental, amortized", "One number per customer", "$0.04 per day regardless of calls"],
        ["<b>Marginal cost per call</b>", "", "<b>~$0.28–0.40</b>"],
    ],
    [50 * mm, 72 * mm, 43 * mm],
))
A(Spacer(1, 2 * mm))
A(para(
    "A Starter customer using 250 of their 500 included minutes costs roughly <b>$25–$35 a "
    "month</b> in vendor spend. Any monthly price set meaningfully below the $200–600 answering "
    "service it replaces still leaves a healthy margin — provided the trial cannot provision "
    "numbers unattended.", BODY))

A(PageBreak())

# ---------------------------------------------------------------------------
# Page 8 — controls, risks, next steps
# ---------------------------------------------------------------------------
A(para("7. Cost controls, risks and next steps", H1))
A(para("What keeps the bill honest, and what to do first.", LEAD))

A(para("Controls already built into the product", H2))
for line in bullets([
    "<b>Dry-run mode</b> replaces every paid vendor with a fake, so development and the whole test suite cost nothing. Crawling is bounded too — page count, depth, bytes, time and concurrency are capped, so no website can run up an open-ended model bill.",
    "<b>A billing gate before the money step.</b> Provisioning pauses before a number is bought unless the account is entitled, then resumes when billing clears.",
    "<b>Idempotent purchasing.</b> Three guards — an existing active row, a vendor lookup by name, and a pending row written before the call — make buying two numbers for one business structurally hard. A setup that fails terminally releases its number and deletes its agent rather than leaving both billing silently.",
    "<b>Deletion with release.</b> Owners delete a receptionist and hand the number back in one action, stopping the rental.",
]):
    A(line)

A(para("Open cost risks", H2))
A(table(
    [
        ["Risk", "Exposure", "Action"],
        ["Trial signups buying real numbers", "$1.15/month each, indefinitely", "Require a payment method, or cap trial provisioning, before launch"],
        ["Orphaned numbers from testing", "Unknown until audited", "Audit Twilio for numbers with no active business; release them"],
        ["ElastiCache Serverless minimum", "Up to ~$60/month for a cache that has a database fallback", "Measure usage; move to a small node or disable"],
        ["No spend cap on model usage", "Grows with call volume", "Record token usage per call — the ledger exists, unused — and alert on anomalies"],
    ],
    [40 * mm, 55 * mm, 70 * mm],
))

A(para("Verifying these numbers", H2))
A(para(
    "Every figure in sections 5 and 6 is a list-price estimate. To replace them with invoiced "
    "amounts, authenticate and run:", BODY))
A(table(
    [
        ["Question", "Command"],
        ["What did AWS actually cost?", "<font face='Courier' size='7.5'>aws ce get-cost-and-usage --granularity MONTHLY --metrics UnblendedCost --group-by Type=DIMENSION,Key=SERVICE</font>"],
        ["How many tasks are running?", "<font face='Courier' size='7.5'>aws ecs describe-services --cluster ai-receptionist --services ...</font>"],
        ["What are the database and cache?", "<font face='Courier' size='7.5'>aws rds describe-db-instances</font> and <font face='Courier' size='7.5'>aws elasticache describe-serverless-caches</font>"],
    ],
    [45 * mm, 120 * mm],
))

A(para("Next three actions", H2))
for line in bullets([
    "<b>Close the trial provisioning gap.</b> It is the only uncontrolled spend in the product and it blocks any public launch.",
    "<b>Set prices.</b> Everything else in the commercial model waits on this; the plans already carry them.",
    "<b>Run the billing audit above</b> and replace these estimates with invoiced figures before quoting margins outside the team.",
]):
    A(line)

A(Spacer(1, 4 * mm))
A(para(
    "Prepared from the repository and deployment as of 14 October 2026. Product facts come from the "
    "codebase; monetary figures are list-price estimates.",
    SMALL,
))


doc = BaseDocTemplate(
    OUT,
    pagesize=A4,
    leftMargin=20 * mm,
    rightMargin=20 * mm,
    topMargin=18 * mm,
    bottomMargin=20 * mm,
    title="Evenline AI Receptionist - Go-to-market and cost management",
    author="Evenline",
)
frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
doc.addPageTemplates([PageTemplate(id="page", frames=[frame], onPage=decorate)])
doc.build(story)
print(f"wrote {OUT}")
