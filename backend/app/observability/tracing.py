"""OpenTelemetry tracing. Exports via OTLP/HTTP when enabled; otherwise spans are still created
(no-op exporter) so trace ids propagate into execution records."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)
_configured = False


def configure_tracing(app: Any | None = None) -> None:
    global _configured
    if _configured:
        return
    s = get_settings()
    provider = TracerProvider(resource=Resource.create({"service.name": s.otel_service_name, "deployment.environment": s.env}))
    if s.otel_enabled:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{s.otel_endpoint}/v1/traces")))
            log.info("otel_exporter_enabled endpoint=%s", s.otel_endpoint)
        except Exception as exc:  # noqa: BLE001
            log.warning("otel_exporter_failed: %s", exc)
    trace.set_tracer_provider(provider)
    if app is not None and s.otel_enabled:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            FastAPIInstrumentor.instrument_app(app, excluded_urls="health,metrics")
        except Exception as exc:  # noqa: BLE001
            log.warning("otel_fastapi_instrumentation_failed: %s", exc)
    _configured = True


tracer = trace.get_tracer("aiecc")


@contextmanager
def span(name: str, **attrs: Any):
    with tracer.start_as_current_span(name) as sp:
        for k, v in attrs.items():
            if v is not None:
                sp.set_attribute(k, v if isinstance(v, str | int | float | bool) else str(v))
        yield sp


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx and ctx.trace_id else None
