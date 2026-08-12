import pytest

from service.registry import PipelineRegistry, RegistryError


class FakePipeline:
    """Minimal object satisfying the Pipeline protocol."""

    name = "fake"

    async def warmup(self):
        return None

    async def prepare_input(self, context):
        return {}

    async def ainvoke(self, state):
        return state

    async def extract_output(self, final_state):
        return {}


def test_register_and_get():
    reg = PipelineRegistry()
    p = FakePipeline()
    reg.register("mock-v1", p)
    assert reg.get("mock-v1") is p
    assert "mock-v1" in reg


def test_duplicate_registration_fails():
    reg = PipelineRegistry()
    reg.register("mock-v1", FakePipeline())
    with pytest.raises(RegistryError, match="already registered"):
        reg.register("mock-v1", FakePipeline())


def test_unknown_pipeline_fails():
    reg = PipelineRegistry()
    with pytest.raises(RegistryError, match="unknown pipeline"):
        reg.get("nope")


def test_names_sorted():
    reg = PipelineRegistry()
    for name in ["c", "a", "b"]:
        reg.register(name, FakePipeline())
    assert reg.names() == ["a", "b", "c"]


def test_object_not_satisfying_protocol_rejected():
    reg = PipelineRegistry()
    with pytest.raises(RegistryError, match="does not satisfy the Pipeline protocol"):
        reg.register("bad", object())


@pytest.mark.parametrize(
    "missing", ["warmup", "prepare_input", "ainvoke", "extract_output"]
)
def test_each_missing_protocol_method_rejected(missing):
    class Partial(FakePipeline):
        pass

    setattr(Partial, missing, None)
    reg = PipelineRegistry()
    with pytest.raises(RegistryError, match=missing):
        reg.register("partial", Partial())


def test_warmup_all_awaits_every_pipeline():
    import asyncio

    calls = []

    class Recording(FakePipeline):
        def __init__(self, tag):
            self.tag = tag

        async def warmup(self):
            calls.append(self.tag)

    reg = PipelineRegistry()
    reg.register("b", Recording("b"))
    reg.register("a", Recording("a"))
    asyncio.run(reg.warmup_all())
    assert calls == ["a", "b"]  # sorted order


def test_clear_makes_startup_repeatable():
    reg = PipelineRegistry()
    reg.register("a", FakePipeline())
    reg.clear()
    assert reg.names() == []
    reg.register("a", FakePipeline())  # must not raise
    assert reg.names() == ["a"]
