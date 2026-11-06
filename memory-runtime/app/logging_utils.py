from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
from collections.abc import Mapping
from contextvars import ContextVar

# Request ID of the call being served (from the bridge's X-Request-ID) or of
# the job being processed. log_event adds it to every line, so one ID ties
# the bridge, the API request and the worker's consolidation outcome together.
current_request_id: ContextVar[str | None] = ContextVar("current_request_id", default=None)
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def accepted_request_id(value: str | None) -> str:
    """The caller's request ID when well formed, otherwise a new one."""
    if value and _REQUEST_ID.match(value):
        return value
    return "rt_" + secrets.token_hex(8)


def fingerprint(value: str | None) -> str | None:
    """A short stable hash for values that may carry user identifiers."""
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def configure_logging(*, level: str = "INFO") -> None:
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(
            level=getattr(logging, level.upper(), logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
    else:
        root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))


def log_event(
    logger: logging.Logger,
    event: str,
    /,
    *,
    level: int = logging.INFO,
    **fields: object,
) -> None:
    payload: dict[str, object] = {"event": event}
    request_id = current_request_id.get()
    if request_id and "request_id" not in fields:
        payload["request_id"] = request_id
    payload.update(_drop_none(fields))
    logger.log(
        level,
        json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str),
    )


def _drop_none(fields: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in fields.items() if value is not None}
