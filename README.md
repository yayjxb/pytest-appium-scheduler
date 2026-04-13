# pytest-appium-scheduler

`pytest-appium-scheduler` is a pytest plugin for Appium test execution with:

- device-aware scheduling
- `pytest-xdist` parallel execution
- `function` and `session` driver lifecycle control
- per-test device targeting via markers
- debug and trace logs for worker/device visibility

The package is published as `pytest-appium-scheduler`. After publishing, both of these forms work with `pip`:

```bash
pip install pytest-appium-scheduler
```

## Features

- `distributed` mode: spread Appium tests across available devices
- `all` mode: run each Appium test on every matching device
- `@pytest.mark.device(...)` and `@pytest.mark.devices(...)`
- shared device pool with worker-safe leasing
- `driver` and `device` fixtures
- extensible hooks:
  - `pytest_appium_create_driver`
  - `pytest_appium_modify_caps`
- compact debug output via `--appium-debug`
- verbose lifecycle tracing via `--appium-trace`

## Installation

```bash
pip install pytest_appium_scheduler
```

This installs:

- `pytest`
- `pytest-xdist`
- `PyYAML`
- `appium-python-client`

## Quick Start

Create a device config, for example from [`device.example.yaml`](./device.example.yaml):

```yaml
devices:
  - name: emulator-5554
    url: http://127.0.0.1:4723
    caps:
      platformName: Android
      automationName: UiAutomator2
      udid: emulator-5554
      systemPort: 8201
      appPackage: com.android.settings
      appActivity: .Settings

  - name: emulator-5556
    url: http://127.0.0.1:4723
    caps:
      platformName: Android
      automationName: UiAutomator2
      udid: emulator-5556
      systemPort: 8202
      appPackage: com.android.settings
      appActivity: .Settings
```

Write tests:

```python
import pytest


def test_open_app(driver):
    assert driver.session_id


def test_device_fixture(device):
    assert device.name


@pytest.mark.device("emulator-5556")
def test_only_on_specific_device(driver):
    assert driver.session_id


@pytest.mark.device(platform="Android")
def test_only_android(driver):
    assert driver.session_id


class TestNoDevice:
    def test_plain_pytest_case(self):
        assert True
```

Run in distributed mode:

```bash
pytest tests \
  --appium-config=device.yaml \
  --appium-mode=distributed \
  -n 2
```

Run in all mode:

```bash
pytest tests \
  --appium-config=device.yaml \
  --appium-mode=all \
  -n 2
```

## Scheduling Modes

### `distributed`

Use devices as a shared execution pool.

- Appium tests are scheduled onto matching devices
- when workers are more than devices, extra workers can stay idle
- non-Appium tests can still run on idle workers
- when workers are fewer than devices, workers will rotate across devices instead of permanently starving one

### `all`

Run every matching Appium test on every matching device.

- each device gets its own execution stream
- workers do not compete for the same device at the same time
- non-Appium tests can still run on idle workers

## Fixtures

### `driver`

Provides an Appium driver.

### `device`

Provides the selected device model:

```python
def test_device(device):
    assert device.name
    assert device.url
    assert device.caps
```

## Markers

Limit a test to one or more devices:

```python
@pytest.mark.device("pixel_7")
def test_only_pixel(driver):
    ...


@pytest.mark.devices(["pixel_7", "iphone_14"])
def test_specific_devices(driver):
    ...


@pytest.mark.device(platform="iOS")
def test_only_ios(driver):
    ...
```

Behavior:

- unmarked tests: all configured devices are eligible
- unknown device marker: skipped with warning
- unmatched filter: skipped with warning

## CLI Options

```bash
--appium-mode=distributed|all
--appium-config=PATH
--appium-device=NAME
--appium-driver-scope=function|session
--appium-retry-session=1
--appium-debug
--appium-trace
```

### Useful examples

Select one device only:

```bash
pytest tests \
  --appium-config=device.yaml \
  --appium-device=emulator-5556
```

Reuse one driver per device session:

```bash
pytest tests \
  --appium-config=device.yaml \
  --appium-driver-scope=session
```

Show compact scheduling logs:

```bash
pytest tests \
  --appium-config=device.yaml \
  --appium-mode=distributed \
  --appium-debug \
  -n 2 -q -s
```

Show verbose lifecycle trace:

```bash
pytest tests \
  --appium-config=device.yaml \
  --appium-mode=distributed \
  --appium-trace \
  -n 2 -q -s
```

## Custom Hooks

### Modify capabilities

```python
def pytest_appium_modify_caps(caps):
    updated = dict(caps)
    updated["newCommandTimeout"] = 120
    return updated
```

### Create your own driver

`pytest_appium_modify_caps` is applied before `pytest_appium_create_driver`, so your custom driver hook receives the updated capabilities.

```python
def pytest_appium_create_driver(device):
    from appium import webdriver
    from appium.options.common.base import AppiumOptions

    options = AppiumOptions()
    options.load_capabilities(device.caps)
    return webdriver.Remote(device.normalized_url(), options=options)
```

## Debug Output

With `--appium-debug`:

```text
[appium] gw0    target=emulator-5556        device=emulator-5556        PASSED  tests/test_real_device.py::test_open_app  session-123
```

With `--appium-trace`:

```text
[pytest-appium-scheduler] action=device-acquire worker=gw0 target=emulator-5556 device=emulator-5556 details=nodeid=...
```

## Notes

- For Android parallel execution, use distinct `udid` and `systemPort`
- if you provide your own `pytest_appium_create_driver`, the plugin still applies `pytest_appium_modify_caps` first
- if a test does not use `driver` or `device`, it can run on idle xdist workers without occupying a device

## Development

Create a local environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Run tests:

```bash
.venv/bin/python -m pytest tests/test_plugin.py -q
```

Build distributions:

```bash
.venv/bin/python -m pip install build
.venv/bin/python -m build
```

## License

MIT. See [LICENSE](./LICENSE).
