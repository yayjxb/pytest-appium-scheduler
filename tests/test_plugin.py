from __future__ import annotations

from pathlib import Path

from pytest_appium_scheduler.config import AppiumSchedulerConfig
from pytest_appium_scheduler.device import Device
from pytest_appium_scheduler.hooks import AppiumSessionRuntime


pytest_plugins = ("pytester",)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _make_plugin_conftest(pytester, body: str) -> None:
    pytester.makeconftest(
        f"""
        import sys

        sys.path.insert(0, {str(PROJECT_ROOT)!r})

        {body}
        """
    )


class _DummyConfig:
    workerinput: dict[str, str]

    def __init__(self, worker_id: str) -> None:
        self.workerinput = {"workerid": worker_id}


def test_worker_affinity_rotates_initial_device_preference(tmp_path) -> None:
    runtime_config = AppiumSchedulerConfig(
        mode="distributed",
        driver_scope="function",
        retry_session=1,
        debug=False,
        config_path=None,
        devices=(
            Device(name="huawei_p60", caps={}, url="http://127.0.0.1:4723"),
            Device(name="iphone_14", caps={}, url="http://127.0.0.1:4725"),
        ),
        selected_device_names=(),
    )

    worker0 = AppiumSessionRuntime(_DummyConfig("gw0"), runtime_config, tmp_path / "gw0")
    worker1 = AppiumSessionRuntime(_DummyConfig("gw1"), runtime_config, tmp_path / "gw1")

    assert worker0._ordered_eligible_names(["huawei_p60", "iphone_14"]) == ["huawei_p60", "iphone_14"]
    assert worker1._ordered_eligible_names(["huawei_p60", "iphone_14"]) == ["iphone_14", "huawei_p60"]


def test_all_mode_runs_each_driver_test_on_each_matching_device(pytester) -> None:
    pytester.makefile(
        ".yaml",
        device="""
        devices:
          - name: pixel_7
            caps:
              platformName: Android
              deviceName: Pixel 7
            url: http://127.0.0.1:4723
          - name: iphone_14
            caps:
              platformName: iOS
              deviceName: iPhone 14
            url: http://127.0.0.1:4723
        """,
    )
    _make_plugin_conftest(
        pytester,
        """
        import itertools

        _counter = itertools.count(1)

        class FakeDriver:
            def __init__(self, device):
                self.device_name = device.name
                self.session_id = f"session-{next(_counter)}"

            def quit(self):
                return None

        def pytest_appium_create_driver(device):
            return FakeDriver(device)
        """,
    )
    pytester.makepyfile(
        """
        import pytest

        def test_runs_on_all_devices(driver):
            assert driver.device_name in {"pixel_7", "iphone_14"}

        @pytest.mark.device(platform="iOS")
        def test_runs_only_on_ios(driver):
            assert driver.device_name == "iphone_14"
        """
    )

    result = pytester.runpytest_subprocess(
        "-p",
        "no:appium_scheduler",
        "-p",
        "pytest_appium_scheduler.plugin",
        "--appium-config=device.yaml",
        "--appium-mode=all",
        "-q",
    )

    result.assert_outcomes(passed=3)


