#!/usr/bin/env python3
"""The verify Job's half of the trace boundary (Factory#638).

``verify_dispatch`` puts ``TRACEPARENT`` + the OTLP config on the Job manifest;
``tools.runners.job_tracing`` is what the Job itself calls to join that trace and
emit. Configuration alone is not coverage — Factory#607's live mutation showed a
FABRICATED traceparent still lands a healthy-looking ``<service>-job`` span,
under a trace nobody is watching — so these tests assert the two properties the
manifest cannot: that the span's PARENT is the id the dispatcher sent, and that
each way of breaking the seam produces no span in the dispatcher's trace rather
than a plausible-looking one.

``job_tracing.py`` is a byte-exact vendored copy of the hub canonical
``scripts/job_tracing.py`` (verification-core drift gate). Never edit it here.
"""

from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from tools.runners import job_tracing

# A well-formed W3C traceparent: version-traceid-spanid-flags, sampled.
TRACE_ID_HEX = "4bf92f3577b34da6a3ce929d0e0e4736"
PARENT_SPAN_HEX = "00f067aa0ba902b7"
SAMPLE_TP = f"00-{TRACE_ID_HEX}-{PARENT_SPAN_HEX}-01"


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    """Undo the module's global latch between tests.

    ``init_agent_tracing`` is idempotent by design (a single attach token), which
    means one test's initialisation would otherwise silently satisfy the next.
    """
    monkeypatch.setattr(job_tracing, "_initialized", False)
    monkeypatch.setattr(job_tracing, "_attach_token", None)
    monkeypatch.setattr(job_tracing, "_job_span", None)
    monkeypatch.setattr(job_tracing, "_provider", None)
    for var in (
        "TRACEPARENT",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_SERVICE_NAME",
        "OTEL_SERVICE_NAME_AGENT",
        "FACTORY_SERVICE",
        "JOB_ID",
        "CORRELATION_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def spans(monkeypatch):
    """Capture the spans this process emits, without a collector.

    The module reuses an already-installed ``TracerProvider`` rather than
    replacing it, so installing one here with an in-memory exporter is enough to
    see what it would have exported.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", provider, raising=False)
    return exporter


def _finish() -> None:
    """Run the atexit hook by hand — pytest does not exit between tests."""
    job_tracing._flush_at_exit()


def test_job_span_is_a_child_of_the_dispatchers_span(monkeypatch, spans):
    """The property the whole change exists for: same trace, right parent."""
    monkeypatch.setenv("TRACEPARENT", SAMPLE_TP)
    monkeypatch.setenv("FACTORY_SERVICE", "tfactory")

    job_tracing.init_agent_tracing()
    _finish()

    finished = spans.get_finished_spans()
    assert len(finished) == 1, "exactly one span per Job process, never one per step"
    span = finished[0]
    assert f"{span.context.trace_id:032x}" == TRACE_ID_HEX
    assert f"{span.parent.span_id:016x}" == PARENT_SPAN_HEX
    assert span.name == "tfactory job"


def test_span_carries_the_scalars_that_lead_back_to_the_run(monkeypatch, spans):
    monkeypatch.setenv("TRACEPARENT", SAMPLE_TP)
    monkeypatch.setenv("FACTORY_SERVICE", "tfactory")
    monkeypatch.setenv("JOB_ID", "verify-123")
    monkeypatch.setenv("CORRELATION_KEY", "9001")

    job_tracing.init_agent_tracing()
    _finish()

    attrs = spans.get_finished_spans()[0].attributes
    assert attrs["factory.service"] == "tfactory"
    assert attrs["factory.job_id"] == "verify-123"
    # The key every log line in this fleet already carries. Trace context
    # replaced it as a PARENTING mechanism, not as an identifier.
    assert attrs["factory.correlation_key"] == "9001"


def test_no_traceparent_emits_nothing(monkeypatch, spans):
    """Mutation 1. No trace to join means no span — an honest gap, not a fake one.

    An unparented job span is span volume with no question attached, and it is
    exactly what the ``trace-silence-check`` join assertion flags as an orphan.
    """
    monkeypatch.setenv("FACTORY_SERVICE", "tfactory")

    job_tracing.init_agent_tracing()
    _finish()

    assert spans.get_finished_spans() == ()
    assert job_tracing._job_span is None


def test_fabricated_traceparent_lands_nothing_in_the_dispatchers_trace(
    monkeypatch, spans
):
    """Mutation 2, the instructive one.

    A fabricated traceparent still produces a perfectly healthy span — that is
    the point. What it does NOT do is land in the dispatcher's trace, and no
    count-by-service check can tell the difference. Only the trace_id can.
    """
    fabricated = "00-" + "ab" * 16 + "-" + "cd" * 8 + "-01"
    monkeypatch.setenv("TRACEPARENT", fabricated)
    monkeypatch.setenv("FACTORY_SERVICE", "tfactory")

    job_tracing.init_agent_tracing()
    _finish()

    finished = spans.get_finished_spans()
    assert len(finished) == 1, "it emits, which is why the config looks fine"
    assert f"{finished[0].context.trace_id:032x}" != TRACE_ID_HEX


def test_no_otlp_endpoint_installs_no_exporter(monkeypatch, spans):
    """Mutation 3. Off-cluster the process joins the trace and exports nothing.

    The span still exists in-process so log lines carry the run's trace_id; what
    is absent is any exporter, so no retry budget is spent on a collector that
    is not there.
    """
    monkeypatch.setenv("TRACEPARENT", SAMPLE_TP)

    class _RecordingProvider:
        def __init__(self) -> None:
            self.processors: list[object] = []

        def add_span_processor(self, processor: object) -> None:
            self.processors.append(processor)

    provider = _RecordingProvider()
    job_tracing._install_exporter(provider)
    assert provider.processors == [], "no endpoint must mean no exporter"

    # And with one, it installs exactly one — otherwise this test would pass just
    # as happily against an _install_exporter that never installs anything.
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://observe:5080/api/default")
    job_tracing._install_exporter(provider)
    assert len(provider.processors) == 1

    # The Job still joins the trace either way, so its logs carry the run's
    # trace_id; what is absent off-cluster is only the export.
    job_tracing.init_agent_tracing()
    _finish()
    assert len(spans.get_finished_spans()) == 1


def test_service_name_defaults_to_the_dispatchers_job_name(monkeypatch):
    """``<service>-job`` is the shape trace-silence-check's join assertion matches.

    Job-side names must stay OUT of its ``EXPECTED_SERVICES``: Jobs are episodic,
    so a quiet hour is idleness, and a check that reddens on idleness is a check
    people learn to ignore.
    """
    monkeypatch.setenv("FACTORY_SERVICE", "tfactory")
    assert job_tracing._service_name() == "tfactory-job"

    monkeypatch.setenv("OTEL_SERVICE_NAME", "tfactory-job")
    assert job_tracing._service_name() == "tfactory-job"


def test_garbage_traceparent_does_not_raise(monkeypatch, spans):
    """Tracing is never allowed to be the reason a verify fails."""
    monkeypatch.setenv("TRACEPARENT", "not-a-traceparent")

    job_tracing.init_agent_tracing()
    _finish()

    assert job_tracing._initialized is True


def test_init_is_idempotent(monkeypatch, spans):
    monkeypatch.setenv("TRACEPARENT", SAMPLE_TP)

    job_tracing.init_agent_tracing()
    first = job_tracing._job_span
    job_tracing.init_agent_tracing()

    assert job_tracing._job_span is first


def test_verify_pipeline_entry_point_calls_the_bootstrap():
    """The half that cannot be asserted from the manifest.

    ``verify_dispatch`` shipping ``trace_env`` without this is the failure this
    issue refused to ship: correct-looking configuration on a Job that emits
    nothing reads as coverage while the trace still stops at the seam.
    """
    from agents import verify_pipeline

    assert verify_pipeline.init_agent_tracing is job_tracing.init_agent_tracing
