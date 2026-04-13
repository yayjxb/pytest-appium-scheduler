"""Custom exceptions for pytest-appium-scheduler."""


class AppiumSchedulerError(Exception):
    """Base exception for the plugin."""


class DeviceNotFoundError(AppiumSchedulerError):
    """Raised when no matching device can be resolved."""


class InvalidConfigError(AppiumSchedulerError):
    """Raised when the plugin configuration is invalid."""


class DriverInitError(AppiumSchedulerError):
    """Raised when a driver cannot be created or recovered."""

