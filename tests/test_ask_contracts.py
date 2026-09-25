from app.domains.registry import implemented_domains
from app.services.ask.contracts import AskTool, Citable, ToolOutput
from tests.domain_fakes import make_fake_spec


def test_ask_tool_to_api_shape():
    tool = AskTool(name="house_example", description="Example.",
                   input_schema={"type": "object", "properties": {}}, run=lambda s, a: ToolOutput(text="ok"))
    assert tool.to_api() == {"name": "house_example", "description": "Example.",
                             "input_schema": {"type": "object", "properties": {}}}


def test_tool_output_defaults():
    out = ToolOutput(text="hi")
    assert out.citables == [] and out.content_blocks == []
    assert out.used_raw_sources is False and out.is_error is False


def test_citable_is_a_value_object():
    assert Citable("wiki:1", "Boiler", "/wiki/1") == Citable("wiki:1", "Boiler", "/wiki/1")


def test_domain_spec_ask_tools_defaults_to_empty_tuple():
    assert make_fake_spec().ask_tools == ()
    for spec in implemented_domains():
        assert isinstance(spec.ask_tools, tuple)
