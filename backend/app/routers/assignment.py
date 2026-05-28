"""
作业模块 - 截止规则功能
适配真实 MySQL 数据库表结构：
- assignments: id, course_id, chapter_id, title, description_md, allow_resubmit, late_policy, deadline, created_by, created_at
  late_policy 枚举: 'allow'(允许迟交), 'deny'(禁止迟交), 'penalty'(扣分迟交)
- submissions: id, assignment_id, student_user_id, version_no, file_url, text_content, is_late, grading_status, allow_resubmit_override, submitted_at
- courses: id, code, name, term, status
- enrollments: id, course_id, student_user_id, enrolled_at
- users: id, role, student_no, email, full_name
- students: user_id, class_name, phone
"""
from fastapi import APIRouter, HTTPException, Header, Query, UploadFile, File, Form
from typing import Optional
from pydantic import BaseModel
from datetime import datetime
from app.database import db
from app.auth_utils import verify_student_token
import os

router = APIRouter()

UPLOAD_BASE = os.environ.get("UPLOAD_DIR", "uploads/assignments")


# ---------- 请求/响应模型 ----------

class AssignmentCreate(BaseModel):
    """创建作业请求模型（教师端使用）"""
    course_id: int
    chapter_id: Optional[int] = None
    title: str
    description_md: str = ""
    deadline: datetime
    late_policy: str = "deny"  # allow | deny | penalty
    allow_resubmit: bool = True
    created_by: int


class AssignmentUpdate(BaseModel):
    """更新作业（含截止规则）"""
    title: Optional[str] = None
    description_md: Optional[str] = None
    deadline: Optional[datetime] = None
    late_policy: Optional[str] = None
    allow_resubmit: Optional[bool] = None


# ---------- 工具函数 ----------

def _get_student_user_id(conn, student_no: str) -> Optional[int]:
    """根据学号查询 users.id"""
    row = conn.execute(
        "SELECT id FROM users WHERE student_no = %s AND role = 'student'",
        (student_no,)
    ).fetchone()
    return row["id"] if row else None


def _classify_status(deadline: datetime, late_policy: str,
                     submission: Optional[dict], now: datetime) -> dict:
    """根据截止时间、迟交策略和提交记录，计算前端展示的状态"""
    is_past = now > deadline

    if submission:
        # 已有提交记录
        grading_status = submission.get("grading_status", "pending")
        is_late = bool(submission.get("is_late", 0))
        if grading_status == "graded":
            label = "已批改"
            code = "graded"
        elif is_late:
            label = "迟交"
            code = "late"
        else:
            label = "已提交"
            code = "submitted"
    else:
        # 无提交记录
        if is_past:
            if late_policy == "deny":
                label = "已截止"
                code = "closed"
            else:
                label = "可迟交"
                code = "late_allowed"
        else:
            label = "待提交"
            code = "pending"

    return {"status_label": label, "status_code": code}


# ---------- API 接口 ----------

