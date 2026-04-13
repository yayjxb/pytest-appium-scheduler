"""xdist scheduler integration for device-aware execution."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from xdist.scheduler.load import LoadScheduling

from .scheduler import CollectedItemStore, ScheduledItemStore

if TYPE_CHECKING:
    import pytest
    from xdist.remote import Producer
    from xdist.workermanage import WorkerController


def build_device_aware_scheduler(config: pytest.Config, log: Producer) -> LoadScheduling:
    runtime_config = config._appium_scheduler_runtime_config  # type: ignore[attr-defined]
    if runtime_config.mode == "all":
        return AllDeviceScheduling(config, log)
    return DistributedDeviceScheduling(config, log)


class _BaseDeviceScheduling(LoadScheduling):
    def __init__(self, config: pytest.Config, log: Producer | None = None) -> None:
        super().__init__(config, log)
        runtime_config = config._appium_scheduler_runtime_config  # type: ignore[attr-defined]
        state_dir = config._appium_scheduler_state_dir  # type: ignore[attr-defined]
        self._device_names = [device.name for device in runtime_config.devices]
        self._collected_items = CollectedItemStore(state_dir)
        self._item_devices: dict[str, tuple[str, ...]] = {}
        self._item_requires_device: dict[str, bool] = {}
        self._scheduled_items = ScheduledItemStore(state_dir)
        self._node_devices: dict[WorkerController, str | None] = {}
        self._device_usage = Counter[str]()

    def add_node(self, node: WorkerController) -> None:
        super().add_node(node)
        self._node_devices[node] = None

    def remove_node(self, node: WorkerController) -> str | None:
        self._node_devices.pop(node, None)
        return super().remove_node(node)

    def remove_pending_tests_from_node(self, node: WorkerController, indices) -> None:
        for index in indices:
            if index not in self.node2pending[node]:
                continue
            self.node2pending[node].remove(index)
            self._requeue_index(index, front=True)
        for other in self._ordered_nodes():
            self.check_schedule(other)

    def mark_test_pending(self, item: str) -> None:
        assert self.collection is not None
        self._requeue_index(self.collection.index(item), front=True)
        for node in self._ordered_nodes():
            self.check_schedule(node)

    def schedule(self) -> None:
        assert self.collection_is_completed

        if self.collection is not None:
            for node in self._ordered_nodes():
                self.check_schedule(node)
            return

        if not self._check_nodes_have_same_collection():
            self.log("**Different tests collected, aborting run**")
            return

        self.collection = next(iter(self.node2collection.values()))
        self.pending[:] = range(len(self.collection))
        self._item_devices = self._collected_items.read_item_devices()
        self._item_requires_device = self._collected_items.read_item_requires_device()
        self._scheduled_items.clear()
        if not self.collection:
            return

        if self.maxschedchunk is None:
            self.maxschedchunk = 1

        self._prepare()
        for node in self._ordered_nodes():
            self.check_schedule(node)

    def check_schedule(self, node: WorkerController, duration: float = 0) -> None:
        del duration
        if node.shutting_down:
            return
        while len(self.node2pending[node]) < 2:
            assignment = self._next_assignment(node)
            if assignment is None:
                unbound_index = self._pop_unbound_index()
                if unbound_index is not None:
                    self._send_test(node, unbound_index, None)
                    self.log("num items waiting for node:", len(self.pending))
                    continue
                if self.node2pending[node] == []:
                    self._node_devices[node] = None
                node.shutdown()
                return

            item_index, device_name = assignment
            if device_name is not None:
                self._device_usage[device_name] += 1
            self._send_test(node, item_index, device_name)
            self.log("num items waiting for node:", len(self.pending))

    def _prepare(self) -> None:
        raise NotImplementedError

    def _next_assignment(self, node: WorkerController) -> tuple[int, str | None] | None:
        raise NotImplementedError

    def _requeue_index(self, index: int, front: bool = False) -> None:
        if front:
            self.pending.insert(0, index)
            return
        self.pending.append(index)

    def _send_test(self, node: WorkerController, item_index: int, device_name: str | None) -> None:
        assert self.collection is not None
        nodeid = self.collection[item_index]
        if device_name is not None:
            self._scheduled_items.assign(nodeid, device_name)
        self.node2pending[node].append(item_index)
        if device_name is not None:
            self._node_devices[node] = device_name
        node.send_runtest_some([item_index])

    def _pop_unbound_index(self) -> int | None:
        assert self.collection is not None
        for position, item_index in enumerate(self.pending):
            nodeid = self.collection[item_index]
            if not self._item_requires_device.get(nodeid, False):
                return self.pending.pop(position)
            if self._item_devices.get(nodeid, ()) == ():
                return self.pending.pop(position)
        return None

    def _ordered_nodes(self) -> list[WorkerController]:
        return sorted(self.nodes, key=lambda node: _worker_sort_key(getattr(node.gateway, "id", "gw0")))

    def _available_device_names(self) -> list[str]:
        return [device_name for device_name in self._device_names if self._has_pending_for_device(device_name)]

    def _claimed_devices(self, *, exclude_node: WorkerController | None = None) -> set[str]:
        claimed = set()
        for node, device_name in self._node_devices.items():
            if node is exclude_node or device_name is None:
                continue
            if self.node2pending.get(node):
                claimed.add(device_name)
        return claimed

    def _has_idle_node(self, *, exclude_node: WorkerController | None = None) -> bool:
        for node in self.nodes:
            if node is exclude_node or node.shutting_down:
                continue
            if self.node2pending.get(node):
                continue
            return True
        return False

    def _pick_device_for_node(self, node: WorkerController) -> str | None:
        current = self._node_devices.get(node)
        available = self._available_device_names()
        if not available:
            return None

        claimed_by_others = self._claimed_devices(exclude_node=node)
        unclaimed = [name for name in available if name not in claimed_by_others]
        other_unclaimed = [name for name in unclaimed if name != current]
        if other_unclaimed:
            return min(other_unclaimed, key=self._device_priority_key)
        if current in available:
            return current
        if unclaimed:
            return min(unclaimed, key=self._device_priority_key)
        return None

    def _device_priority_key(self, device_name: str) -> tuple[int, int, str]:
        return (self._device_usage[device_name], self._device_names.index(device_name), device_name)

    def _has_pending_for_device(self, device_name: str) -> bool:
        raise NotImplementedError


class DistributedDeviceScheduling(_BaseDeviceScheduling):
    def _prepare(self) -> None:
        return None

    def _pick_device_for_node(self, node: WorkerController) -> str | None:
        current = self._node_devices.get(node)
        node_has_pending = bool(self.node2pending.get(node))

        if current is not None and node_has_pending and self._has_idle_node(exclude_node=node):
            if self._has_pending_for_device(current):
                return current
            return None
        return super()._pick_device_for_node(node)

    def _next_assignment(self, node: WorkerController) -> tuple[int, str | None] | None:
        device_name = self._pick_device_for_node(node)
        if device_name is None:
            return None

        item_index = _pop_best_index_for_device(
            pending=self.pending,
            collection=self.collection or [],
            item_devices=self._item_devices,
            item_requires_device=self._item_requires_device,
            device_name=device_name,
        )
        if item_index is None:
            self._node_devices[node] = None
            return None
        return (item_index, device_name)

    def _has_pending_for_device(self, device_name: str) -> bool:
        assert self.collection is not None
        for item_index in self.pending:
            nodeid = self.collection[item_index]
            if not self._item_requires_device.get(nodeid, False):
                continue
            if device_name in self._item_devices.get(nodeid, ()):
                return True
        return False


class AllDeviceScheduling(_BaseDeviceScheduling):
    def __init__(self, config: pytest.Config, log: Producer | None = None) -> None:
        super().__init__(config, log)
        self._device_queues: dict[str, list[int]] = {}

    def _prepare(self) -> None:
        self._device_queues = {device_name: [] for device_name in self._device_names}
        assert self.collection is not None
        for index in list(self.pending):
            nodeid = self.collection[index]
            if not self._item_requires_device.get(nodeid, False):
                continue
            eligible = self._item_devices.get(nodeid, ())
            if not eligible:
                continue
            self._device_queues.setdefault(eligible[0], []).append(index)

    def _next_assignment(self, node: WorkerController) -> tuple[int, str | None] | None:
        device_name = self._pick_device_for_node(node)
        if device_name is None:
            return None

        queue = self._device_queues.get(device_name, [])
        if not queue:
            self._node_devices[node] = None
            return None
        item_index = queue.pop(0)
        if item_index in self.pending:
            self.pending.remove(item_index)
        return (item_index, device_name)

    def _has_pending_for_device(self, device_name: str) -> bool:
        return bool(self._device_queues.get(device_name))

    def _requeue_index(self, index: int, front: bool = False) -> None:
        super()._requeue_index(index, front=front)
        assert self.collection is not None
        nodeid = self.collection[index]
        if not self._item_requires_device.get(nodeid, False):
            return
        eligible = self._item_devices.get(nodeid, ())
        if not eligible:
            return
        queue = self._device_queues.setdefault(eligible[0], [])
        if front:
            queue.insert(0, index)
            return
        queue.append(index)

    def _pick_device_for_node(self, node: WorkerController) -> str | None:
        current = self._node_devices.get(node)
        if current and self._has_pending_for_device(current):
            return current

        available = self._available_device_names()
        if not available:
            return None

        claimed_by_others = self._claimed_devices(exclude_node=node)
        unclaimed = [name for name in available if name not in claimed_by_others]
        if not unclaimed:
            return None
        return min(unclaimed, key=self._device_priority_key)


def _pop_best_index_for_device(
    *,
    pending: list[int],
    collection: list[str],
    item_devices: dict[str, tuple[str, ...]],
    item_requires_device: dict[str, bool],
    device_name: str,
) -> int | None:
    best_position: int | None = None
    best_rank: tuple[int, int] | None = None

    for position, item_index in enumerate(pending):
        nodeid = collection[item_index]
        if not item_requires_device.get(nodeid, False):
            continue
        eligible_devices = item_devices.get(nodeid, ())
        if device_name not in eligible_devices:
            continue
        rank = (len(eligible_devices), position)
        if best_rank is None or rank < best_rank:
            best_rank = rank
            best_position = position

    if best_position is None:
        return None
    return pending.pop(best_position)


def _worker_sort_key(worker_id: str) -> tuple[int, str]:
    digits = "".join(ch for ch in worker_id if ch.isdigit())
    return (int(digits) if digits else 0, worker_id)
