import os
import uuid
import contextlib
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.trace import Status, StatusCode

# GenAI Semantic Conventions (Standard + AI extension fallback)
try:
    from opentelemetry.semconv._incubating.attributes import gen_ai_attributes as GenAI
    GEN_AI_OPERATION_NAME = GenAI.GEN_AI_OPERATION_NAME
    GEN_AI_AGENT_NAME = GenAI.GEN_AI_AGENT_NAME
    GEN_AI_AGENT_ID = GenAI.GEN_AI_AGENT_ID
    GEN_AI_TOOL_NAME = GenAI.GEN_AI_TOOL_NAME
    GEN_AI_TOOL_CALL_ID = GenAI.GEN_AI_TOOL_CALL_ID
    GEN_AI_TOOL_TYPE = GenAI.GEN_AI_TOOL_TYPE
    GEN_AI_PROVIDER_NAME = GenAI.GEN_AI_PROVIDER_NAME
    GEN_AI_REQUEST_MODEL = GenAI.GEN_AI_REQUEST_MODEL
    GEN_AI_USAGE_INPUT_TOKENS = GenAI.GEN_AI_USAGE_INPUT_TOKENS
    GEN_AI_USAGE_OUTPUT_TOKENS = GenAI.GEN_AI_USAGE_OUTPUT_TOKENS
except Exception:
    GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
    GEN_AI_AGENT_NAME = "gen_ai.agent.name"
    GEN_AI_AGENT_ID = "gen_ai.agent.id"
    GEN_AI_TOOL_NAME = "gen_ai.tool.name"
    GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"
    GEN_AI_TOOL_TYPE = "gen_ai.tool.type"
    GEN_AI_PROVIDER_NAME = "gen_ai.provider.name"
    GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
    GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
    GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"

_tracer = None
_provider = None

def init_telemetry():
    global _tracer, _provider
    if _tracer is not None:
        return _tracer

    service_name = os.getenv("OTEL_SERVICE_NAME", "security-analyzer")
    agent_id = os.getenv("AGENT_ID", os.getenv("HOSTNAME", "agent-node"))
    actor_name = os.getenv("ACTOR_NAME", service_name)

    resource_attrs = {
        "service.name": service_name,
        "service.version": "1.0.0",
        "agent.id": agent_id,
        "substrate.platform": "google-ax-ate",
        "k8s.pod.name": os.getenv("HOSTNAME", "unknown"),
        "ate.actor.name": actor_name,
    }

    resource = Resource.create(resource_attrs)
    _provider = TracerProvider(resource=resource)

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    if not endpoint:
        base = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://opentelemetry-collector.otel-system.svc.cluster.local:4318")
        endpoint = f"{base.rstrip('/')}/v1/traces"

    try:
        exporter = OTLPSpanExporter(endpoint=endpoint)
        _provider.add_span_processor(
            BatchSpanProcessor(exporter, max_export_batch_size=10, schedule_delay_millis=500)
        )
        trace.set_tracer_provider(_provider)
        print(f"[OTel] Initialized OpenTelemetry HTTP exporter: {endpoint}")
    except Exception as e:
        print(f"[OTel] Warning: Failed to initialize OTLPSpanExporter ({e}). Tracing will run in memory.")
        trace.set_tracer_provider(_provider)

    _tracer = trace.get_tracer("security-analyzer", "1.0.0")
    return _tracer

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
            print(f"[OTel] Flush error: {e}")

@contextlib.contextmanager
def trace_agent(agent_name="security-analyzer", agent_id=None):
    tracer = get_tracer()
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

@contextlib.contextmanager
def trace_chat(model_name="nvidia/nemotron-3-super-120b-a12b", provider="nvidia"):
    tracer = get_tracer()
    short_model = model_name.split("/")[-1]
    span_name = f"chat {short_model}"
    attrs = {
        GEN_AI_OPERATION_NAME: "chat",
        GEN_AI_PROVIDER_NAME: provider,
        GEN_AI_REQUEST_MODEL: model_name,
    }
    with tracer.start_as_current_span(span_name, attributes=attrs) as span:
        try:
            yield span
        except Exception as e:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise
