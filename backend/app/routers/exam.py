from fastapi import APIRouter, HTTPException, Header, Query
from typing import Optional
from pydantic import BaseModel
from app.database import db
from app.auth_utils import verify_student_token

router = APIRouter()

# 远程数据库常量
COURSE_ID = 2       # 嵌入式系统综合实践
TEACHER_ID = 14     # 教师 李明


class SubmitRequest(BaseModel):
    score: float
    total: float


def _get_user_id(conn, student_no: str) -> Optional[int]:
    """通过 student_no 获取 user_id"""
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM users WHERE role = 'student' AND student_no = %s",
        (student_no,)
    )
    row = cur.fetchone()
    return row["id"] if row else None


@router.post("/api/exam/submit")
def submit_score(req: SubmitRequest, authorization: Optional[str] = Header(None)):
    """提交成绩"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    token = authorization.removeprefix("Bearer ").strip()

    try:
        payload = verify_student_token(token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

    student_no = payload["student_id"]
    exam_id = payload["exam_id"]

    if req.score < 0 or req.total <= 0 or req.score > req.total:
        raise HTTPException(status_code=422, detail="成绩数据无效")

    with db() as conn:
        cur = conn.cursor()
        user_id = _get_user_id(conn, student_no)
        if not user_id:
            raise HTTPException(status_code=404, detail="学生不存在")

        # 检查是否已有成绩（按 exam_id 筛选）
        cur.execute("""
            SELECT gr.id FROM grading_records gr
            JOIN submissions sub ON gr.submission_id = sub.id
            JOIN assignments a ON sub.assignment_id = a.id
            WHERE sub.student_user_id = %s
              AND a.title LIKE CONCAT('exam_', %s, '%%')
            LIMIT 1
        """, (user_id, exam_id))
        if cur.fetchone():
            raise HTTPException(status_code=409, detail="您已经提交过成绩")

        # 为该考试创建 assignment（如果不存在）
        assignment_title = f"exam_{exam_id}"
        cur.execute(
            "SELECT id FROM assignments WHERE course_id = %s AND title = %s LIMIT 1",
            (COURSE_ID, assignment_title)
        )
        assignment = cur.fetchone()
        if not assignment:
            cur.execute(
                "INSERT INTO assignments (course_id, title, deadline, created_by) "
                "VALUES (%s, %s, DATE_ADD(NOW(), INTERVAL 7 DAY), %s)",
                (COURSE_ID, assignment_title, TEACHER_ID)
            )
            assignment_id = cur.lastrowid
        else:
            assignment_id = assignment["id"]

        # 创建 submission 记录
        cur.execute(
            "INSERT INTO submissions (assignment_id, student_user_id, version_no) VALUES (%s, %s, 1)",
            (assignment_id, user_id)
        )
        submission_id = cur.lastrowid

        # 创建 grading_record
        cur.execute(
            "INSERT INTO grading_records (submission_id, graded_by, exam_score, total_score) VALUES (%s, %s, %s, %s)",
            (submission_id, TEACHER_ID, req.score, req.total)
        )

    return {"ok": True, "student_id": student_no, "exam_id": exam_id,
            "score": req.score, "total": req.total}


@router.get("/api/scores")
def get_scores(student_id: str = Query(...)):
    """查询某学生所有考试成绩"""
    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT u.id AS user_id, u.full_name AS name, u.student_no AS student_id,
                   COALESCE(s.class_name, '') AS class_name
            FROM users u
            LEFT JOIN students s ON u.id = s.user_id
            WHERE u.role = 'student' AND u.student_no = %s
        """, (student_id,))
        student = cur.fetchone()
        if not student:
            raise HTTPException(status_code=404, detail="学号不存在")

        # 获取所有考试
        cur.execute("SELECT id, title, exam_type FROM exams WHERE course_id = %s ORDER BY id", (COURSE_ID,))
        exams = cur.fetchall()

        # 获取该学生所有成绩
        cur.execute("""
            SELECT a.title AS assignment_title, gr.exam_score AS score, gr.total_score AS total, gr.graded_at AS submitted_at
            FROM grading_records gr
            JOIN submissions sub ON gr.submission_id = sub.id
            JOIN assignments a ON sub.assignment_id = a.id
            WHERE sub.student_user_id = %s AND a.course_id = %s
            ORDER BY gr.graded_at DESC
        """, (student["user_id"], COURSE_ID))
        score_rows = cur.fetchall()

    # 构建 exam_id → score 映射
    scores_map = {}
    for sr in score_rows:
        title = sr["assignment_title"] or ""
        if title.startswith("exam_"):
            eid = title.replace("exam_", "")
            if eid not in scores_map:
                scores_map[eid] = sr

    result = []
    for exam in exams:
        eid = str(exam["id"])
        s = scores_map.get(eid)
        result.append({
            "exam_id": eid,
            "exam_title": exam["title"],
            "exam_type": exam["exam_type"],
            "score": float(s["score"]) if s else None,
            "total": float(s["total"]) if s else None,
            "submitted_at": s["submitted_at"].strftime("%Y-%m-%d %H:%M:%S") if s and s["submitted_at"] else None,
        })

    return {
        "student": {"name": student["name"], "student_id": student["student_id"], "class_name": student["class_name"]},
        "scores": result,
    }
