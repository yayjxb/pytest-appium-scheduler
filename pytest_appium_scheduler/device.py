"""Device model helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Device:
    """Simple device abstraction."""

    name: str
    caps: dict[str, Any] = field(default_factory=dict)
    url: str = "http://127.0.0.1:4723"

    def normalized_url(self) -> str:
        if self.url.startswith(("http://", "https://")):
            return self.url
        return f"http://{self.url}"

    def capability(self, key: str, default: Any = None) -> Any:
        if key in self.caps:
            return self.caps.get(key, default)
        lowered = key.casefold()
        for existing_key, value in self.caps.items():
            if existing_key.casefold() == lowered:
                return value
        return default

