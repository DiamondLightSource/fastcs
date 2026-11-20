import pytest

from fastcs.attributes import AttrR


@pytest.mark.asyncio
async def test_attr_r_update_trace_logs_when_tracing_enabled(loguru_caplog):
    """log_event emits 'Attribute set' and 'Value validated' when tracing is on."""
    attr = AttrR(int)
    attr.enable_tracing()

    await attr.update(42)

    messages = [r.message for r in loguru_caplog.records]
    assert any("Attribute set" in m for m in messages)
    assert any("Value validated" in m for m in messages)


@pytest.mark.asyncio
async def test_attr_r_update_no_trace_logs_when_tracing_disabled(loguru_caplog):
    attr = AttrR(int)

    await attr.update(42)

    messages = [r.message for r in loguru_caplog.records]
    assert not any("Attribute set" in m for m in messages)
    assert not any("Value validated" in m for m in messages)


@pytest.mark.asyncio
async def test_attr_r_update_logs_validation_error(loguru_caplog):
    attr = AttrR(int)

    with pytest.raises(ValueError):
        await attr.update("not_an_int")  # type: ignore[arg-type]

    assert "Failed to validate value" in loguru_caplog.text


@pytest.mark.asyncio
async def test_attr_r_update_logs_callback_failure(loguru_caplog):
    attr = AttrR(int)

    async def failing_callback(_value: int):
        raise RuntimeError("callback failed")

    attr.add_readback_callback(failing_callback)

    with pytest.raises(RuntimeError):
        await attr.update(42)

    assert "Readback callbacks failed" in loguru_caplog.text
