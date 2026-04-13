"""Debug logging helpers for pytest-appium-scheduler."""

from __future__ import annotations

from typing import Any

import pytest


def log_debug(
    config: pytest.Config,
    action: str,
    *,
    worker_id: str | None = None,
    scheduled_device_name: str | None = None,
    device_name: str | None = None,
    session_id: str | None = None,
    caps: dict[str, Any] | None = None,
    details: str | None = None,
) -> None:
    if not config.getoption("appium_trace", default=False):
        return

    parts = [f"[pytest-appium-scheduler] action={action}"]
    if worker_id:
        parts.append(f"worker={worker_id}")
    if scheduled_device_name:
        parts.append(f"target={scheduled_device_name}")
    if device_name:
        parts.append(f"device={device_name}")
    if session_id:
        parts.append(f"session={session_id}")
    if caps is not None:
        parts.append(f"caps={caps}")
    if details:
        parts.append(f"details={details}")
    print(" ".join(parts), flush=True)
