from dataclasses import dataclass
from typing import NewType


@dataclass
class GraylogEndpoint:
    """Server and port for a graylog instance."""

    host: str
    """GELF UDP Input server hostname"""
    port: int
    """GELF UDP Input port"""

    @classmethod
    def parse_graylog_endpoint(cls, endpoint: str) -> "GraylogEndpoint":
        try:
            host, port = endpoint.split(":")
            port = int(port)
        except Exception as e:
            raise ValueError(
                "Invalid graylog endpoint. Expected '<host>:<port>'."
            ) from e

        return cls(host, port)


GraylogEnvFields = NewType("GraylogEnvFields", dict[str, str])
"""Fields to add to graylog messages from environment variables."""


def parse_graylog_env_fields(comma_separated_fields: str) -> GraylogEnvFields:
    try:
        return GraylogEnvFields(_parse_graylog_env_fields(comma_separated_fields))
    except Exception as e:
        raise ValueError(
            "Failed to parse fields. Expected '<field_name>:<env_name>,...'"
        ) from e


GraylogStaticFields = NewType("GraylogStaticFields", dict[str, str])
"""Fields to add to graylog messages with static values."""


def parse_graylog_static_fields(comma_separated_fields: str) -> GraylogStaticFields:
    try:
        return GraylogStaticFields(_parse_graylog_env_fields(comma_separated_fields))
    except Exception as e:
        raise ValueError("Failed to parse fields. Expected '<key>:<value>,...'") from e


def _parse_graylog_env_fields(comma_separated_fields: str) -> dict[str, str]:
    return {
        field: env
        for pair in comma_separated_fields.split(",")
        for field, env in [pair.split(":")]
    }
