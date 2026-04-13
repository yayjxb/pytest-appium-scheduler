"""Configuration loading for pytest-appium-scheduler."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from .device import Device
from .exceptions import InvalidConfigError

ENV_PREFIX = "PYTEST_APPIUM_"


@dataclass(frozen=True)
class AppiumSchedulerConfig:
    mode: str
    driver_scope: str
    retry_session: int
    debug: bool
    config_path: Path | None
    devices: tuple[Device, ...]
    selected_device_names: tuple[str, ...]

    @property
    def devices_by_name(self) -> dict[str, Device]:
        return {device.name: device for device in self.devices}


def add_cli_options(parser: pytest.Parser) -> None:
    group = parser.getgroup("appium-scheduler")
    group.addoption(
        "--appium-mode",
        action="store",
        choices=("distributed", "all"),
        default=None,
        help="Device scheduling mode: distributed or all.",
    )
    group.addoption(
        "--appium-device",
        action="append",
        default=None,
        metavar="NAME",
        help="Run only against the selected device name. Can be provided multiple times.",
    )
    group.addoption(
        "--appium-driver-scope",
        action="store",
        choices=("function", "session"),
        default=None,
        help="Driver lifecycle strategy.",
    )
    group.addoption(
        "--appium-retry-session",
        action="store",
        type=int,
        default=None,
        help="How many times driver session creation/recovery should be retried.",
    )
    group.addoption(
        "--appium-config",
        action="store",
        default=None,
        metavar="PATH",
        help="Path to the device yaml config file.",
    )
    group.addoption(
        "--appium-debug",
        action="store_true",
        default=None,
        help="Print compact per-test device assignment logs.",
    )
    group.addoption(
        "--appium-trace",
        action="store_true",
        default=None,
        help="Print verbose device scheduling and driver lifecycle trace logs.",
    )


def load_runtime_config(config: pytest.Config) -> AppiumSchedulerConfig:
    mode = _option_or_env(config, "appium_mode", "MODE", "distributed")
    driver_scope = _option_or_env(config, "appium_driver_scope", "DRIVER_SCOPE", "function")
    retry_session = int(_option_or_env(config, "appium_retry_session", "RETRY_SESSION", 1))
    debug = _as_bool(_option_or_env(config, "appium_debug", "DEBUG", False))

    config_path_value = _option_or_env(config, "appium_config", "CONFIG", None)
    config_path = Path(config_path_value).expanduser().resolve() if config_path_value else None

    devices = tuple(_load_devices(config_path))
    selected_device_names = tuple(_selected_device_names(config))
    if selected_device_names:
        available = {device.name for device in devices}
        devices = tuple(device for device in devices if device.name in selected_device_names)
        unknown = [name for name in selected_device_names if name not in available]
        for name in unknown:
            config.issue_config_time_warning(
                pytest.PytestConfigWarning(
                    f"--appium-device selected unknown device '{name}', it will be ignored."
                ),
                stacklevel=2,
            )

    return AppiumSchedulerConfig(
        mode=mode,
        driver_scope=driver_scope,
        retry_session=max(retry_session, 0),
        debug=debug,
        config_path=config_path,
        devices=devices,
        selected_device_names=selected_device_names,
    )


def _selected_device_names(config: pytest.Config) -> list[str]:
    cli_values = config.getoption("appium_device") or None
    if cli_values:
        return list(cli_values)
    env_value = os.getenv(f"{ENV_PREFIX}DEVICE")
    if not env_value:
        return []
    return [part.strip() for part in env_value.split(",") if part.strip()]


def _option_or_env(
    config: pytest.Config,
    option_name: str,
    env_suffix: str,
    default: Any,
) -> Any:
    value = config.getoption(option_name)
    if value is not None:
        return value
    env_name = f"{ENV_PREFIX}{env_suffix}"
    if env_name in os.environ:
        return os.environ[env_name]
    return default


def _load_devices(config_path: Path | None) -> list[Device]:
    if config_path is None:
        return []
    if not config_path.exists():
        raise InvalidConfigError(f"Appium config file does not exist: {config_path}")

    try:
        raw_data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise InvalidConfigError(f"Invalid yaml in appium config: {config_path}") from exc

    if not isinstance(raw_data, dict):
        raise InvalidConfigError("Appium config root must be a mapping.")

    raw_devices = raw_data.get("devices", [])
    if not isinstance(raw_devices, list):
        raise InvalidConfigError("'devices' must be a list.")

    devices: list[Device] = []
    for index, raw_device in enumerate(raw_devices):
        if not isinstance(raw_device, dict):
            raise InvalidConfigError(f"Device entry at index {index} must be a mapping.")
        name = raw_device.get("name")
        caps = raw_device.get("caps", {})
        url = raw_device.get("url", "http://127.0.0.1:4723")
        if not name or not isinstance(name, str):
            raise InvalidConfigError(f"Device entry at index {index} is missing a valid 'name'.")
        if not isinstance(caps, dict):
            raise InvalidConfigError(f"Device '{name}' has invalid 'caps'.")
        if not isinstance(url, str):
            raise InvalidConfigError(f"Device '{name}' has invalid 'url'.")
        devices.append(Device(name=name, caps=caps, url=url))
    return devices


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on"}
    return bool(value)