@router.get("/api/assignments")
def list_assignments(
    student_no: str = Query(..., description="学号"),
    course_id: Optional[int] = Query(None, description="按课程筛选"),
    status: Optional[str] = Query(None, description="状态筛选: all/pending/submitted/graded/late/closed")
):
    """获取学生的作业列表，含截止状态与迟交标记"""
    with db() as conn:
        student_user_id = _get_student_user_id(conn, student_no)
        if not student_user_id:
            raise HTTPException(status_code=404, detail=f"学号 {student_no} 不存在")

        # 查询学生的全部作业，关联其个人提交记录（最高版本）
        sql = """
            SELECT
                a.id, a.course_id, a.chapter_id, a.title, a.description_md,
                a.deadline, a.late_policy, a.allow_resubmit, a.created_at,
                c.code AS course_code, c.name AS course_name, c.term AS course_term,
                s.id AS submission_id, s.version_no, s.file_url,
                s.is_late, s.grading_status, s.submitted_at
            FROM assignments a
            JOIN courses c ON c.id = a.course_id
            LEFT JOIN submissions s ON s.id = (
                SELECT id FROM submissions
                WHERE assignment_id = a.id AND student_user_id = %s
                ORDER BY version_no DESC LIMIT 1
            )
            WHERE a.course_id IN (
                SELECT course_id FROM enrollments WHERE student_user_id = %s
            )
        """
        params = [student_user_id, student_user_id]

        if course_id is not None:
            sql += " AND a.course_id = %s"
            params.append(course_id)

        sql += " ORDER BY a.deadline ASC"
        rows = conn.execute(sql, tuple(params)).fetchall()

        now = datetime.now()
        result = []
        for row in rows:
            submission = None
            if row.get("submission_id"):
                submission = {
                    "id": row["submission_id"],
                    "version_no": row["version_no"],
                    "file_url": row["file_url"],
                    "is_late": row["is_late"],
                    "grading_status": row["grading_status"],
                    "submitted_at": row["submitted_at"].isoformat() if row["submitted_at"] else None,
                }

            deadline = row["deadline"]
            cls = _classify_status(deadline, row["late_policy"], submission, now)

            time_left = (deadline - now).total_seconds()
            remaining_days = int(time_left // 86400) if time_left > 0 else int(time_left // 86400)

            item = {
                "id": row["id"],
                "title": row["title"],
                "course": {
                    "id": row["course_id"],
                    "code": row["course_code"],
                    "name": row["course_name"],
                    "term": row["course_term"],
                },
                "chapter_id": row["chapter_id"],
                "description_md": row["description_md"],
                "deadline": deadline.isoformat(),
                "late_policy": row["late_policy"],
                "allow_resubmit": bool(row["allow_resubmit"]),
                "remaining_days": remaining_days,
                "is_past_deadline": now > deadline,
                "status_label": cls["status_label"],
                "status_code": cls["status_code"],
                "submission": submission,
            }

            # 前端筛选
            if status and status != "all" and cls["status_code"] != status:
                continue
            result.append(item)

        return {"student_no": student_no, "assignments": result}


@router.get("/api/assignments/{assignment_id}")
def get_assignment_detail(assignment_id: int):
    """获取作业详情（含截止规则配置）"""
    with db() as conn:
        row = conn.execute(
            """SELECT a.*, c.code AS course_code, c.name AS course_name, c.term AS course_term
               FROM assignments a
               JOIN courses c ON c.id = a.course_id
               WHERE a.id = %s""",
            (assignment_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="作业不存在")

        now = datetime.now()
        return {
            "id": row["id"],
            "title": row["title"],
            "course": {
                "id": row["course_id"],
                "code": row["course_code"],
                "name": row["course_name"],
                "term": row["course_term"],
            },
            "chapter_id": row["chapter_id"],
            "description_md": row["description_md"],
            "deadline": row["deadline"].isoformat(),
            "late_policy": row["late_policy"],
            "allow_resubmit": bool(row["allow_resubmit"]),
            "is_past_deadline": now > row["deadline"],
            "created_by": row["created_by"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        }


@router.post("/api/assignments/submit")
async def submit_assignment(
    assignment_id: int = Form(...),
    student_no: str = Form(...),
    text_content: str = Form(""),
    file: Optional[UploadFile] = File(None),
):
    """
    提交作业 —— 核心截止规则逻辑：
    - deadline 之前：正常提交，is_late=0
    - deadline 之后：
        * late_policy='deny'    → 拒绝提交（HTTP 403）
        * late_policy='allow'   → 允许提交，is_late=1，状态标记为"迟交"
        * late_policy='penalty' → 允许提交，is_late=1，可由教师后续扣分
    """
    with db() as conn:
        # 查作业
        a = conn.execute(
            "SELECT id, deadline, late_policy, allow_resubmit FROM assignments WHERE id = %s",
            (assignment_id,)
        ).fetchone()
        if not a:
            raise HTTPException(status_code=404, detail="作业不存在")

        # 查学生
        student_user_id = _get_student_user_id(conn, student_no)
        if not student_user_id:
            raise HTTPException(status_code=404, detail=f"学号 {student_no} 不存在")

        # 是否已选修该作业所属课程
        enrolled = conn.execute(
            """SELECT 1 FROM enrollments e
               JOIN assignments a2 ON a2.course_id = e.course_id
               WHERE e.student_user_id = %s AND a2.id = %s""",
            (student_user_id, assignment_id)
        ).fetchone()
        if not enrolled:
            raise HTTPException(status_code=403, detail="您未选修该作业所在课程")

        now = datetime.now()
        deadline = a["deadline"]
        is_past = now > deadline
        late_policy = a["late_policy"]

        # ===== 截止规则核心判定 =====
        if is_past and late_policy == "deny":
            raise HTTPException(
                status_code=403,
                detail=f"作业已于 {deadline.strftime('%Y-%m-%d %H:%M')} 截止，按课程策略禁止提交"
            )

        is_late = 1 if is_past else 0

        # 查询当前最高版本号
        prev = conn.execute(
            "SELECT MAX(version_no) AS max_v, COUNT(*) AS cnt FROM submissions WHERE assignment_id = %s AND student_user_id = %s",
            (assignment_id, student_user_id)
        ).fetchone()
        prev_count = prev["cnt"] if prev else 0
        if prev_count > 0 and not a["allow_resubmit"]:
            raise HTTPException(status_code=409, detail="该作业不允许重新提交")
        version_no = (prev["max_v"] or 0) + 1

        # 保存文件
        file_url = None
        if file is not None and file.filename:
            upload_dir = os.path.join(UPLOAD_BASE, str(assignment_id))
            os.makedirs(upload_dir, exist_ok=True)
            safe_name = f"{student_no}_v{version_no}_{file.filename}"
            file_path = os.path.join(upload_dir, safe_name)
            content = await file.read()
            with open(file_path, "wb") as f:
                f.write(content)
            file_url = file_path.replace("\\", "/")

        # 写入提交记录
        conn.execute(
            """INSERT INTO submissions
               (assignment_id, student_user_id, version_no, file_url, text_content, is_late, grading_status, submitted_at)
               VALUES (%s, %s, %s, %s, %s, %s, 'pending', NOW())""",
            (assignment_id, student_user_id, version_no, file_url, text_content, is_late)
        )

        message_map = {
            ("normal", 0): "提交成功",
            ("normal", 1): "已迟交，按课程策略允许提交",
            ("penalty", 1): "已迟交，将按课程策略扣分",
        }
        if not is_late:
            message = "提交成功"
        elif late_policy == "penalty":
            message = "已迟交，将按课程策略扣分"
        else:
            message = "已迟交，按课程策略允许提交"

        return {
            "ok": True,
            "assignment_id": assignment_id,
            "student_no": student_no,
            "version_no": version_no,
            "is_late": bool(is_late),
            "late_policy": late_policy,
            "submitted_at": now.isoformat(),
            "message": message,
        }


@router.get("/api/assignments/{assignment_id}/submissions")
def list_submissions(assignment_id: int, student_no: str = Query(...)):
    """查询某学生在某作业上的所有提交版本"""
    with db() as conn:
        student_user_id = _get_student_user_id(conn, student_no)
        if not student_user_id:
            raise HTTPException(status_code=404, detail=f"学号 {student_no} 不存在")

        rows = conn.execute(
            """SELECT id, version_no, file_url, text_content, is_late, grading_status, submitted_at
               FROM submissions
               WHERE assignment_id = %s AND student_user_id = %s
               ORDER BY version_no DESC""",
            (assignment_id, student_user_id)
        ).fetchall()

        return {
            "assignment_id": assignment_id,
            "student_no": student_no,
            "versions": [
                {
                    "id": r["id"],
                    "version_no": r["version_no"],
                    "file_url": r["file_url"],
                    "text_content": r["text_content"],
                    "is_late": bool(r["is_late"]),
                    "grading_status": r["grading_status"],
                    "submitted_at": r["submitted_at"].isoformat() if r["submitted_at"] else None,
                }
                for r in rows
            ],
        }
