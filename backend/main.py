from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import init_db
from routes import upload, query, documents

app = FastAPI(title="Qlipoth", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload.router, prefix="/api")
app.include_router(query.router, prefix="/api")
app.include_router(documents.router, prefix="/api")


@app.on_event("startup")
async def startup():
    await init_db()


if __name__ == "__main__":
    import uvicorn
    from config import load_settings

    settings = load_settings()
    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=True)
