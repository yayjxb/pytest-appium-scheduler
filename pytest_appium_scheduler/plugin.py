"""Pytest plugin entrypoint."""

from __future__ import annotations

from typing import Any

import pytest

from .config import AppiumSchedulerConfig, add_cli_options, load_runtime_config
from .exceptions import DeviceNotFoundError, InvalidConfigError
from .hooks import (
    AppiumSessionRuntime,
    DRIVER_KEY,
    ELIGIBLE_DEVICE_NAMES_KEY,
    OWNED_DEVICE_KEY,
    REPORT_DEVICE_NAME_KEY,
    REPORT_SESSION_ID_KEY,
    REPORT_TARGET_DEVICE_NAME_KEY,
    REPORT_WORKER_ID_KEY,
    RESOLVED_DEVICE_KEY,
    TARGET_DEVICE_NAME_KEY,
    build_state_dir,
)
from .markers import resolve_devices_for_item
from .scheduler import CollectedItemStore
from .xdist_scheduler import build_device_aware_scheduler

_LATEST_CONFIG: pytest.Config | None = None


def pytest_addhooks(pluginmanager: pytest.PytestPluginManager) -> None:
    class HookSpecs:
        @pytest.hookspec(firstresult=True)
        def pytest_appium_create_driver(device):  # pragma: no cover - hook spec
            """Create and return a driver for the given device."""

        @pytest.hookspec(firstresult=True)
        def pytest_appium_modify_caps(caps):  # pragma: no cover - hook spec
            """Modify capabilities before driver creation and return the updated mapping."""

    pluginmanager.add_hookspecs(HookSpecs)


def pytest_addoption(parser: pytest.Parser) -> None:
    add_cli_options(parser)


def pytest_configure(config: pytest.Config) -> None:
    global _LATEST_CONFIG
    config.addinivalue_line("markers", "device(*names, **filters): limit a test to matching devices.")
    config.addinivalue_line("markers", "devices(names): limit a test to specific device names.")

    try:
        runtime_config = load_runtime_config(config)
    except InvalidConfigError as exc:
        raise pytest.UsageError(str(exc)) from exc

    config._appium_scheduler_runtime_config = runtime_config  # type: ignore[attr-defined]
    state_dir = build_state_dir(config)
    config._appium_scheduler_state_dir = state_dir  # type: ignore[attr-defined]
    config._appium_scheduler_session_runtime = AppiumSessionRuntime(  # type: ignore[attr-defined]
        pytest_config=config,
        runtime_config=runtime_config,
        state_dir=state_dir,
    )
    _LATEST_CONFIG = config


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node: Any) -> None:
    config = node.config
    state_dir = getattr(config, "_appium_scheduler_state_dir", None)
    if state_dir is not None:
        node.workerinput["appium_state_dir"] = str(state_dir)


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    runtime_config = _runtime_config(metafunc.config)
    if runtime_config.mode != "all" or not runtime_config.devices:
        return

    fixture_name = None
    if "device" in metafunc.fixturenames:
        fixture_name = "device"
    elif "driver" in metafunc.fixturenames:
        fixture_name = "driver"
    if fixture_name is None:
        return

    pseudo_item = _MetafuncProxy(metafunc)
    resolution = resolve_devices_for_item(pseudo_item, list(runtime_config.devices))
    for warning in resolution.warnings:
        metafunc.config.issue_config_time_warning(pytest.PytestWarning(warning), stacklevel=2)

    params = [pytest.param(device.name, id=device.name) for device in resolution.devices]
    if not params:
        params = [
            pytest.param(
                None,
                marks=pytest.mark.skip(reason="No matching Appium device found for this test."),
                id="no-device",
            )
        ]
    metafunc.parametrize(fixture_name, params, indirect=True)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    runtime_config = _runtime_config(config)
    if not runtime_config.devices:
        return
    devices = list(runtime_config.devices)
    item_devices: dict[str, tuple[str, ...]] = {}
    item_requires_device: dict[str, bool] = {}
    for item in items:
        requires_device = _item_requires_device(item)
        exact_target = _parametrized_target_device_name(item)
        if runtime_config.mode == "all" and exact_target is not None:
            eligible_names = (exact_target,)
        else:
            resolution = resolve_devices_for_item(item, devices)
            for warning in resolution.warnings:
                config.issue_config_time_warning(pytest.PytestWarning(warning), stacklevel=2)
            eligible_names = tuple(device.name for device in resolution.devices)
        item.stash[ELIGIBLE_DEVICE_NAMES_KEY] = eligible_names
        item_devices[item.nodeid] = eligible_names
        item_requires_device[item.nodeid] = requires_device
        if not eligible_names:
            item.add_marker(pytest.mark.skip(reason="No matching Appium device found for this test."))
    config._appium_scheduler_item_devices = item_devices  # type: ignore[attr-defined]
    config._appium_scheduler_item_requires_device = item_requires_device  # type: ignore[attr-defined]
    CollectedItemStore(config._appium_scheduler_state_dir).merge(  # type: ignore[attr-defined]
        item_devices,
        item_requires_device,
    )


