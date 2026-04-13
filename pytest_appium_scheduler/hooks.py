"""Shared hook state and helpers."""

from __future__ import annotations

from pathlib import Path
import tempfile

import pytest

from .config import AppiumSchedulerConfig
from .debug import log_debug
from .device import Device
from .driver import ResilientDriverProxy, create_driver_with_retries
from .scheduler import DevicePool, ScheduledItemStore

ELIGIBLE_DEVICE_NAMES_KEY: pytest.StashKey[tuple[str, ...]] = pytest.StashKey()
TARGET_DEVICE_NAME_KEY: pytest.StashKey[str | None] = pytest.StashKey()
RESOLVED_DEVICE_KEY: pytest.StashKey[Device] = pytest.StashKey()
OWNED_DEVICE_KEY: pytest.StashKey[bool] = pytest.StashKey()
DRIVER_KEY: pytest.StashKey[object] = pytest.StashKey()
REPORT_TARGET_DEVICE_NAME_KEY: pytest.StashKey[str] = pytest.StashKey()
REPORT_DEVICE_NAME_KEY: pytest.StashKey[str] = pytest.StashKey()
REPORT_SESSION_ID_KEY: pytest.StashKey[str] = pytest.StashKey()
REPORT_WORKER_ID_KEY: pytest.StashKey[str] = pytest.StashKey()


