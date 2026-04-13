"""Driver lifecycle helpers."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable

import pytest

from .debug import log_debug
from .device import Device
from .exceptions import DriverInitError


def create_driver_with_retries(
    config: pytest.Config,
    device: Device,
    retries: int,
    worker_id: str | None = None,
    scheduled_device_name: str | None = None,
) -> Any:
    attempts = retries + 1
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            log_debug(
                config,
                "driver-create-attempt",
                worker_id=worker_id,
                scheduled_device_name=scheduled_device_name,
                device_name=device.name,
                details=f"attempt={attempt + 1}/{attempts}",
            )
            return _create_driver(config, device)
        except Exception as exc:  # pragma: no cover - exact vendor errors vary
            last_error = exc
            log_debug(
                config,
                "driver-create-failed",
                worker_id=worker_id,
                scheduled_device_name=scheduled_device_name,
                device_name=device.name,
                details=f"attempt={attempt + 1}/{attempts} error={exc}",
            )
    raise DriverInitError(
        f"Failed to create driver for device '{device.name}' after {attempts} attempts."
    ) from last_error


class ResilientDriverProxy:
    """Retry a driver command once after session loss by recreating the driver."""

    def __init__(
        self,
        factory: Callable[[], Any],
        retries: int,
        config: pytest.Config,
        worker_id: str | None = None,
        scheduled_device_name: str | None = None,
        device_name: str | None = None,
    ) -> None:
        self._factory = factory
        self._retries = retries
        self._config = config
        self._worker_id = worker_id
        self._scheduled_device_name = scheduled_device_name
        self._device_name = device_name
        self._driver = factory()

    @property
    def wrapped(self) -> Any:
        return self._driver

    def quit(self) -> None:
        quit_fn = getattr(self._driver, "quit", None)
        if callable(quit_fn):
            quit_fn()

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._driver, name)
        if not callable(target):
            return target

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            attempts = self._retries + 1
            last_error: Exception | None = None
            for attempt in range(attempts):
                try:
                    current = getattr(self._driver, name)
                    return current(*args, **kwargs)
                except Exception as exc:  # pragma: no cover - exact vendor errors vary
                    last_error = exc
                    if attempt >= self._retries or not _is_recoverable_session_error(exc):
                        raise
                    log_debug(
                        self._config,
                        "driver-recover",
                        worker_id=self._worker_id,
                        scheduled_device_name=self._scheduled_device_name,
                        device_name=self._device_name,
                        session_id=getattr(self._driver, "session_id", None),
                        details=f"method={name} attempt={attempt + 1}/{attempts} error={exc}",
                    )
                    self._recreate()
            raise last_error  # pragma: no cover

        return wrapped

    def _recreate(self) -> None:
        self.quit()
        self._driver = self._factory()


def _create_driver(config: pytest.Config, device: Device) -> Any:
    prepared_device = _prepare_device_for_driver(config, device)

    hook_driver = config.hook.pytest_appium_create_driver(device=prepared_device)
    if hook_driver is not None:
        return hook_driver

    try:
        from appium import webdriver
        from appium.options.common.base import AppiumOptions
    except ImportError as exc:  # pragma: no cover - optional runtime dependency
        raise DriverInitError(
            "appium-python-client is not installed. Install it or implement pytest_appium_create_driver."
        ) from exc

    options = AppiumOptions()
    options.load_capabilities(dict(prepared_device.caps))
    return webdriver.Remote(command_executor=prepared_device.normalized_url(), options=options)


def _prepare_device_for_driver(config: pytest.Config, device: Device) -> Device:
    caps = dict(device.caps)
    modified_caps = config.hook.pytest_appium_modify_caps(caps=caps)
    if modified_caps is not None:
        caps = dict(modified_caps)
    return replace(device, caps=caps)


def _is_recoverable_session_error(exc: Exception) -> bool:
    message = str(exc).casefold()
    recoverable_markers = (
        "invalid session id",
        "session deleted",
        "session timed out",
        "newcommandtimeout",
        "connection refused",
        "broken pipe",
    )
    return any(marker in message for marker in recoverable_markers)
