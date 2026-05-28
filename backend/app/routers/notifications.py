from fastapi import APIRouter, HTTPException, Header, Query
from typing import Optional
from pydantic import BaseModel
from app.database import db
from app.auth_utils import require_teacher, get_student_from_token

router = APIRouter()

# ──────────────── 通知分类常量 ────────────────
VALID_CATEGORIES = ("announcement", "deadline", "grade", "system", "graded")

# ──────────────── 辅助 ────────────────

def _get_unread_count(conn, user_id: int) -> int:
    """统计学生未读通知数（远程 notifications 表）"""
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) AS cnt FROM notifications WHERE user_id = %s AND is_read = 0",
        (user_id,)
    )
    return cur.fetchone()["cnt"]


def _datetime_to_str(d: dict, *keys):
    """将 dict 中的 datetime 字段转为字符串"""
    for k in keys:
        if d.get(k):
            d[k] = d[k].strftime("%Y-%m-%d %H:%M:%S")


# ──────────────── 模型 ────────────────

class NotificationCreate(BaseModel):
    title: str
    content: str = ""
    category: str = "announcement"


class NotificationUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    category: Optional[str] = None


# ──────────────── 教师端 API ────────────────

@router.post("/api/teacher/notifications")
def create_notification(req: NotificationCreate, authorization: Optional[str] = Header(None)):
    """创建通知（广播：给所有选课学生每人发一条）"""
    require_teacher(authorization)
    if req.category not in VALID_CATEGORIES:
        raise HTTPException(status_code=422,
                            detail=f"无效分类，可选值：{', '.join(VALID_CATEGORIES)}")

    with db() as conn:
        cur = conn.cursor()
        # 获取所有选课学生
        cur.execute("""
            SELECT e.student_user_id FROM enrollments e
            JOIN courses c ON e.course_id = c.id
            WHERE c.status = 'open'
        """)
        students = cur.fetchall()

        if not students:
            raise HTTPException(status_code=422, detail="没有选课学生，无法发送通知")

        # 批量插入通知
        notification_ids = []
        for s in students:
            cur.execute(
                "INSERT INTO notifications (user_id, title, body, category) VALUES (%s, %s, %s, %s)",
                (s["student_user_id"], req.title, req.content, req.category)
            )
            notification_ids.append(cur.lastrowid)

    return {"ok": True, "sent_count": len(notification_ids), "ids": notification_ids}


@router.put("/api/teacher/notifications/{nid}")
def update_notification(nid: int, req: NotificationUpdate, authorization: Optional[str] = Header(None)):
    require_teacher(authorization)

    updates = {}
    if req.title is not None:
        updates["title"] = req.title
    if req.content is not None:
        updates["body"] = req.content
    if req.category is not None:
        if req.category not in VALID_CATEGORIES:
            raise HTTPException(status_code=422, detail="无效分类")
        updates["category"] = req.category

    if not updates:
        raise HTTPException(status_code=422, detail="没有要更新的字段")

    with db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM notifications WHERE id = %s", (nid,))
        existing = cur.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="通知不存在")

        parts = [f"{k} = %s" for k in updates]
        values = list(updates.values())
        cur.execute(
            f"UPDATE notifications SET {', '.join(parts)} WHERE id = %s",
            (*values, nid)
        )

        cur.execute("SELECT * FROM notifications WHERE id = %s", (nid,))
        row = cur.fetchone()
    _datetime_to_str(row, "created_at")
    return row


@router.delete("/api/teacher/notifications/{nid}")
def delete_notification(nid: int, authorization: Optional[str] = Header(None)):
    require_teacher(authorization)
    with db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM notifications WHERE id = %s", (nid,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="通知不存在")
        cur.execute("DELETE FROM notifications WHERE id = %s", (nid,))
    return {"ok": True}


