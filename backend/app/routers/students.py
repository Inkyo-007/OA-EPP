from fastapi import APIRouter, Query, HTTPException
from app.database import db

router = APIRouter()


@router.get("/api/students/search")
def search_students(q: str = Query(..., min_length=1)):
    """按姓名或学号模糊搜索学生（适配远程 users + students 表）"""
    q = q.strip()
    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT u.id, u.full_name AS name, u.student_no AS student_id,
                   COALESCE(s.class_name, '') AS class_name
            FROM users u
            LEFT JOIN students s ON u.id = s.user_id
            WHERE u.role = 'student'
              AND (u.full_name LIKE %s OR u.student_no LIKE %s)
            ORDER BY u.full_name
            LIMIT 10
        """, (f"%{q}%", f"%{q}%"))
        rows = cur.fetchall()
    return rows