def test_unknown_device_marker_is_skipped(pytester) -> None:
    pytester.makefile(
        ".yaml",
        device="""
        devices:
          - name: pixel_7
            caps:
              platformName: Android
            url: http://127.0.0.1:4723
        """,
    )
    _make_plugin_conftest(
        pytester,
        """
        class FakeDriver:
            def __init__(self, device):
                self.device_name = device.name
                self.session_id = device.name

            def quit(self):
                return None

        def pytest_appium_create_driver(device):
            return FakeDriver(device)
        """,
    )
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.device("iphone_14")
        def test_unknown_device(driver):
            raise AssertionError("should not run")
        """
    )

    result = pytester.runpytest_subprocess(
        "-p",
        "no:appium_scheduler",
        "-p",
        "pytest_appium_scheduler.plugin",
        "--appium-config=device.yaml",
        "-q",
        "-ra",
    )

    result.assert_outcomes(skipped=1)
    result.stdout.fnmatch_lines(["*unknown device 'iphone_14'*"])


def test_distributed_mode_skips_unknown_device_marker_without_internal_error(pytester) -> None:
    pytester.makefile(
        ".yaml",
        device="""
        devices:
          - name: pixel_7
            caps:
              platformName: Android
            url: http://127.0.0.1:4723
          - name: huawei_p60
            caps:
              platformName: Android
            url: http://127.0.0.1:4725
        """,
    )
    _make_plugin_conftest(
        pytester,
        """
        class FakeDriver:
            def __init__(self, device):
                self.device_name = device.name
                self.session_id = device.name

            def quit(self):
                return None

        def pytest_appium_create_driver(device):
            return FakeDriver(device)
        """,
    )
    pytester.makepyfile(
        """
        import pytest

        def test_known(driver):
            assert driver.session_id

        @pytest.mark.device("iphone_14")
        def test_unknown_device(driver):
            raise AssertionError("should not run")
        """
    )

    result = pytester.runpytest_subprocess(
        "-p",
        "no:appium_scheduler",
        "-p",
        "pytest_appium_scheduler.plugin",
        "--appium-config=device.yaml",
        "--appium-mode=distributed",
        "-n",
        "2",
        "-q",
        "-ra",
    )

    result.assert_outcomes(passed=1, skipped=1)
    result.stdout.no_fnmatch_line("*INTERNALERROR*")


def test_all_mode_skips_unknown_device_marker_without_internal_error(pytester) -> None:
    pytester.makefile(
        ".yaml",
        device="""
        devices:
          - name: pixel_7
            caps:
              platformName: Android
            url: http://127.0.0.1:4723
          - name: huawei_p60
            caps:
              platformName: Android
            url: http://127.0.0.1:4725
        """,
    )
    _make_plugin_conftest(
        pytester,
        """
        class FakeDriver:
            def __init__(self, device):
                self.device_name = device.name
                self.session_id = device.name

            def quit(self):
                return None

        def pytest_appium_create_driver(device):
            return FakeDriver(device)
        """,
    )
    pytester.makepyfile(
        """
        import pytest

        def test_known(driver):
            assert driver.session_id

        @pytest.mark.device("iphone_14")
        def test_unknown_device(driver):
            raise AssertionError("should not run")
        """
    )

    result = pytester.runpytest_subprocess(
        "-p",
        "no:appium_scheduler",
        "-p",
        "pytest_appium_scheduler.plugin",
        "--appium-config=device.yaml",
        "--appium-mode=all",
        "-n",
        "2",
        "-q",
        "-ra",
    )

    result.assert_outcomes(passed=2, skipped=1)
    result.stdout.no_fnmatch_line("*INTERNALERROR*")


def test_debug_logs_show_worker_device_and_session(pytester) -> None:
    pytester.makefile(
        ".yaml",
        device="""
        devices:
          - name: pixel_7
            caps:
              platformName: Android
              deviceName: Pixel 7
            url: http://127.0.0.1:4723
        """,
    )
    _make_plugin_conftest(
        pytester,
        """
        class FakeDriver:
            def __init__(self, device):
                self.device_name = device.name
                self.session_id = "session-debug"

            def quit(self):
                return None

        def pytest_appium_create_driver(device):
            return FakeDriver(device)
        """,
    )
    pytester.makepyfile(
        """
        def test_debug_output(driver):
            assert driver.session_id == "session-debug"
        """
    )

    result = pytester.runpytest_subprocess(
        "-p",
        "no:appium_scheduler",
        "-p",
        "pytest_appium_scheduler.plugin",
        "--appium-config=device.yaml",
        "--appium-trace",
        "-q",
        "-s",
    )

    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(
        [
            "*action=device-acquire-attempt worker=master target=pixel_7*",
            "*action=device-acquire worker=master target=pixel_7 device=pixel_7*",
            "*action=driver-create-attempt worker=master target=pixel_7 device=pixel_7*",
            "*action=driver-create worker=master target=pixel_7 device=pixel_7 session=session-debug*",
            "*action=driver-quit worker=master target=pixel_7 device=pixel_7 session=session-debug*",
            "*action=device-release worker=master target=pixel_7 device=pixel_7*",
        ]
    )


def test_modify_caps_applies_before_custom_create_driver(pytester) -> None:
    pytester.makefile(
        ".yaml",
        device="""
        devices:
          - name: pixel_7
            caps:
              platformName: Android
              deviceName: Pixel 7
            url: http://127.0.0.1:4723
        """,
    )
    _make_plugin_conftest(
        pytester,
        """
        class FakeDriver:
            def __init__(self, device):
                self.device_name = device.name
                self.session_id = "session-modified"
                self.capabilities = dict(device.caps)

            def quit(self):
                return None

        def pytest_appium_modify_caps(caps):
            updated = dict(caps)
            updated["appPackage"] = "com.example.settings"
            updated["newCommandTimeout"] = 120
            return updated

        def pytest_appium_create_driver(device):
            assert device.caps["appPackage"] == "com.example.settings"
            assert device.caps["newCommandTimeout"] == 120
            return FakeDriver(device)
        """,
    )
    pytester.makepyfile(
        """
        def test_caps_are_modified_before_custom_driver(driver):
            assert driver.capabilities["appPackage"] == "com.example.settings"
            assert driver.capabilities["newCommandTimeout"] == 120
        """
    )

    result = pytester.runpytest_subprocess(
        "-p",
        "no:appium_scheduler",
        "-p",
        "pytest_appium_scheduler.plugin",
        "--appium-config=device.yaml",
        "-q",
    )

    result.assert_outcomes(passed=1)


def test_distributed_mode_spreads_unmarked_tests_across_devices(pytester) -> None:
    pytester.makefile(
        ".yaml",
        device="""
        devices:
          - name: huawei_p60
            caps:
              platformName: Android
            url: http://127.0.0.1:4723
          - name: pixel_7
            caps:
              platformName: Android
            url: http://127.0.0.1:4725
        """,
    )
    _make_plugin_conftest(
        pytester,
        """
        from pathlib import Path

        class FakeDriver:
            def __init__(self, device):
                self.device_name = device.name
                self.session_id = f"session-{device.name}"
                self.capabilities = device.caps

            def quit(self):
                return None

        def pytest_appium_create_driver(device):
            return FakeDriver(device)
        """,
    )
    pytester.makepyfile(
        """
        from pathlib import Path

        def _log(line):
            with Path("assignments.log").open("a", encoding="utf-8") as handle:
                handle.write(line + "\\n")

        def test_a(driver, device, worker_id):
            _log(f"{worker_id} {device.name} test_a")

        def test_b(driver, device, worker_id):
            _log(f"{worker_id} {device.name} test_b")

        def test_c(driver, device, worker_id):
            _log(f"{worker_id} {device.name} test_c")

        def test_d(driver, device, worker_id):
            _log(f"{worker_id} {device.name} test_d")
        """
    )

    result = pytester.runpytest_subprocess(
        "-p",
        "no:appium_scheduler",
        "-p",
        "pytest_appium_scheduler.plugin",
        "--appium-config=device.yaml",
        "--appium-mode=distributed",
        "-n",
        "2",
        "-q",
    )

    result.assert_outcomes(passed=4)
    assignments = (pytester.path / "assignments.log").read_text(encoding="utf-8").splitlines()
    assert any("huawei_p60" in line for line in assignments)
    assert any("pixel_7" in line for line in assignments)


def test_distributed_mode_idles_extra_workers_instead_of_sharing_devices(pytester) -> None:
    pytester.makefile(
        ".yaml",
        device="""
        devices:
          - name: huawei_p60
            caps:
              platformName: Android
            url: http://127.0.0.1:4723
          - name: pixel_7
            caps:
              platformName: Android
            url: http://127.0.0.1:4725
        """,
    )
    _make_plugin_conftest(
        pytester,
        """
        class FakeDriver:
            def __init__(self, device):
                self.device_name = device.name
                self.session_id = f"session-{device.name}"
                self.capabilities = device.caps

            def quit(self):
                return None

        def pytest_appium_create_driver(device):
            return FakeDriver(device)
        """,
    )
    pytester.makepyfile(
        """
        from pathlib import Path
        import time

        def _log(line):
            with Path("assignments.log").open("a", encoding="utf-8") as handle:
                handle.write(line + "\\n")

        def test_a(driver, device, worker_id):
            _log(f"{worker_id} {device.name} test_a")
            time.sleep(0.05)

        def test_b(driver, device, worker_id):
            _log(f"{worker_id} {device.name} test_b")
            time.sleep(0.05)

        def test_c(driver, device, worker_id):
            _log(f"{worker_id} {device.name} test_c")
            time.sleep(0.05)

        def test_d(driver, device, worker_id):
            _log(f"{worker_id} {device.name} test_d")
            time.sleep(0.05)
        """
    )

    result = pytester.runpytest_subprocess(
        "-p",
        "no:appium_scheduler",
        "-p",
        "pytest_appium_scheduler.plugin",
        "--appium-config=device.yaml",
        "--appium-mode=distributed",
        "-n",
        "4",
        "-q",
    )

    result.assert_outcomes(passed=4)
    assignments = (pytester.path / "assignments.log").read_text(encoding="utf-8").splitlines()
    workers = {line.split()[0] for line in assignments}
    assert workers <= {"gw0", "gw1"}
    assert any("huawei_p60" in line for line in assignments)
    assert any("pixel_7" in line for line in assignments)


def test_distributed_mode_uses_both_devices_before_reusing_one(pytester) -> None:
    pytester.makefile(
        ".yaml",
        device="""
        devices:
          - name: huawei_p60
            caps:
              platformName: Android
            url: http://127.0.0.1:4723
          - name: pixel_7
            caps:
              platformName: Android
            url: http://127.0.0.1:4725
        """,
    )
    _make_plugin_conftest(
        pytester,
        """
        class FakeDriver:
            def __init__(self, device):
                self.device_name = device.name
                self.session_id = f"session-{device.name}"
                self.capabilities = device.caps

            def quit(self):
                return None

        def pytest_appium_create_driver(device):
            return FakeDriver(device)
        """,
    )
    pytester.makepyfile(
        """
        from pathlib import Path
        import time

        def _log(line):
            with Path("start_order.log").open("a", encoding="utf-8") as handle:
                handle.write(line + "\\n")

        def test_a(driver, device, worker_id):
            _log(f"{worker_id} {device.name} test_a")
            time.sleep(0.1)

        def test_b(driver, device, worker_id):
            _log(f"{worker_id} {device.name} test_b")
            time.sleep(0.1)

        def test_c(driver, device, worker_id):
            _log(f"{worker_id} {device.name} test_c")
            time.sleep(0.1)

        def test_d(driver, device, worker_id):
            _log(f"{worker_id} {device.name} test_d")
            time.sleep(0.1)
        """
    )

    result = pytester.runpytest_subprocess(
        "-p",
        "no:appium_scheduler",
        "-p",
        "pytest_appium_scheduler.plugin",
        "--appium-config=device.yaml",
        "--appium-mode=distributed",
        "-n",
        "4",
        "-q",
    )

    result.assert_outcomes(passed=4)
    rows = (pytester.path / "start_order.log").read_text(encoding="utf-8").splitlines()
    first_two_devices = {row.split()[1] for row in rows[:2]}
    assert first_two_devices == {"huawei_p60", "pixel_7"}


def test_distributed_mode_runs_non_device_tests_on_idle_workers(pytester) -> None:
    pytester.makefile(
        ".yaml",
        device="""
        devices:
          - name: huawei_p60
            caps:
              platformName: Android
            url: http://127.0.0.1:4723
          - name: pixel_7
            caps:
              platformName: Android
            url: http://127.0.0.1:4725
        """,
    )
    _make_plugin_conftest(
        pytester,
        """
        class FakeDriver:
            def __init__(self, device):
                self.device_name = device.name
                self.session_id = f"session-{device.name}"
                self.capabilities = device.caps

            def quit(self):
                return None

        def pytest_appium_create_driver(device):
            return FakeDriver(device)
        """,
    )
    pytester.makepyfile(
        """
        from pathlib import Path
        import time

        def _log(path, line):
            with Path(path).open("a", encoding="utf-8") as handle:
                handle.write(line + "\\n")

        def test_device_a(driver, device, worker_id):
            _log("device_workers.log", f"{worker_id} {device.name} test_device_a")
            time.sleep(0.1)

        def test_device_b(driver, device, worker_id):
            _log("device_workers.log", f"{worker_id} {device.name} test_device_b")
            time.sleep(0.1)

        class TestNoDevice:
            def test_no_driver_no_device(self, worker_id):
                _log("generic_workers.log", f"{worker_id} test_no_driver_no_device")

            def test_no_driver_no_device_again(self, worker_id):
                _log("generic_workers.log", f"{worker_id} test_no_driver_no_device_again")
        """
    )

    result = pytester.runpytest_subprocess(
        "-p",
        "no:appium_scheduler",
        "-p",
        "pytest_appium_scheduler.plugin",
        "--appium-config=device.yaml",
        "--appium-mode=distributed",
        "-n",
        "4",
        "-q",
    )

    result.assert_outcomes(passed=4)
    device_rows = (pytester.path / "device_workers.log").read_text(encoding="utf-8").splitlines()
    generic_rows = (pytester.path / "generic_workers.log").read_text(encoding="utf-8").splitlines()
    device_workers = {row.split()[0] for row in device_rows}
    generic_workers = {row.split()[0] for row in generic_rows}
    assert generic_workers - device_workers


def test_distributed_mode_reuses_single_worker_across_all_devices(pytester) -> None:
    pytester.makefile(
        ".yaml",
        device="""
        devices:
          - name: huawei_p60
            caps:
              platformName: Android
            url: http://127.0.0.1:4723
          - name: pixel_7
            caps:
              platformName: Android
            url: http://127.0.0.1:4725
          - name: iphone_14
            caps:
              platformName: iOS
            url: http://127.0.0.1:4727
        """,
    )
    _make_plugin_conftest(
        pytester,
        """
        class FakeDriver:
            def __init__(self, device):
                self.device_name = device.name
                self.session_id = f"session-{device.name}"
                self.capabilities = device.caps

            def quit(self):
                return None

        def pytest_appium_create_driver(device):
            return FakeDriver(device)
        """,
    )
    pytester.makepyfile(
        """
        from pathlib import Path

        def _log(line):
            with Path("single_worker_distributed.log").open("a", encoding="utf-8") as handle:
                handle.write(line + "\\n")

        def test_a(driver, device, worker_id):
            _log(f"{worker_id} {device.name} test_a")

        def test_b(driver, device, worker_id):
            _log(f"{worker_id} {device.name} test_b")

        def test_c(driver, device, worker_id):
            _log(f"{worker_id} {device.name} test_c")

        def test_d(driver, device, worker_id):
            _log(f"{worker_id} {device.name} test_d")

        def test_e(driver, device, worker_id):
            _log(f"{worker_id} {device.name} test_e")

        def test_f(driver, device, worker_id):
            _log(f"{worker_id} {device.name} test_f")
        """
    )

    result = pytester.runpytest_subprocess(
        "-p",
        "no:appium_scheduler",
        "-p",
        "pytest_appium_scheduler.plugin",
        "--appium-config=device.yaml",
        "--appium-mode=distributed",
        "-n",
        "1",
        "-q",
    )

    result.assert_outcomes(passed=6)
    rows = (pytester.path / "single_worker_distributed.log").read_text(encoding="utf-8").splitlines()
    used_devices = {row.split()[1] for row in rows}
    assert used_devices == {"huawei_p60", "pixel_7", "iphone_14"}


def test_debug_reporting_shows_device_assignment_under_xdist(pytester) -> None:
    pytester.makefile(
        ".yaml",
        device="""
        devices:
          - name: huawei_p60
            caps:
              platformName: Android
            url: http://127.0.0.1:4723
          - name: pixel_7
            caps:
              platformName: Android
            url: http://127.0.0.1:4725
        """,
    )
    _make_plugin_conftest(
        pytester,
        """
        class FakeDriver:
            def __init__(self, device):
                self.device_name = device.name
                self.session_id = f"session-{device.name}"
                self.capabilities = device.caps

            def quit(self):
                return None

        def pytest_appium_create_driver(device):
            return FakeDriver(device)
        """,
    )
    pytester.makepyfile(
        """
        def test_a(driver):
            assert driver.session_id

        def test_b(driver):
            assert driver.session_id
        """
    )

    result = pytester.runpytest_subprocess(
        "-p",
        "no:appium_scheduler",
        "-p",
        "pytest_appium_scheduler.plugin",
        "--appium-config=device.yaml",
        "--appium-mode=distributed",
        "--appium-debug",
        "-n",
        "2",
        "-q",
        "-s",
        "-vv",
    )

    result.assert_outcomes(passed=2)
    stdout = "\n".join(result.stdout.lines)
    assert "[appium]" in stdout
    assert "target=" in stdout
    assert "device=" in stdout
    assert "test_debug_reporting_shows_device_assignment_under_xdist.py::test_a" in stdout
    assert "test_debug_reporting_shows_device_assignment_under_xdist.py::test_b" in stdout
    assert "gw" in stdout
    assert "session-" in stdout


def test_all_mode_does_not_assign_same_device_to_multiple_workers(pytester) -> None:
    pytester.makefile(
        ".yaml",
        device="""
        devices:
          - name: huawei_p60
            caps:
              platformName: Android
            url: http://127.0.0.1:4723
          - name: pixel_7
            caps:
              platformName: Android
            url: http://127.0.0.1:4725
        """,
    )
    _make_plugin_conftest(
        pytester,
        """
        class FakeDriver:
            def __init__(self, device):
                self.device_name = device.name
                self.session_id = f"session-{device.name}"
                self.capabilities = device.caps

            def quit(self):
                return None

        def pytest_appium_create_driver(device):
            return FakeDriver(device)
        """,
    )
    pytester.makepyfile(
        """
        from pathlib import Path

        def _log(line):
            with Path("all_mode.log").open("a", encoding="utf-8") as handle:
                handle.write(line + "\\n")

        def test_a(driver, device, worker_id):
            _log(f"{worker_id} {device.name} test_a")

        def test_b(driver, device, worker_id):
            _log(f"{worker_id} {device.name} test_b")
        """
    )

    result = pytester.runpytest_subprocess(
        "-p",
        "no:appium_scheduler",
        "-p",
        "pytest_appium_scheduler.plugin",
        "--appium-config=device.yaml",
        "--appium-mode=all",
        "-n",
        "4",
        "-q",
    )

    result.assert_outcomes(passed=4)
    rows = (pytester.path / "all_mode.log").read_text(encoding="utf-8").splitlines()
    device_workers: dict[str, set[str]] = {}
    for row in rows:
        worker_id, device_name, _ = row.split()
        device_workers.setdefault(device_name, set()).add(worker_id)
    assert len(device_workers["huawei_p60"]) == 1
    assert len(device_workers["pixel_7"]) == 1


def test_all_mode_single_worker_runs_multiple_devices_sequentially(pytester) -> None:
    pytester.makefile(
        ".yaml",
        device="""
        devices:
          - name: huawei_p60
            caps:
              platformName: Android
            url: http://127.0.0.1:4723
          - name: pixel_7
            caps:
              platformName: Android
            url: http://127.0.0.1:4725
        """,
    )
    _make_plugin_conftest(
        pytester,
        """
        class FakeDriver:
            def __init__(self, device):
                self.device_name = device.name
                self.session_id = f"session-{device.name}"
                self.capabilities = device.caps

            def quit(self):
                return None

        def pytest_appium_create_driver(device):
            return FakeDriver(device)
        """,
    )
    pytester.makepyfile(
        """
        from pathlib import Path

        def _log(line):
            with Path("single_worker_all.log").open("a", encoding="utf-8") as handle:
                handle.write(line + "\\n")

        def test_a(driver, device, worker_id):
            _log(f"{worker_id} {device.name} test_a")

        def test_b(driver, device, worker_id):
            _log(f"{worker_id} {device.name} test_b")
        """
    )

    result = pytester.runpytest_subprocess(
        "-p",
        "no:appium_scheduler",
        "-p",
        "pytest_appium_scheduler.plugin",
        "--appium-config=device.yaml",
        "--appium-mode=all",
        "-n",
        "1",
        "-q",
    )

    result.assert_outcomes(passed=4)
    rows = (pytester.path / "single_worker_all.log").read_text(encoding="utf-8").splitlines()
    used_devices = {row.split()[1] for row in rows}
    assert used_devices == {"huawei_p60", "pixel_7"}


def test_all_mode_runs_non_device_tests_on_idle_workers(pytester) -> None:
    pytester.makefile(
        ".yaml",
        device="""
        devices:
          - name: huawei_p60
            caps:
              platformName: Android
            url: http://127.0.0.1:4723
          - name: pixel_7
            caps:
              platformName: Android
            url: http://127.0.0.1:4725
        """,
    )
    _make_plugin_conftest(
        pytester,
        """
        class FakeDriver:
            def __init__(self, device):
                self.device_name = device.name
                self.session_id = f"session-{device.name}"
                self.capabilities = device.caps

            def quit(self):
                return None

        def pytest_appium_create_driver(device):
            return FakeDriver(device)
        """,
    )
    pytester.makepyfile(
        """
        from pathlib import Path
        import time

        def _log(path, line):
            with Path(path).open("a", encoding="utf-8") as handle:
                handle.write(line + "\\n")

        def test_device_a(driver, device, worker_id):
            _log("all_mode_device_workers.log", f"{worker_id} {device.name} test_device_a")
            time.sleep(0.05)

        def test_device_b(driver, device, worker_id):
            _log("all_mode_device_workers.log", f"{worker_id} {device.name} test_device_b")
            time.sleep(0.05)

        class TestNoDevice:
            def test_no_driver_no_device(self, worker_id):
                _log("all_mode_generic_workers.log", f"{worker_id} test_no_driver_no_device")

            def test_no_driver_no_device_again(self, worker_id):
                _log("all_mode_generic_workers.log", f"{worker_id} test_no_driver_no_device_again")
        """
    )

    result = pytester.runpytest_subprocess(
        "-p",
        "no:appium_scheduler",
        "-p",
        "pytest_appium_scheduler.plugin",
        "--appium-config=device.yaml",
        "--appium-mode=all",
        "-n",
        "4",
        "-q",
    )

    result.assert_outcomes(passed=6)
    device_rows = (pytester.path / "all_mode_device_workers.log").read_text(encoding="utf-8").splitlines()
    generic_rows = (pytester.path / "all_mode_generic_workers.log").read_text(encoding="utf-8").splitlines()
    device_workers = {row.split()[0] for row in device_rows}
    generic_workers = {row.split()[0] for row in generic_rows}
    assert generic_workers - device_workers


def test_temp_scheduler_state_is_cleaned_after_run(pytester) -> None:
    pytester.makefile(
        ".yaml",
        device="""
        devices:
          - name: pixel_7
            caps:
              platformName: Android
            url: http://127.0.0.1:4723
        """,
    )
    _make_plugin_conftest(
        pytester,
        """
        class FakeDriver:
            def __init__(self, device):
                self.device_name = device.name
                self.session_id = "session-cleanup"

            def quit(self):
                return None

        def pytest_appium_create_driver(device):
            return FakeDriver(device)
        """,
    )
    pytester.makepyfile(
        """
        def test_cleanup(driver):
            assert driver.session_id
        """
    )

    result = pytester.runpytest_subprocess(
        "-p",
        "no:appium_scheduler",
        "-p",
        "pytest_appium_scheduler.plugin",
        "--appium-config=device.yaml",
        "-q",
    )

    result.assert_outcomes(passed=1)
    assert list(pytester.path.glob("pytest-appium-scheduler-*")) == []


def test_session_driver_scope_reuses_driver_for_same_device(pytester) -> None:
    pytester.makefile(
        ".yaml",
        device="""
        devices:
          - name: pixel_7
            caps:
              platformName: Android
              deviceName: Pixel 7
            url: http://127.0.0.1:4723
        """,
    )
    _make_plugin_conftest(
        pytester,
        """
        import itertools

        _counter = itertools.count(1)

        class FakeDriver:
            def __init__(self, device):
                self.device_name = device.name
                self.session_id = f"session-{next(_counter)}"

            def quit(self):
                return None

        def pytest_appium_create_driver(device):
            return FakeDriver(device)
        """,
    )
    pytester.makepyfile(
        """
        seen = []

        def test_first(driver):
            seen.append(driver.session_id)

        def test_second(driver):
            seen.append(driver.session_id)

        def test_reused():
            assert seen == ["session-1", "session-1"]
        """
    )

    result = pytester.runpytest_subprocess(
        "-p",
        "no:appium_scheduler",
        "-p",
        "pytest_appium_scheduler.plugin",
        "--appium-config=device.yaml",
        "--appium-driver-scope=session",
        "-q",
    )

    result.assert_outcomes(passed=3)
