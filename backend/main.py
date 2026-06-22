from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import init_db
from routes import upload, query, documents, openspec, checklist, battlemap
from auth import router as auth_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


import os

app = FastAPI(title="克里珀 - 大型活动保障知识库", version="1.0.0", lifespan=lifespan)

# CORS: 默认本地开发域名；生产用环境变量 QLIPOTH_CORS_ORIGINS(逗号分隔)指定白名单。
# allow_credentials=True 时不能用通配 "*"(浏览器会拒绝带凭据请求)，故用明确白名单。
_default_origins = "http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000,http://127.0.0.1:5173"
_origins = [o.strip() for o in os.environ.get("QLIPOTH_CORS_ORIGINS", _default_origins).split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload.router, prefix="/api")
app.include_router(query.router, prefix="/api")
app.include_router(documents.router, prefix="/api")
app.include_router(openspec.router, prefix="/api")
app.include_router(checklist.router, prefix="/api")
app.include_router(battlemap.router, prefix="/api")
app.include_router(auth_router, prefix="/api")


if __name__ == "__main__":
    import uvicorn
    from config import load_settings

    settings = load_settings()
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
        reload_dirs=[".", "routes"],
    )
