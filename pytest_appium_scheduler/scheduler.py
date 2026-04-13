"""Cross-process state shared by xdist workers."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from .device import Device
from .exceptions import DeviceNotFoundError

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


class DevicePool:
    """A simple file-backed device lease pool shared by xdist workers."""

    def __init__(self, state_dir: Path, devices: list[Device]) -> None:
        self.state_dir = state_dir
        self.devices_by_name = {device.name: device for device in devices}
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._store = _JsonFileState(
            lock_path=self.state_dir / "pool.lock",
            data_path=self.state_dir / "leases.json",
        )

    def acquire(
        self,
        eligible_names: list[str],
        lease_id: str,
        preferred_name: str | None = None,
        wait_timeout: float | None = None,
        poll_interval: float = 0.2,
    ) -> Device:
        if not eligible_names:
            raise DeviceNotFoundError("No eligible devices are available for this test.")

        start = time.monotonic()
        while True:
            device = self._try_acquire(eligible_names, lease_id=lease_id, preferred_name=preferred_name)
            if device is not None:
                return device
            if wait_timeout is not None and (time.monotonic() - start) >= wait_timeout:
                raise DeviceNotFoundError(
                    f"Timed out waiting for a device. Eligible devices: {', '.join(eligible_names)}"
                )
            time.sleep(poll_interval)

    def release(self, device_name: str, lease_id: str) -> None:
        with self._store.locked() as leases:
            if leases.get(device_name) == lease_id:
                leases.pop(device_name, None)

    def owner_of(self, device_name: str) -> str | None:
        with self._store.locked(read_only=True) as leases:
            return leases.get(device_name)

    def cleanup(self) -> None:
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def _try_acquire(
        self,
        eligible_names: list[str],
        lease_id: str,
        preferred_name: str | None,
    ) -> Device | None:
        with self._store.locked() as leases:
            if preferred_name and preferred_name in eligible_names:
                owner = leases.get(preferred_name)
                if owner in (None, lease_id):
                    leases[preferred_name] = lease_id
                    return self.devices_by_name[preferred_name]

            for device_name in eligible_names:
                owner = leases.get(device_name)
                if owner in (None, lease_id):
                    leases[device_name] = lease_id
                    return self.devices_by_name[device_name]
        return None


class ScheduledItemStore:
    """Persist controller-side device choices so workers can honor them."""

    def __init__(self, state_dir: Path) -> None:
        state_dir.mkdir(parents=True, exist_ok=True)
        self._store = _JsonFileState(
            lock_path=state_dir / "scheduled-items.lock",
            data_path=state_dir / "scheduled-items.json",
        )

    def assign(self, nodeid: str, device_name: str) -> None:
        with self._store.locked() as assignments:
            assignments[nodeid] = device_name

    def get(self, nodeid: str) -> str | None:
        with self._store.locked(read_only=True) as assignments:
            value = assignments.get(nodeid)
            if value is None:
                return None
            return str(value)

    def clear(self) -> None:
        with self._store.locked() as assignments:
            assignments.clear()


class CollectedItemStore:
    """Persist per-item eligible device lists from worker collection."""

    def __init__(self, state_dir: Path) -> None:
        state_dir.mkdir(parents=True, exist_ok=True)
        self._store = _JsonFileState(
            lock_path=state_dir / "collected-items.lock",
            data_path=state_dir / "collected-items.json",
        )

    def replace_all(
        self,
        item_devices: dict[str, tuple[str, ...]],
        item_requires_device: dict[str, bool],
    ) -> None:
        serialized = self._serialize(item_devices, item_requires_device)
        with self._store.locked() as assignments:
            assignments.clear()
            assignments.update(serialized)

    def merge(
        self,
        item_devices: dict[str, tuple[str, ...]],
        item_requires_device: dict[str, bool],
    ) -> None:
        serialized = self._serialize(item_devices, item_requires_device)
        with self._store.locked() as assignments:
            assignments.update(serialized)

    def read_item_devices(self) -> dict[str, tuple[str, ...]]:
        with self._store.locked(read_only=True) as assignments:
            result: dict[str, tuple[str, ...]] = {}
            for nodeid, entry in assignments.items():
                if not isinstance(nodeid, str):
                    continue
                if isinstance(entry, list):
                    result[nodeid] = tuple(str(device_name) for device_name in entry)
                    continue
                if not isinstance(entry, dict):
                    continue
                device_names = entry.get("devices")
                if not isinstance(device_names, list):
                    continue
                result[nodeid] = tuple(str(device_name) for device_name in device_names)
            return result

    def read_item_requires_device(self) -> dict[str, bool]:
        with self._store.locked(read_only=True) as assignments:
            result: dict[str, bool] = {}
            for nodeid, entry in assignments.items():
                if not isinstance(nodeid, str):
                    continue
                if isinstance(entry, list):
                    result[nodeid] = True
                    continue
                if not isinstance(entry, dict):
                    continue
                result[nodeid] = bool(entry.get("requires_device", True))
            return result

    def clear(self) -> None:
        with self._store.locked() as assignments:
            assignments.clear()

    def _serialize(
        self,
        item_devices: dict[str, tuple[str, ...]],
        item_requires_device: dict[str, bool],
    ) -> dict[str, dict[str, object]]:
        serialized: dict[str, dict[str, object]] = {}
        for nodeid, device_names in item_devices.items():
            serialized[nodeid] = {
                "devices": list(device_names),
                "requires_device": bool(item_requires_device.get(nodeid, False)),
            }
        return serialized


class _JsonFileState:
    def __init__(self, lock_path: Path, data_path: Path) -> None:
        self._lock_path = lock_path
        self._data_path = data_path
        self._data_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._data_path.exists():
            self._data_path.write_text("{}", encoding="utf-8")

    def locked(self, read_only: bool = False) -> _LockedJsonFile:
        return _LockedJsonFile(
            lock_path=self._lock_path,
            data_path=self._data_path,
            read_only=read_only,
        )


class _LockedJsonFile:
    def __init__(self, lock_path: Path, data_path: Path, read_only: bool) -> None:
        self._lock_path = lock_path
        self._data_path = data_path
        self._read_only = read_only
        self._lock_file = None
        self._data: dict[str, object] | None = None

    def __enter__(self) -> dict[str, object]:
        self._lock_path.touch(exist_ok=True)
        self._lock_file = self._lock_path.open("r+", encoding="utf-8")
        if fcntl is not None:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX)
        raw = self._data_path.read_text(encoding="utf-8").strip() or "{}"
        self._data = json.loads(raw)
        return self._data

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self._read_only and exc_type is None and self._data is not None:
            self._data_path.write_text(
                json.dumps(self._data, ensure_ascii=True, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        if self._lock_file is not None and fcntl is not None:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
        if self._lock_file is not None:
            self._lock_file.close()
