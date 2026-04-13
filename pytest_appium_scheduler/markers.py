"""Marker parsing and device resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from .device import Device

CAPABILITY_ALIASES = {
    "platform": "platformName",
    "platform_name": "platformName",
    "device_name": "deviceName",
}


@dataclass
class MarkerResolution:
    devices: list[Device]
    warnings: list[str] = field(default_factory=list)


def resolve_devices_for_item(item: pytest.Item, devices: list[Device]) -> MarkerResolution:
    if not devices:
        return MarkerResolution(devices=[], warnings=["No devices are configured."])

    selectors = _collect_name_selectors(item)
    attribute_filters = _collect_attribute_filters(item)

    if not selectors and not attribute_filters:
        return MarkerResolution(devices=list(devices))

    selected: dict[str, Device] = {}
    warnings: list[str] = []
    devices_by_name = {device.name: device for device in devices}

    for selector in selectors:
        device = devices_by_name.get(selector)
        if device is None:
            warnings.append(f"{item.nodeid}: marker references unknown device '{selector}', item will be skipped.")
            continue
        selected[device.name] = device

    for filters in attribute_filters:
        matches = [device for device in devices if _matches_filters(device, filters)]
        if not matches:
            warnings.append(
                f"{item.nodeid}: marker filter {filters} matched no configured device, item will be skipped."
            )
        for device in matches:
            selected[device.name] = device

    return MarkerResolution(devices=[device for device in devices if device.name in selected], warnings=warnings)


def _collect_name_selectors(item: pytest.Item) -> list[str]:
    names: list[str] = []
    for marker_name in ("device", "devices"):
        for marker in item.iter_markers(name=marker_name):
            names.extend(_extract_marker_names(marker))
    return names


def _collect_attribute_filters(item: pytest.Item) -> list[dict[str, Any]]:
    filters: list[dict[str, Any]] = []
    for marker in item.iter_markers(name="device"):
        if marker.kwargs:
            filters.append(dict(marker.kwargs))
    return filters


def _extract_marker_names(marker: pytest.Mark) -> list[str]:
    names: list[str] = []
    for arg in marker.args:
        if isinstance(arg, str):
            names.append(arg)
            continue
        if isinstance(arg, (list, tuple, set)):
            names.extend(str(entry) for entry in arg)
    return names


def _matches_filters(device: Device, filters: dict[str, Any]) -> bool:
    for raw_key, expected in filters.items():
        key = CAPABILITY_ALIASES.get(raw_key, raw_key)
        actual = getattr(device, key, None)
        if actual is None:
            actual = device.capability(key)
        if isinstance(actual, str) and isinstance(expected, str):
            if actual.casefold() != expected.casefold():
                return False
            continue
        if actual != expected:
            return False
    return True

