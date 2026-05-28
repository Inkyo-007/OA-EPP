from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.database import db
from app.auth_utils import create_token

router = APIRouter()


class VerifyRequest(BaseModel):
    student_id: str
    exam_id: str


@router.post("/api/auth/verify")
def verify_identity(req: VerifyRequest):
    """
    核验学生身份并检查是否已提交成绩。
    适配远程 users + students + exams 表。
    """
    with db() as conn:
        cur = conn.cursor()
        # 通过 student_no 查找学生
        cur.execute("""
            SELECT u.id AS user_id, u.full_name AS name, u.student_no AS student_id,
                   COALESCE(s.class_name, '') AS class_name
            FROM users u
            LEFT JOIN students s ON u.id = s.user_id
            WHERE u.role = 'student' AND u.student_no = %s
        """, (req.student_id,))
        student = cur.fetchone()

        if not student:
            raise HTTPException(status_code=403, detail="学号不在名单中，请联系老师确认")

        # 查找考试（兼容远程库 exams 表可能为空的情况）
        cur.execute(
            "SELECT id, title, exam_type FROM exams WHERE id = %s",
            (req.exam_id,)
        )
        exam = cur.fetchone()

        if not exam:
            # 如果考试不存在，仍然允许验证身份（返回 token），
            # 实际考试状态由前端根据列表接口判断
            pass

        # 检查是否已提交成绩（通过 submissions + grading_records）
        cur.execute("""
            SELECT gr.exam_score AS score, gr.total_score AS total, gr.graded_at AS submitted_at
            FROM submissions sub
            JOIN grading_records gr ON sub.id = gr.submission_id
            WHERE sub.student_user_id = %s
            ORDER BY gr.graded_at DESC
            LIMIT 1
        """, (student["user_id"],))
        existing = cur.fetchone()

        if existing:
            return {
                "already_submitted": True,
                "name": student["name"],
                "score": float(existing["score"]) if existing["score"] else 0,
                "total": float(existing["total"]) if existing["total"] else 0,
                "submitted_at": existing["submitted_at"].strftime("%Y-%m-%d %H:%M:%S") if existing["submitted_at"] else None,
            }

        token = create_token({
            "role": "student",
            "student_id": student["student_id"],
            "name": student["name"],
            "exam_id": req.exam_id,
        }, expires_hours=2)

        return {
            "already_submitted": False,
            "name": student["name"],
            "token": token,
        }
