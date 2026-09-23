from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.routes.b24hooks import router as b24_router
from app.routes.ui import router as ui_router
from app.routes.unsub import router as unsub_router


def create_app(session_factory=None, token_manager=None, settings=None) -> FastAPI:
    """Фабрика приложения: session_factory/token_manager/settings инъектятся (в тестах) или из конфига."""
    app = FastAPI(title="Outreacher")

    if settings is None:
        from app.config import get_settings

        settings = get_settings()
    if session_factory is None:
        from app.db import make_sessionmaker

        session_factory = make_sessionmaker(settings.database_url)
    if token_manager is None:
        from app.b24.tokenmanager import TokenManager

        token_manager = TokenManager(session_factory)

    app.state.session_factory = session_factory
    app.state.token_manager = token_manager
    app.state.settings = settings

    app.include_router(b24_router)
    app.include_router(unsub_router)
    app.include_router(ui_router)

    static_dir = Path(__file__).resolve().parent.parent.parent / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/health")
    async def health() -> dict:
        db_ok = False
        try:
            async with session_factory() as session:
                await session.execute(text("SELECT 1"))
            db_ok = True
        except Exception:
            db_ok = False
        return {"status": "ok", "db": db_ok}

    return app


app = create_app()
