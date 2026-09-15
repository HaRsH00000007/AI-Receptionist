"""Metrics.

Deliberately small and dependency-free. A Prometheus client library would bring
a process-wide global registry, a second configuration surface and a set of
import-time side effects, in exchange for features this application does not
use. What it actually needs is a handful of counters and histograms, exposed in
a format every scraper already understands.

**What is measured is chosen, not collected.** Each metric below exists because
a specific question gets asked during an incident and would otherwise be
answered by reading logs:

* did provisioning succeed, and how long did it take? (the SLO)
* which vendor is failing, and on which step? (who to call)
* are webhooks being rejected, and why? (a signature rotation gone wrong)
* is ``/voice/init`` inside its budget? (the only latency a customer *hears*)
* is the worker draining its queues, or falling behind? (silent backlog)

**Label values are bounded, always.** A tenant id as a label is an unbounded
cardinality explosion that kills the scraper first and the database second — so
identifiers go in logs and traces, where they belong, and labels carry only
small closed sets like a step name or an outcome. :func:`_safe_label` enforces
that for values that come from outside.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

#: Histogram buckets, in seconds.
#:
#: Chosen around the thresholds that matter rather than spread evenly: the
#: `/voice/init` budget is 300ms, a vendor call is usually under a second, and
#: anything past ten is already a customer-visible failure.
DEFAULT_BUCKETS: tuple[float, ...] = (0.05, 0.1, 0.3, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)

#: Any label value longer than this is truncated. A vendor error message used
#: as a label would otherwise create a new time series per unique message.
_MAX_LABEL_LENGTH = 64

_LabelKey = tuple[tuple[str, str], ...]


def _safe_label(value: object) -> str:
    """Coerce a label value into something bounded and escapable."""
    text = str(value)
    if len(text) > _MAX_LABEL_LENGTH:
        text = text[:_MAX_LABEL_LENGTH]
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _labels_key(labels: dict[str, object] | None) -> _LabelKey:
    if not labels:
        return ()
    return tuple(sorted((name, _safe_label(value)) for name, value in labels.items()))


def _render_labels(key: _LabelKey, extra: tuple[str, str] | None = None) -> str:
    pairs = [f'{name}="{value}"' for name, value in key]
    if extra is not None:
        pairs.append(f'{extra[0]}="{extra[1]}"')
    return "{" + ",".join(pairs) + "}" if pairs else ""


@dataclass(slots=True)
class Counter:
    """A monotonically increasing count."""

    name: str
    help_text: str
    values: dict[_LabelKey, float] = field(default_factory=dict)

    def inc(self, amount: float = 1.0, **labels: object) -> None:
        key = _labels_key(labels)
        self.values[key] = self.values.get(key, 0.0) + amount

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help_text}", f"# TYPE {self.name} counter"]
        for key, value in sorted(self.values.items()):
            lines.append(f"{self.name}{_render_labels(key)} {value:g}")
        return lines


@dataclass(slots=True)
class Histogram:
    """A distribution, as cumulative buckets plus a sum and a count."""

    name: str
    help_text: str
    buckets: tuple[float, ...] = DEFAULT_BUCKETS
    counts: dict[_LabelKey, list[int]] = field(default_factory=dict)
    sums: dict[_LabelKey, float] = field(default_factory=dict)
    totals: dict[_LabelKey, int] = field(default_factory=dict)

    def observe(self, seconds: float, **labels: object) -> None:
        key = _labels_key(labels)
        counts = self.counts.setdefault(key, [0] * len(self.buckets))
        for index, edge in enumerate(self.buckets):
            if seconds <= edge:
                counts[index] += 1
        self.sums[key] = self.sums.get(key, 0.0) + seconds
        self.totals[key] = self.totals.get(key, 0) + 1

    @contextmanager
    def time(self, **labels: object) -> Iterator[None]:
        """Time a block. Records even when the block raises.

        A failed operation's duration is the interesting one during an
        incident: "it hangs for thirty seconds and then fails" and "it fails
        instantly" are different problems with different causes.
        """
        started = time.perf_counter()
        try:
            yield
        finally:
            self.observe(time.perf_counter() - started, **labels)

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help_text}", f"# TYPE {self.name} histogram"]
        for key in sorted(self.counts):
            counts = self.counts[key]
            for edge, count in zip(self.buckets, counts, strict=True):
                labels = _render_labels(key, ("le", f"{edge:g}"))
                lines.append(f"{self.name}_bucket{labels} {count}")
            total = self.totals.get(key, 0)
            lines.append(f"{self.name}_bucket{_render_labels(key, ('le', '+Inf'))} {total}")
            lines.append(f"{self.name}_sum{_render_labels(key)} {self.sums.get(key, 0.0):g}")
            lines.append(f"{self.name}_count{_render_labels(key)} {total}")
        return lines


class MetricsRegistry:
    """Every metric this process reports.

    One instance lives on ``app.state`` rather than at module scope, so tests
    get a clean registry per application and assertions cannot leak between
    them — the usual reason a metrics test is flaky.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.counters: dict[str, Counter] = {}
        self.histograms: dict[str, Histogram] = {}
        self._register_defaults()

    def counter(self, name: str, help_text: str = "") -> Counter:
        with self._lock:
            if name not in self.counters:
                self.counters[name] = Counter(name, help_text or name)
            return self.counters[name]

    def histogram(
        self, name: str, help_text: str = "", buckets: tuple[float, ...] = DEFAULT_BUCKETS
    ) -> Histogram:
        with self._lock:
            if name not in self.histograms:
                self.histograms[name] = Histogram(name, help_text or name, buckets)
            return self.histograms[name]

    def _register_defaults(self) -> None:
        """Declare every metric up front.

        So that a dashboard panel reads ``0`` rather than "no data" before the
        first event — the difference between "nothing is failing" and "the
        exporter is broken", which is not a distinction to be making at 3am.
        """
        self.counter(
            "provisioning_steps_total",
            "Provisioning steps executed, by step and outcome.",
        )
        self.histogram(
            "provisioning_step_duration_seconds",
            "Wall time per provisioning step, including failed attempts.",
        )
        self.counter(
            "provisioning_runs_total",
            "Provisioning runs reaching a terminal state, by outcome.",
        )
        self.counter(
            "provider_calls_total",
            "Outbound vendor calls, by provider and outcome. Names who to call.",
        )
        self.counter(
            "webhook_deliveries_total",
            "Inbound webhooks, by provider and status. A spike in rejected "
            "usually means a rotated signing secret.",
        )
        self.counter(
            "calls_processed_total",
            "Post-call processing outcomes.",
        )
        self.counter(
            "inbound_calls_total",
            "Inbound calls by routing disposition. Voicemail rising means "
            "callers are not reaching an agent.",
        )
        self.histogram(
            "voice_init_duration_seconds",
            "Time to serve /voice/init. The only latency a caller can hear; "
            "budget is 300ms at p99.",
            buckets=(0.01, 0.05, 0.1, 0.2, 0.3, 0.5, 1.0, 2.0),
        )
        self.counter(
            "notifications_total",
            "Notification delivery outcomes.",
        )

    def render(self) -> str:
        """The Prometheus text exposition format."""
        lines: list[str] = []
        with self._lock:
            for counter in sorted(self.counters.values(), key=lambda item: item.name):
                lines.extend(counter.render())
            for histogram in sorted(self.histograms.values(), key=lambda item: item.name):
                lines.extend(histogram.render())
        return "\n".join(lines) + "\n"


#: A process-wide default, for code that runs outside a request — the worker and
#: the Temporal activities, which have no ``app.state`` to reach for.
#:
#: The API deliberately does *not* use this one; it keeps its own on
#: ``app.state`` so that tests are isolated from each other.
WORKER_METRICS = MetricsRegistry()
