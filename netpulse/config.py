"""Configuration loading (TOML, stdlib only)."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CHECK_TYPES = {"ping", "tcp", "dns", "http"}


@dataclass(frozen=True)
class Target:
    id: str
    name: str
    type: str
    host: str
    port: int | None = None
    url: str | None = None
    interval: float = 5.0
    timeout: float = 2.0
    latency_warn_ms: float = 150.0

    def describe(self) -> str:
        if self.type == "http":
            return self.url or self.host
        if self.type == "tcp":
            return f"{self.host}:{self.port}"
        return self.host


@dataclass
class Settings:
    db_path: str = "netpulse.db"
    retention_hours: float = 24.0
    bandwidth_interval: float = 1.0
    targets: list[Target] = field(default_factory=list)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "target"


def parse_settings(data: dict) -> Settings:
    general = data.get("general", {})
    defaults = data.get("defaults", {})
    targets: list[Target] = []
    seen: set[str] = set()

    for raw in data.get("targets", []):
        ctype = raw.get("type", "ping")
        if ctype not in CHECK_TYPES:
            raise ValueError(f"Unknown check type {ctype!r} (expected one of {sorted(CHECK_TYPES)})")
        name = raw.get("name") or raw.get("host") or raw.get("url")
        if not name:
            raise ValueError("Every target needs at least a name, host or url")
        host = raw.get("host", "")
        url = raw.get("url")
        if ctype == "http" and not url:
            raise ValueError(f"HTTP target {name!r} needs a url")
        if ctype == "tcp" and not raw.get("port"):
            raise ValueError(f"TCP target {name!r} needs a port")
        if ctype != "http" and not host:
            raise ValueError(f"Target {name!r} needs a host")

        tid = raw.get("id") or _slug(name)
        base, n = tid, 2
        while tid in seen:
            tid, n = f"{base}-{n}", n + 1
        seen.add(tid)

        targets.append(
            Target(
                id=tid,
                name=name,
                type=ctype,
                host=host,
                port=raw.get("port"),
                url=url,
                interval=float(raw.get("interval", defaults.get("interval", 5))),
                timeout=float(raw.get("timeout", defaults.get("timeout", 2))),
                latency_warn_ms=float(raw.get("latency_warn_ms", defaults.get("latency_warn_ms", 150))),
            )
        )

    return Settings(
        db_path=general.get("db_path", "netpulse.db"),
        retention_hours=float(general.get("retention_hours", 24)),
        bandwidth_interval=float(general.get("bandwidth_interval", 1)),
        targets=targets,
    )


def load_settings(path: str | os.PathLike | None = None) -> Settings:
    path = Path(path or os.environ.get("NETPULSE_CONFIG", "config.toml"))
    with path.open("rb") as fh:
        settings = parse_settings(tomllib.load(fh))
    if env_db := os.environ.get("NETPULSE_DB"):
        settings.db_path = env_db
    return settings
