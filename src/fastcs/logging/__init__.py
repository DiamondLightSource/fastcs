import logging

from loguru import logger as _logger

from ._graylog import GraylogEndpoint as GraylogEndpoint
from ._graylog import GraylogEnvFields as GraylogEnvFields
from ._graylog import GraylogStaticFields as GraylogStaticFields
from ._graylog import parse_graylog_env_fields as parse_graylog_env_fields
from ._graylog import parse_graylog_static_fields as parse_graylog_static_fields
from ._logging import LogLevel, _configure_logger

logger = _logger.bind(logger_name="fastcs")
"""FastCS logger

This is a singleton logger instance to be used throughout the library and in specific
drivers. This logger uses ``loguru`` as underlying logging library, which enables much
simpler configuration as well as structured logging.

Keyword arguments to log statements will be attached as extra fields on the log record.
These fields are displayed separately in the console output and can used for filtering
and metrics in graylog.

It is best to keep the message short and use extra fields for additional information for
messages to be formatted nicely in the console.

.. code-block:: python

    from fastcs.logging import logger

    logger.info("PV put: {pv} = {value}", pv=pv, value=value)

To add kwargs to format the message without them appearing as extra fields, prepend the
key with ``_``.

By default messages will be logged with the name ``fastcs``. Within a driver it may be
useful to set a distinct logger name. This can be done with the ``bind`` method. To
create a new logger with the the name of the driver, use the following in a logging.py
module and use it throughout the package instead of the fastcs logger:

.. code-block:: python

    from fastcs.logging import logger as _logger

    logger = _logger.bind(logger_name="fastcs-driver")

As standard ``loguru`` supports ``trace`` level monitoring, but it should not be used in
fastcs. Instead there is a `Tracer` class for verbose logging with fine-grained
controls that can be enabled by the user at runtime.

Use ``configure_logging`` to re-configure the logger at runtime. For more advanced
controls, configure the ``logger`` singleton directly.

See the ``loguru`` docs for more information: https://loguru.readthedocs.io
"""


def configure_logging(
    level: LogLevel = LogLevel.INFO,
    graylog_endpoint: GraylogEndpoint | None = None,
    graylog_static_fields: GraylogStaticFields | None = None,
    graylog_env_fields: GraylogEnvFields | None = None,
):
    """Configure FastCS logger

    This function can be called at any time to
      - Change the console log level
      - Enable/disable graylog logging

    Args:
        level: Log level to set
        graylog_endpoint: Endpoint for graylog logging - '<host>:<port>'
        graylog_static_fields: Fields to add to graylog messages with static values
        graylog_env_fields: Fields to add to graylog messages from env variables

    """
    global logger

    _configure_logger(
        logger, level, graylog_endpoint, graylog_static_fields, graylog_env_fields
    )


class _StdLoggingInterceptHandler(logging.Handler):
    """A std logging handler to forward messages to loguru with nice formatting."""

    def emit(self, record: logging.LogRecord):
        logger.bind(logger_name=self.name).log(record.levelname, record.getMessage())


def intercept_std_logger(logger_name: str):
    """Intercept std logging messages from the given logger

    To find the correct ``logger_name`` for a message. Add a breakpoint in
    ``logging.Logger.callHandlers``, debug and run until the log message appears as
    ``record.msg``. The logger name will be in ``self.name``.

    Args:
        logger_name: Name of the logger to intercept

    """
    handler = _StdLoggingInterceptHandler()
    handler.set_name(logger_name)

    std_logger = logging.getLogger(logger_name)
    std_logger.handlers = [handler]
    std_logger.propagate = False


# For sphinx autodoc to find things import from private modules
__all__ = [
    "logger",
    "configure_logging",
    "intercept_std_logger",
    "GraylogEndpoint",
]
