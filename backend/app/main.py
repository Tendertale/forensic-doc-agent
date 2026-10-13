"""应用装配（`docs/architecture.md` 第 1 节 / 第 9 节）。

`create_app(db_path=None, storage_root=None, seed_data=True) -> FastAPI`

* 数据库路径：参数 → 环境变量 `FORENSIC_DB_PATH` → `data/cases.db`；
* 存储根目录：参数 → 环境变量 `FORENSIC_STORAGE_ROOT` → `storage`；
* `app.state.db` / `app.state.storage` 供路由层取用；
* `seed_data=True` 且库里还没有案件时，写入 `SEED_CASE_COUNT` 个演示案件（TODO-DL-6）；
* CORS：允许 5173（Vite 开发服务器）与本机同源；
* 静态托管：`frontend/static` 始终挂到 `/static`；若 `frontend/dist/index.html` 存在，
  把 `frontend/dist` 挂到 `/`（html=True），否则 `GET /` 返回 `frontend/static/index.html`。

模块级 `app` 供 `uvicorn backend.app.main:app` 使用（`backend/scripts/serve.py` 则显式
调用 `create_app()`）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .constants import SEED_CASE_COUNT
from .database import Database
from .routes import install_error_handlers, router
from .storage import DEFAULT_STORAGE_ROOT, CaseStorage

__all__ = ["create_app", "DEFAULT_DB_PATH", "PROJECT_ROOT", "STATIC_DIR", "DIST_DIR"]

#: 项目根目录（本文件位于 `<root>/backend/app/main.py`）。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = PROJECT_ROOT / "frontend" / "static"
DIST_DIR = PROJECT_ROOT / "frontend" / "dist"

#: 默认数据库路径（可由 `FORENSIC_DB_PATH` 覆盖）。
DEFAULT_DB_PATH = "data/cases.db"

_FALLBACK_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>法医尸表检验文书整理 Agent</title></head>
<body style="font-family:system-ui;margin:40px">
<h1>法医尸表检验文书整理 Agent（后端已启动）</h1>
<p>未找到前端工作台文件。请确认 <code>frontend/static/index.html</code> 存在，
或执行 <code>npm install &amp;&amp; npm run build</code>（TODO-DL-3）后重试。</p>
<p>接口自检：<a href="/api/health">/api/health</a> ·
<a href="/api/config">/api/config</a></p>
</body></html>
"""


def create_app(db_path: Optional[str] = None, storage_root: Optional[str] = None,
               seed_data: bool = True) -> FastAPI:
    """建立 FastAPI 应用（含数据库初始化、可选演示数据、静态工作台挂载）。"""

    resolved_db = str(db_path or os.environ.get("FORENSIC_DB_PATH") or DEFAULT_DB_PATH)
    resolved_storage = str(storage_root or os.environ.get("FORENSIC_STORAGE_ROOT")
                           or DEFAULT_STORAGE_ROOT)

    app = FastAPI(
        title="法医尸表检验文书整理 Agent",
        description="导入委托书与记录本扫描件 → 识别 → 提取 → 整理 → 生成 → 校核 → 交付",
        version="1.0.0",
    )

    database = Database(resolved_db)
    database.initialize()
    storage = CaseStorage(resolved_storage)
    storage.ensure()
    database.storage = storage

    app.state.db = database
    app.state.storage = storage
    app.state.db_path = resolved_db
    app.state.storage_root = resolved_storage

    install_error_handlers(app)
    app.include_router(router)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173", "http://127.0.0.1:5173",
            "http://localhost:9765", "http://127.0.0.1:9765",
            "http://localhost:8000", "http://127.0.0.1:8000",
        ],
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 静态资源：无构建依赖工作台固定挂在 /static
    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    dist_index = DIST_DIR / "index.html"
    if dist_index.is_file():
        # 有构建产物时直接挂在根路径（TODO-DL-3 解锁后的路径）
        app.mount("/", StaticFiles(directory=str(DIST_DIR), html=True), name="dist")
    else:

        @app.get("/", include_in_schema=False)
        def workbench():
            index = STATIC_DIR / "index.html"
            if index.is_file():
                return FileResponse(str(index), media_type="text/html; charset=utf-8")
            return HTMLResponse(_FALLBACK_HTML, status_code=200)

    if seed_data:
        try:
            if not database.list_cases(page=1, page_size=1).get("total"):
                database.seed(SEED_CASE_COUNT)
        except Exception:  # noqa: BLE001 - 演示数据失败不应阻止服务启动
            pass

    return app


#: 供 `uvicorn backend.app.main:app` 直接使用：**惰性**构造，避免普通 import 的副作用。
def __getattr__(name: str):
    if name == "app":
        return create_app()
    raise AttributeError("module {0!r} has no attribute {1!r}".format(__name__, name))
