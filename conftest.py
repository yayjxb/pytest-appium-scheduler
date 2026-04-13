import random


def pytest_appium_create_driver(device):
    from appium import webdriver
    from appium.options.common.base import AppiumOptions

    options = AppiumOptions()
    options.load_capabilities(device.caps)
    driver = webdriver.Remote(command_executor=device.normalized_url(), options=options)
    driver.yay = random.choices("abcdefghijklmnopqrstuvwxyz", k=6).__str__()
    return driver


def pytest_appium_modify_caps(caps):
    caps["appPackage"] = "com.android.settings"
    caps["appActivity"] = ".Settings"
    caps["newCommandTimeout"] = 3000
    return caps
