"""``connector_type`` -> connector. Each connector's config model carries only what it
needs to read its export (a path for the local exports); Lattice-side settings
(defaults, account mapping, channel settings) never reach a connector."""

from pathlib import Path

from lattice.connectors.contract import Connector, ConnectorType
from lattice.connectors.markdown import MarkdownConfig, MarkdownConnector
from lattice.connectors.slack import SlackConfig, SlackConnector
from lattice.connectors.tickets import TicketsConfig, TicketsConnector


def build_connector(connector_type: ConnectorType, path: Path) -> Connector:
    if connector_type == "document_repository":
        return MarkdownConnector(MarkdownConfig(path=path))
    if connector_type == "ticketing":
        return TicketsConnector(TicketsConfig(path=path))
    return SlackConnector(SlackConfig(path=path))
