"""FastAPI 应用入口."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .db import init_engine
from .routers import (
    agent_api, auth, classrooms, logs, schedules, unlock, usbs,
)


def create_app() -> FastAPI:
    # 日志
    log_dir = os.environ.get("SEEWOF_LOG_DIR", "data/logs")
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s %(message)s",
    )

    app = FastAPI(
        title="Seewof Control Server",
        version="1.0.0",
        description="希沃教室电脑权限控制 - 管理端",
    )

    # 初始化 DB
    init_engine()

    # CORS (开发开放, 生产应收紧)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.environ.get("SEEWOF_CORS_ORIGINS", "*").split(","),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 路由
    app.include_router(auth.router)
    app.include_router(classrooms.router)
    app.include_router(schedules.router)
    app.include_router(unlock.router)
    app.include_router(usbs.router)
    app.include_router(logs.router)
    app.include_router(agent_api.router)

    # 健康检查
    @app.get("/api/v1/health")
    def health():
        return {"ok": True, "service": "seewof-server"}

    # 静态前端 (如果构建了)
    web_dir = Path(__file__).resolve().parent.parent / "web"
    if web_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(web_dir / "assets")), name="assets")

        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(str(web_dir / "index.html"))

        @app.get("/{path:path}", include_in_schema=False)
        def spa_fallback(path: str):
            # 简单 SPA fallback: 任何非 /api/* 都返回 index.html
            if path.startswith("api/"):
                return JSONResponse({"error": "not found"}, status_code=404)
            f = web_dir / path
            if f.exists() and f.is_file():
                return FileResponse(str(f))
            return FileResponse(str(web_dir / "index.html"))

    return app


app = create_app()