class AppiumSessionRuntime:
    def __init__(
        self,
        pytest_config: pytest.Config,
        runtime_config: AppiumSchedulerConfig,
        state_dir: Path,
    ) -> None:
        self.pytest_config = pytest_config
        self.runtime_config = runtime_config
        self.worker_id = getattr(pytest_config, "workerinput", {}).get("workerid", "master")
        self.lease_id = f"{self.worker_id}:{id(self)}"
        self.pool = DevicePool(state_dir=state_dir, devices=list(runtime_config.devices))
        self.scheduled_items = ScheduledItemStore(state_dir=state_dir)
        self._current_device: Device | None = None
        self._current_driver: ResilientDriverProxy | None = None
        self._last_device_name: str | None = None

    def eligible_devices_for_item(self, item: pytest.Item) -> list[str]:
        exact_target = item.stash.get(TARGET_DEVICE_NAME_KEY, None)
        if exact_target:
            return [exact_target]
        eligible_names = list(item.stash.get(ELIGIBLE_DEVICE_NAMES_KEY, tuple()))
        scheduled_target = self.scheduled_items.get(item.nodeid)
        if scheduled_target and scheduled_target in eligible_names:
            return [scheduled_target]
        return eligible_names

    def scheduled_device_name_for_item(self, item: pytest.Item) -> str | None:
        exact_target = item.stash.get(TARGET_DEVICE_NAME_KEY, None)
        if exact_target:
            return exact_target
        scheduled_target = self.scheduled_items.get(item.nodeid)
        if scheduled_target:
            return scheduled_target
        eligible_names = list(item.stash.get(ELIGIBLE_DEVICE_NAMES_KEY, tuple()))
        if len(eligible_names) == 1:
            return eligible_names[0]
        return None

    def report_target_device_name(self, item: pytest.Item, actual_device_name: str | None = None) -> str | None:
        scheduled_target = item.stash.get(REPORT_TARGET_DEVICE_NAME_KEY, None)
        if scheduled_target is not None:
            return scheduled_target
        scheduled_target = self.scheduled_device_name_for_item(item)
        if scheduled_target is None:
            scheduled_target = actual_device_name
        if scheduled_target is not None:
            item.stash[REPORT_TARGET_DEVICE_NAME_KEY] = scheduled_target
        return scheduled_target

    def acquire_device(self, item: pytest.Item) -> Device:
        scheduled_target = self.scheduled_device_name_for_item(item)
        eligible_names = self._ordered_eligible_names(self.eligible_devices_for_item(item))
        log_debug(
            self.pytest_config,
            "device-acquire-attempt",
            worker_id=self.worker_id,
            scheduled_device_name=scheduled_target,
            details=f"nodeid={item.nodeid} eligible={eligible_names}",
        )
        preferred_name = None
        if self.runtime_config.driver_scope == "session" and self._current_device is not None:
            if self._current_device.name in eligible_names:
                resolved_target = self.report_target_device_name(item, self._current_device.name)
                log_debug(
                    self.pytest_config,
                    "device-reuse",
                    worker_id=self.worker_id,
                    scheduled_device_name=resolved_target,
                    device_name=self._current_device.name,
                    details=f"nodeid={item.nodeid}",
                )
                return self._current_device
            preferred_name = self._current_device.name
            self._shutdown_session_resources()
        elif self._last_device_name in eligible_names:
            preferred_name = self._last_device_name

        device = self.pool.acquire(
            eligible_names=eligible_names,
            lease_id=self.lease_id,
            preferred_name=preferred_name,
        )
        self._last_device_name = device.name
        resolved_target = self.report_target_device_name(item, device.name)
        log_debug(
            self.pytest_config,
            "device-acquire",
            worker_id=self.worker_id,
            scheduled_device_name=resolved_target,
            device_name=device.name,
            caps=device.caps,
            details=f"nodeid={item.nodeid}",
        )
        if self.runtime_config.driver_scope == "session":
            self._current_device = device
        return device

    def get_driver(self, item: pytest.Item, device: Device) -> ResilientDriverProxy:
        target_device_name = self.report_target_device_name(item, device.name)
        if (
            self.runtime_config.driver_scope == "session"
            and self._current_driver is not None
            and self._current_device is not None
            and self._current_device.name == device.name
        ):
            log_debug(
                self.pytest_config,
                "driver-reuse",
                worker_id=self.worker_id,
                scheduled_device_name=target_device_name,
                device_name=device.name,
                session_id=getattr(self._current_driver.wrapped, "session_id", None),
            )
            return self._current_driver

        if self.runtime_config.driver_scope == "session":
            if self._current_device is not None and self._current_device.name != device.name:
                self._shutdown_session_resources()
            self._current_device = device

        factory = lambda: create_driver_with_retries(
            config=self.pytest_config,
            device=device,
            retries=self.runtime_config.retry_session,
            worker_id=self.worker_id,
            scheduled_device_name=target_device_name,
        )
        driver = ResilientDriverProxy(
            factory=factory,
            retries=self.runtime_config.retry_session,
            config=self.pytest_config,
            worker_id=self.worker_id,
            scheduled_device_name=target_device_name,
            device_name=device.name,
        )
        log_debug(
            self.pytest_config,
            "driver-create",
            worker_id=self.worker_id,
            scheduled_device_name=target_device_name,
            device_name=device.name,
            session_id=getattr(driver.wrapped, "session_id", None),
            caps=device.caps,
        )
        if self.runtime_config.driver_scope == "session":
            self._current_driver = driver
        return driver

    def release_item(self, item: pytest.Item) -> None:
        driver = item.stash.get(DRIVER_KEY, None)
        device = item.stash.get(RESOLVED_DEVICE_KEY, None)
        owned_device = item.stash.get(OWNED_DEVICE_KEY, False)
        target_device_name = self.report_target_device_name(item, getattr(device, "name", None))

        if self.runtime_config.driver_scope == "function":
            if driver is not None:
                log_debug(
                    self.pytest_config,
                    "driver-quit",
                    worker_id=self.worker_id,
                    scheduled_device_name=target_device_name,
                    device_name=getattr(device, "name", None),
                    session_id=getattr(getattr(driver, "wrapped", None), "session_id", None),
                )
                driver.quit()
            if device is not None and owned_device:
                log_debug(
                    self.pytest_config,
                    "device-release",
                    worker_id=self.worker_id,
                    scheduled_device_name=target_device_name,
                    device_name=device.name,
                )
                self.pool.release(device.name, self.lease_id)
        for key in (
            DRIVER_KEY,
            REPORT_TARGET_DEVICE_NAME_KEY,
            RESOLVED_DEVICE_KEY,
            OWNED_DEVICE_KEY,
            TARGET_DEVICE_NAME_KEY,
        ):
            if key in item.stash:
                del item.stash[key]

    def close(self) -> None:
        self._shutdown_session_resources()

    def cleanup(self) -> None:
        self.pool.cleanup()

    def _shutdown_session_resources(self) -> None:
        if self._current_driver is not None:
            log_debug(
                self.pytest_config,
                "driver-quit",
                worker_id=self.worker_id,
                device_name=getattr(self._current_device, "name", None),
                session_id=getattr(self._current_driver.wrapped, "session_id", None),
            )
            self._current_driver.quit()
            self._current_driver = None
        if self._current_device is not None:
            log_debug(
                self.pytest_config,
                "device-release",
                worker_id=self.worker_id,
                device_name=self._current_device.name,
            )
            self.pool.release(self._current_device.name, self.lease_id)
            self._current_device = None

    def _ordered_eligible_names(self, eligible_names: list[str]) -> list[str]:
        if len(eligible_names) <= 1:
            return eligible_names
        start = _worker_index(self.worker_id) % len(eligible_names)
        return eligible_names[start:] + eligible_names[:start]


def build_state_dir(config: pytest.Config) -> Path:
    workerinput = getattr(config, "workerinput", None) or {}
    shared_dir = workerinput.get("appium_state_dir")
    if shared_dir:
        return Path(shared_dir)
    return Path(
        tempfile.mkdtemp(
            prefix="pytest-appium-scheduler-",
            dir=str(config.rootpath),
        )
    )


def _worker_index(worker_id: str) -> int:
    if worker_id == "master":
        return 0
    digits = "".join(ch for ch in worker_id if ch.isdigit())
    if digits:
        return int(digits)
    return 0
