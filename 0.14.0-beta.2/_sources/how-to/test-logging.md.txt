# Testing Log Output

FastCS uses [loguru](https://loguru.readthedocs.io) for logging. The test suite provides
a `loguru_caplog` fixture that bridges loguru into pytest's standard `caplog` mechanism,
making it straightforward to assert on log messages in tests.

## The `loguru_caplog` Fixture

The fixture is defined in `tests/conftest.py` and registers a loguru sink that forwards
all messages (down to `TRACE` level) into pytest's `caplog`:

:::{literalinclude} ../../tests/conftest.py
:pyobject: loguru_caplog
:::

Use it by adding `loguru_caplog` as a parameter to your test function. The yielded value
is pytest's standard `caplog` object, so the same assertions work:

- `loguru_caplog.text` — all captured log output as a single string
- `loguru_caplog.records` — list of `logging.LogRecord` objects, each with a `.message`
  attribute

## Asserting on ERROR-level Messages

Pass `loguru_caplog` to any test that exercises code that calls `logger.error(...)` or
similar. After the code runs, assert against `loguru_caplog.text`:

:::{literalinclude} ../../tests/test_attribute_logging.py
:pyobject: test_attr_r_update_logs_validation_error
:::

The same pattern applies when a callback raises:

:::{literalinclude} ../../tests/test_attribute_logging.py
:pyobject: test_attr_r_update_logs_callback_failure
:::

## Asserting on TRACE-level Messages

`log_event` calls (from the `Tracer` mixin) emit at `TRACE` level and are only active
when tracing is enabled on that object. The `loguru_caplog` fixture captures at `TRACE`
level, so no extra setup is needed beyond enabling tracing on the object under test.

Use `loguru_caplog.records` and check `.message` on each record for precise matching:

:::{literalinclude} ../../tests/test_attribute_logging.py
:pyobject: test_attr_r_update_trace_logs_when_tracing_enabled
:::

You can also verify that messages are absent when tracing is not enabled:

:::{literalinclude} ../../tests/test_attribute_logging.py
:pyobject: test_attr_r_update_no_trace_logs_when_tracing_disabled
:::
