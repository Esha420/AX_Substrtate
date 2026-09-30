import os
import uuid
import contextlib
import logging

# Fallback no-op tracer and context manager if opentelemetry is not present
_tracer = None
_provider = None

# Semantic conventions
try:
    from opentelemetry.semconv._incubating.attributes import gen_ai_attributes as GenAI
    GEN_AI_OPERATION_NAME = GenAI.GEN_AI_OPERATION_NAME
    GEN_AI_AGENT_NAME = GenAI.GEN_AI_AGENT_NAME
    GEN_AI_AGENT_ID = GenAI.GEN_AI_AGENT_ID
    GEN_AI_TOOL_NAME = GenAI.GEN_AI_TOOL_NAME
    GEN_AI_TOOL_CALL_ID = GenAI.GEN_AI_TOOL_CALL_ID
    GEN_AI_TOOL_TYPE = GenAI.GEN_AI_TOOL_TYPE
except Exception:
    GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
    GEN_AI_AGENT_NAME = "gen_ai.agent.name"
    GEN_AI_AGENT_ID = "gen_ai.agent.id"
    GEN_AI_TOOL_NAME = "gen_ai.tool.name"
    GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"
    GEN_AI_TOOL_TYPE = "gen_ai.tool.type"

def init_telemetry():
    global _tracer, _provider
    if _tracer is not None:
        return _tracer

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.urllib import URLLibInstrumentor

        service_name = os.getenv("OTEL_SERVICE_NAME", "openclaw-agent")
        agent_id = os.getenv("AGENT_ID", os.getenv("HOSTNAME", "openclaw-actor"))
        actor_name = os.getenv("ACTOR_NAME", "openclaw-task")

        resource_attrs = {
            "service.name": service_name,
            "service.version": "1.0.0",
            "agent.id": agent_id,
            "ate.actor.name": actor_name,
            "substrate.platform": "google-ax-ate",
            "k8s.pod.name": os.getenv("HOSTNAME", "unknown"),
        }

        resource = Resource.create(resource_attrs)
        _provider = TracerProvider(resource=resource)

        endpoint = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
        if not endpoint:
            base = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://opentelemetry-collector.otel-system.svc.cluster.local:4318")
            endpoint = f"{base.rstrip('/')}/v1/traces"

        exporter = OTLPSpanExporter(endpoint=endpoint)
        _provider.add_span_processor(
            BatchSpanProcessor(exporter, max_export_batch_size=5, schedule_delay_millis=500)
        )
        trace.set_tracer_provider(_provider)

        # Automatically instrument all urllib requests (for mock-scan-api calls)
        try:
            URLLibInstrumentor().instrument()
            logging.info("[OTel] URLLib auto-instrumentation active.")
        except Exception as e:
            logging.warning(f"[OTel] URLLib instrumentation warning: {e}")

        _tracer = trace.get_tracer("openclaw-agent", "1.0.0")
        logging.info(f"[OTel] OpenTelemetry HTTP exporter initialized: {endpoint}")
        return _tracer
    except Exception as e:
        logging.warning(f"[OTel] Tracing disabled or failed to initialize ({e}). Continuing in no-op mode.")
        return None

def get_tracer():
    global _tracer
    if _tracer is None:
        return init_telemetry()
    return _tracer

def flush_telemetry():
    global _provider
    if _provider:
        try:
            _provider.force_flush(timeout_millis=5000)
        except Exception as e:
            logging.warning(f"[OTel] Flush error: {e}")

@contextlib.contextmanager
def trace_agent(agent_name="openclaw-agent", agent_id=None):
    tracer = get_tracer()
    if tracer is None:
        yield None
        return

    from opentelemetry.trace import Status, StatusCode
    agent_id = agent_id or os.getenv("AGENT_ID", str(uuid.uuid4())[:8])
    span_name = f"invoke_agent {agent_name}"
    attrs = {
        GEN_AI_OPERATION_NAME: "invoke_agent",
        GEN_AI_AGENT_NAME: agent_name,
        GEN_AI_AGENT_ID: agent_id,
        "gen_ai.system": "google-ax-substrate",
    }
    with tracer.start_as_current_span(span_name, attributes=attrs) as span:
        try:
            yield span
        except Exception as e:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise

@contextlib.contextmanager
def trace_tool(tool_name, tool_type="function", tool_args=None):
    tracer = get_tracer()
    if tracer is None:
        yield None
        return

    from opentelemetry.trace import Status, StatusCode
    tool_call_id = str(uuid.uuid4())
    span_name = f"execute_tool {tool_name}"
    attrs = {
        GEN_AI_OPERATION_NAME: "execute_tool",
        GEN_AI_TOOL_NAME: tool_name,
        GEN_AI_TOOL_TYPE: tool_type,
        GEN_AI_TOOL_CALL_ID: tool_call_id,
    }
    if tool_args:
        attrs["gen_ai.tool.call.arguments"] = str(tool_args)

    with tracer.start_as_current_span(span_name, attributes=attrs) as span:
        try:
            yield span
        except Exception as e:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise
