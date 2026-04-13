import pytest


def test_open_app(driver):
    assert driver.session_id


def test_device_fixture(device):
    assert device.name
    assert device.url


@pytest.mark.device(["emulator-5554"])
def test_only_oppo_findn3(driver):
    print(driver.session_id, driver.capabilities)
    assert driver.session_id


@pytest.mark.device(platform="iOS")
def test_only_ios(driver):
    assert driver.session_id


@pytest.mark.device(platform="Android")
def test_only_android(driver):
    assert driver.session_id


@pytest.mark.device(["emulator-5554"])
class TestMultipleDevices:
    def test_multiple_devices(self, driver):
        assert driver.session_id

    def test_multiple_devices_again(self, driver):
        assert driver.session_id


class TestNoDevice:

    def test_no_driver_no_device(self):
        pass

    def test_no_driver_no_device_again(self):
        pass

