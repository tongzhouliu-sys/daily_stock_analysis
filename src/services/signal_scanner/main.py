from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.services.signal_scanner.app_base import (
    AppContext,
    register_exception_handlers,
    register_health_auth_routes,
    register_security_middleware,
)
from src.services.signal_scanner.app_routes import (
    register_admin_routes,
    register_batch_routes,
    register_market_routes,
    register_plan_routes,
)
from src.services.signal_scanner.core import ROOT, Settings, require
from src.services.signal_scanner.domain.calendar import Calendar
from src.services.signal_scanner.services.auth import Auth
from src.services.signal_scanner.services.config import initialize
from src.services.signal_scanner.storage.db import Database
from src.services.signal_scanner.storage.files import Artifacts


def create_app(settings=None, *, host_auth=None, api_prefix="/api"):
    settings = settings or Settings.load()
    db = Database(settings.db_path)
    db.migrate()
    files = Artifacts(settings.runtime / "artifacts")
    auth = host_auth or Auth(settings)
    cal = Calendar()
    with db.transaction() as cx:
        initialize(cx)
    app = FastAPI(title="交易信号扫描器", version="0.2.0", docs_url=None, redoc_url=None)
    app.state.db = db
    app.state.files = files
    app.state.auth = auth
    app.state.settings = settings
    ctx = AppContext(
        settings=settings,
        db=db,
        files=files,
        auth=auth,
        cal=cal,
        host_auth=host_auth,
        api_prefix=api_prefix,
    )
    register_exception_handlers(app)
    register_security_middleware(app, ctx)
    register_health_auth_routes(app, ctx)
    register_market_routes(app, ctx)
    register_plan_routes(app, ctx)
    register_admin_routes(app, ctx)
    register_batch_routes(app, ctx)

    from src.services.signal_scanner.api.rule_validation import router as rule_validation_router

    app.include_router(rule_validation_router(settings, api_prefix=api_prefix))

    dist = ROOT / "frontend" / "dist"
    if host_auth is None and dist.is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def frontend(path: str):
            require(not path.startswith(("api/", "health/")), "RESOURCE_NOT_FOUND", status=404)
            return FileResponse(dist / "index.html")

    return app