@router.get("/api/teacher/notifications")
def teacher_list_notifications(
    category: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    authorization: Optional[str] = Header(None)
):
    require_teacher(authorization)
    conditions = []
    params = []
    if category:
        conditions.append("category = %s")
        params.append(category)

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    with db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) AS cnt FROM notifications{where}", params)
        total = cur.fetchone()["cnt"]

        cur.execute(
            f"SELECT n.*, u.full_name AS student_name, u.student_no FROM notifications n "
            f"JOIN users u ON n.user_id = u.id{where} "
            f"ORDER BY n.created_at DESC LIMIT %s OFFSET %s",
            (*params, page_size, (page - 1) * page_size)
        )
        rows = cur.fetchall()

        # 统计已读/未读
        cur.execute("SELECT COUNT(*) AS cnt FROM notifications")
        total_count = cur.fetchone()["cnt"]

    result = []
    for r in rows:
        d = dict(r)
        _datetime_to_str(d, "created_at")
        d["read_count"] = 1 if d.get("is_read") else 0
        d["total_students"] = 1  # 一对一通知
        result.append(d)

    return {"items": result, "total": total, "page": page, "page_size": page_size}


# ──────────────── 学生端 API ────────────────

def _get_user_id_by_student_no(conn, student_no: str) -> Optional[int]:
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM users WHERE role = 'student' AND student_no = %s",
        (student_no,)
    )
    row = cur.fetchone()
    return row["id"] if row else None


@router.get("/api/notifications")
def student_list_notifications(
    category: Optional[str] = Query(None),
    unread_only: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    authorization: Optional[str] = Header(None)
):
    student = get_student_from_token(authorization)
    if not student:
        raise HTTPException(status_code=401, detail="请先登录")

    with db() as conn:
        user_id = _get_user_id_by_student_no(conn, student["student_id"])
        if not user_id:
            raise HTTPException(status_code=404, detail="学生不存在")

        conditions = ["n.user_id = %s"]
        params = [user_id]
        if category:
            conditions.append("n.category = %s")
            params.append(category)
        if unread_only:
            conditions.append("n.is_read = 0")

        where = " WHERE " + " AND ".join(conditions)

        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) AS cnt FROM notifications n{where}", params)
        total = cur.fetchone()["cnt"]

        cur.execute(
            f"SELECT * FROM notifications n{where} ORDER BY n.created_at DESC LIMIT %s OFFSET %s",
            (*params, page_size, (page - 1) * page_size)
        )
        rows = cur.fetchall()

        unread_count = _get_unread_count(conn, user_id)

    items = []
    for r in rows:
        d = dict(r)
        # 映射 body → content
        d["content"] = d.pop("body", "")
        d["is_read"] = bool(d.get("is_read", 0))
        _datetime_to_str(d, "created_at")
        items.append(d)

    return {"items": items, "total": total, "page": page, "page_size": page_size, "unread_count": unread_count}


@router.post("/api/notifications/{nid}/read")
def mark_notification_read(nid: int, authorization: Optional[str] = Header(None)):
    student = get_student_from_token(authorization)
    if not student:
        raise HTTPException(status_code=401, detail="请先登录")

    with db() as conn:
        user_id = _get_user_id_by_student_no(conn, student["student_id"])
        if not user_id:
            raise HTTPException(status_code=404, detail="学生不存在")

        cur = conn.cursor()
        cur.execute("SELECT id FROM notifications WHERE id = %s AND user_id = %s", (nid, user_id))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="通知不存在")

        cur.execute("UPDATE notifications SET is_read = 1 WHERE id = %s", (nid,))
        unread_count = _get_unread_count(conn, user_id)

    return {"ok": True, "unread_count": unread_count}


@router.post("/api/notifications/read-all")
def mark_all_read(authorization: Optional[str] = Header(None)):
    student = get_student_from_token(authorization)
    if not student:
        raise HTTPException(status_code=401, detail="请先登录")

    with db() as conn:
        user_id = _get_user_id_by_student_no(conn, student["student_id"])
        if not user_id:
            raise HTTPException(status_code=404, detail="学生不存在")

        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS cnt FROM notifications WHERE user_id = %s AND is_read = 0", (user_id,))
        unread_cnt = cur.fetchone()["cnt"]

        cur.execute("UPDATE notifications SET is_read = 1 WHERE user_id = %s AND is_read = 0", (user_id,))

    return {"ok": True, "marked_count": unread_cnt, "unread_count": 0}


@router.get("/api/notifications/unread-count")
def get_unread_count(authorization: Optional[str] = Header(None)):
    student = get_student_from_token(authorization)
    if not student:
        return {"unread_count": 0}

    with db() as conn:
        user_id = _get_user_id_by_student_no(conn, student["student_id"])
        if not user_id:
            return {"unread_count": 0}
        unread_count = _get_unread_count(conn, user_id)

    return {"unread_count": unread_count}
