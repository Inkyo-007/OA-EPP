from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

# 自动加载 .env 文件（若存在）
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    load_dotenv(_env_path)
except ImportError:
    pass

from app.database import init_db
from app.sync_exams import sync_exams
from app.routers import students, auth, exam, teacher, notifications

app = FastAPI(title="嵌入式系统综合实践 - OA-EPP", docs_url="/api/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(students.router)
app.include_router(auth.router)
app.include_router(exam.router)
app.include_router(teacher.router)
app.include_router(notifications.router)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@app.get("/teacher")
def teacher_page():
    return FileResponse(os.path.join(STATIC_DIR, "teacher.html"))


@app.get("/score")
def score_page():
    return FileResponse(os.path.join(STATIC_DIR, "score.html"))


@app.on_event("startup")
def startup():
    init_db()
    sync_exams()