@pytest.fixture
def device(request: pytest.FixtureRequest):
    runtime_config = _runtime_config(request.config)
    if not runtime_config.devices:
        raise pytest.UsageError("No Appium devices are configured. Pass --appium-config to enable the plugin.")

    runtime = _session_runtime(request.config)
    exact_target = _fixture_param_or_none(request)
    if exact_target is not None:
        request.node.stash[TARGET_DEVICE_NAME_KEY] = exact_target
    item_device = request.node.stash.get(RESOLVED_DEVICE_KEY, None)
    if item_device is None:
        try:
            resolved = runtime.acquire_device(request.node)
        except DeviceNotFoundError as exc:
            pytest.skip(str(exc))
        request.node.stash[RESOLVED_DEVICE_KEY] = resolved
        request.node.stash[OWNED_DEVICE_KEY] = True
        item_device = resolved
    request.node.stash[REPORT_TARGET_DEVICE_NAME_KEY] = (
        runtime.report_target_device_name(request.node, item_device.name) or item_device.name
    )
    request.node.stash[REPORT_DEVICE_NAME_KEY] = item_device.name
    request.node.stash[REPORT_WORKER_ID_KEY] = runtime.worker_id
    try:
        yield item_device
    finally:
        runtime.release_item(request.node)


@pytest.fixture
def driver(request: pytest.FixtureRequest):
    runtime_config = _runtime_config(request.config)
    if not runtime_config.devices:
        raise pytest.UsageError("No Appium devices are configured. Pass --appium-config to enable the plugin.")

    runtime = _session_runtime(request.config)
    exact_target = _fixture_param_or_none(request)
    if exact_target is not None:
        request.node.stash[TARGET_DEVICE_NAME_KEY] = exact_target

    item_device = request.node.stash.get(RESOLVED_DEVICE_KEY, None)
    owned_here = False
    if item_device is None:
        try:
            item_device = runtime.acquire_device(request.node)
        except DeviceNotFoundError as exc:
            pytest.skip(str(exc))
        request.node.stash[RESOLVED_DEVICE_KEY] = item_device
        request.node.stash[OWNED_DEVICE_KEY] = True
        owned_here = True
    request.node.stash[REPORT_TARGET_DEVICE_NAME_KEY] = (
        runtime.report_target_device_name(request.node, item_device.name) or item_device.name
    )
    request.node.stash[REPORT_DEVICE_NAME_KEY] = item_device.name
    request.node.stash[REPORT_WORKER_ID_KEY] = runtime.worker_id

    driver_instance = runtime.get_driver(request.node, item_device)
    request.node.stash[DRIVER_KEY] = driver_instance
    session_id = getattr(getattr(driver_instance, "wrapped", driver_instance), "session_id", None)
    if session_id is not None:
        request.node.stash[REPORT_SESSION_ID_KEY] = str(session_id)
    try:
        yield driver_instance
    finally:
        if owned_here:
            runtime.release_item(request.node)


