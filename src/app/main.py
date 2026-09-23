from fastapi import FastAPI

from sqlalchemy import text

from app.routes.b24hooks import router as b24_router
from app.routes.unsub import router as unsub_router


def create_app(session_factory=None, token_manager=None, settings=None) -> FastAPI:
    app = FastAPI(title="Outreacher")
    app.state.session_factory = session_factory
    app.state.token_manager = token_manager
    app.state.settings = settings

    app.include_router(b24_router)
    app.include_router(unsub_router)

    @app.get("/health")
    async def health() -> dict:
        db_ok = False
        if app.state.session_factory is not None:
            try:
                async with app.state.session_factory() as session:
                    await session.execute(text("SELECT 1"))
                db_ok = True
            except Exception:
                db_ok = False
        return {"status": "ok", "db": db_ok}

    return app


app = create_app()
