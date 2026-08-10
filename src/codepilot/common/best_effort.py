from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import TypeVar

logger = logging.getLogger(__name__)
T = TypeVar("T")


def run_best_effort(
    operation: Callable[[], T],
    *,
    operation_name: str,
    context: Mapping[str, object],
    expected_errors: tuple[type[Exception], ...],
    on_error: Callable[[Exception], None] | None = None,
) -> T | None:
    """Run a non-critical side effect while making its failure observable."""

    try:
        return operation()
    except expected_errors as exc:
        logger.warning(
            "%s failed",
            operation_name,
            extra={**context, "error_type": type(exc).__name__},
            exc_info=True,
        )
        if on_error is not None:
            on_error(exc)
        return None