def pytest_sessionfinish(session: pytest.Session) -> None:
    runtime = _session_runtime(session.config)
    runtime.close()
    if not getattr(session.config, "workerinput", None):
        runtime.cleanup()


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_make_scheduler(config: pytest.Config, log):
    runtime_config = _runtime_config(config)
    if runtime_config.mode not in {"distributed", "all"} or not runtime_config.devices:
        return None
    return build_device_aware_scheduler(config, log)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    report = outcome.get_result()

    target_device_name = item.stash.get(REPORT_TARGET_DEVICE_NAME_KEY, None)
    device_name = item.stash.get(REPORT_DEVICE_NAME_KEY, None)
    worker_id = item.stash.get(REPORT_WORKER_ID_KEY, None)
    session_id = item.stash.get(REPORT_SESSION_ID_KEY, None)

    if target_device_name is not None:
        report.user_properties.append(("appium_target_device", target_device_name))
    if device_name is not None:
        report.user_properties.append(("appium_device", device_name))
    if worker_id is not None:
        report.user_properties.append(("appium_worker", worker_id))
    if session_id is not None:
        report.user_properties.append(("appium_session", session_id))


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    config = _LATEST_CONFIG
    if config is None or not config.getoption("appium_debug", default=False):
        return
    if report.when not in {"call", "setup"}:
        return
    if report.when == "setup" and not (report.failed or report.skipped):
        return

    properties = dict(report.user_properties)
    target_device_name = properties.get("appium_target_device")
    device_name = properties.get("appium_device")
    session_id = properties.get("appium_session")
    worker_id = properties.get("appium_worker") or _report_worker_id(report)
    if target_device_name is None and device_name is None and session_id is None and worker_id is None:
        return

    terminal = config.pluginmanager.get_plugin("terminalreporter")
    if terminal is None:
        return

    terminal.write_line(
        _format_compact_report_line(
            report,
            worker_id=worker_id,
            target_device_name=target_device_name,
            device_name=device_name,
            session_id=session_id,
        )
    )


def _runtime_config(config: pytest.Config) -> AppiumSchedulerConfig:
    return config._appium_scheduler_runtime_config  # type: ignore[attr-defined]


def _session_runtime(config: pytest.Config) -> AppiumSessionRuntime:
    return config._appium_scheduler_session_runtime  # type: ignore[attr-defined]


def _fixture_param_or_none(request: pytest.FixtureRequest) -> str | None:
    if not hasattr(request, "param"):
        return None
    if request.param is None:
        return None
    return str(request.param)


def _item_requires_device(item: pytest.Item) -> bool:
    fixturenames = set(getattr(item, "fixturenames", ()))
    return "device" in fixturenames or "driver" in fixturenames


class _MetafuncProxy:
    def __init__(self, metafunc: pytest.Metafunc) -> None:
        self.definition = metafunc.definition
        self.function = metafunc.function
        self.module = metafunc.module
        self.cls = metafunc.cls
        self.config = metafunc.config
        self.fspath = metafunc.definition.path
        self.nodeid = metafunc.definition.nodeid

    def iter_markers(self, name: str | None = None):
        return self.definition.iter_markers(name=name)


def _report_worker_id(report: pytest.TestReport) -> str | None:
    node = getattr(report, "node", None)
    gateway = getattr(node, "gateway", None)
    return getattr(gateway, "id", None)


def _parametrized_target_device_name(item: pytest.Item) -> str | None:
    callspec = getattr(item, "callspec", None)
    if callspec is None:
        return None
    for fixture_name in ("device", "driver"):
        if fixture_name not in callspec.params:
            continue
        value = callspec.params[fixture_name]
        if value is None:
            return None
        return str(value)
    return None


def _format_compact_report_line(
    report: pytest.TestReport,
    worker_id: str | None,
    target_device_name: str | None,
    device_name: str | None,
    session_id: str | None,
) -> str:
    worker = (worker_id or "main")[:6]
    target = f"target={(target_device_name or '-')[:16]}"
    device = f"device={(device_name or '-')[:16]}"
    outcome = report.outcome.upper()[:7]
    session = f"  {session_id}" if session_id else ""
    return f"[appium] {worker:<6} {target:<24} {device:<24} {outcome:<7} {report.nodeid}{session}"
