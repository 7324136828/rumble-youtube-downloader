"""Connector lookup by URL or id."""
from .base import Connector
from .generic import GenericConnector
from .rumble import RumbleConnector
from .youtube import YouTubeConnector

CONNECTORS: list[Connector] = [
    YouTubeConnector(),
    RumbleConnector(),
    GenericConnector(),
]


def resolve(url: str) -> Connector | None:
    for connector in CONNECTORS:
        if connector.matches(url):
            return connector
    return None


def get(connector_id: str) -> Connector | None:
    for connector in CONNECTORS:
        if connector.id == connector_id:
            return connector
    return None


def describe() -> list[dict]:
    return [connector.describe() for connector in CONNECTORS]
