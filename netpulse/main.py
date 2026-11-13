"""FastAPI application: REST API, Server-Sent Events stream and static dashboard."""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from contextlib import asynccontextmanager
from pathlib import Path

import psutil
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings, load_settings
from .monitor import Monitor
from .store import Store, window_start

STATIC_DIR = Path(__file__).parent / "static"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(settings: Settings | None = None, start_monitor: bool = True) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = Store(settings.db_path)
        monitor = Monitor(settings, store)
        app.state.store, app.state.monitor = store, monitor
        if start_monitor:
            await monitor.start()
        try:
            yield
        finally:
            await monitor.stop()
            store.close()

    app = FastAPI(title="NetPulse", version="1.0.0", lifespan=lifespan)
    targets = {t.id: t for t in settings.targets}

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/system")
    async def system():
        interfaces = []
        stats = psutil.net_if_stats()
        for name, addrs in psutil.net_if_addrs().items():
            st = stats.get(name)
            if not st or not st.isup or name.startswith(("lo", "utun", "awdl", "llw", "bridge", "anpi")):
                continue
            ipv4 = [a.address for a in addrs if a.family == socket.AF_INET]
            if ipv4:
                interfaces.append({"name": name, "ipv4": ipv4, "speed_mbps": st.speed or None, "mtu": st.mtu})
        return {"hostname": socket.gethostname(), "interfaces": interfaces}

    @app.get("/api/targets")
    async def list_targets(request: Request, minutes: float = Query(60, gt=0, le=1440)):
        monitor: Monitor = request.app.state.monitor
        store: Store = request.app.state.store
        since = window_start(minutes)
        out = []
        for t in settings.targets:
            latest = monitor.latest.get(t.id)
            out.append({
                "id": t.id,
                "name": t.name,
                "type": t.type,
                "endpoint": t.describe(),
                "interval": t.interval,
                "latency_warn_ms": t.latency_warn_ms,
                "latest": latest.to_dict() if latest else None,
                "stats": await asyncio.to_thread(store.stats, t.id, since),
            })
        return out

    @app.get("/api/targets/{target_id}/history")
    async def history(request: Request, target_id: str, minutes: float = Query(15, gt=0, le=1440)):
        if target_id not in targets:
            raise HTTPException(404, f"Unknown target {target_id!r}")
        store: Store = request.app.state.store
        return await asyncio.to_thread(store.history, target_id, window_start(minutes))

    @app.get("/api/bandwidth")
    async def bandwidth(request: Request):
        return list(request.app.state.monitor.bandwidth)

    @app.get("/api/stream")
    async def stream(request: Request):
        monitor: Monitor = request.app.state.monitor
        queue = monitor.subscribe()

        async def events():
            try:
                yield "retry: 3000\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event, data = await asyncio.wait_for(queue.get(), timeout=15)
                        yield f"event: {event}\ndata: {json.dumps(data)}\n\n"
                    except TimeoutError:
                        yield ": keep-alive\n\n"
            finally:
                monitor.unsubscribe(queue)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def run() -> None:
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="NetPulse network monitor")
    parser.add_argument("--config", default=None, help="Path to config.toml")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    uvicorn.run(create_app(load_settings(args.config)), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    run()
