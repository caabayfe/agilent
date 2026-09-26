"""Minimal structured logging setup shared by every service."""

import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level,
        stream=sys.stdout,
        format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
    )
    # httpx logs full URLs at INFO; keep it quiet.
    for noisy in ("httpx", "httpx2", "httpcore", "mcp.client.streamable_http", "mcp.server.streamable_http_manager"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
