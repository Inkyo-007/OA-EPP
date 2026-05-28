"""
sync_exams.py — 扫描 DOCS_DIR 下所有 Markdown 文件，自动维护 exam-meta 与数据库 exams 表的一致性。
由 app/main.py 在 FastAPI 启动时调用。

规则：
1. 发现含 <quiz> 的 .md 文件 → 若缺少 exam-meta div，自动注入
2. exam-id = 文件名去掉扩展名（如 chapter3 → chapter3）
3. 通过 source_ref 字段（如果 exams 表有）或 title 匹配来同步
4. 数据库中找不到对应 .md 的考试记录不删除（因为可能是其他来源创建的）
"""

import os
import re
from pathlib import Path
from app.database import db

DOCS_DIR = os.environ.get("DOCS_DIR", "")
COURSE_ID = int(os.environ.get("COURSE_ID", "2"))

_BAKED_DOCS_DIR = Path(__file__).parent.parent / "docs_baked"

_QUIZ_RE       = re.compile(r"<quiz\b",                              re.IGNORECASE)
_META_DIV_RE   = re.compile(r'<div[^>]+id=["\']exam-meta["\']',     re.IGNORECASE)
_META_ID_RE    = re.compile(r'data-exam-id=["\']([^"\']+)["\']',    re.IGNORECASE)
_META_TITLE_RE = re.compile(r'data-exam-title=["\']([^"\']+)["\']', re.IGNORECASE)
_HEADING_RE    = re.compile(r'^#\s+(.+)',                            re.MULTILINE)

INTRO_MARKER   = "<!-- mkdocs-quiz intro -->"
RESULTS_MARKER = "<!-- mkdocs-quiz results -->"


def _derive_title(content: str, exam_id: str) -> str:
    m = _HEADING_RE.search(content)
    if m:
        return re.sub(r"[*_`]", "", m.group(1)).strip() + " 测验"
    return f"{exam_id} 测验"


def _parse_exam_meta(content: str):
    if not _META_DIV_RE.search(content):
        return None
    mid    = _META_ID_RE.search(content)
    mtitle = _META_TITLE_RE.search(content)
    if mid:
        return mid.group(1), (mtitle.group(1) if mtitle else mid.group(1))
    return None


def _inject_exam_meta(content: str, exam_id: str, exam_title: str) -> str:
    meta_line = (
        f'<div id="exam-meta" data-exam-id="{exam_id}" '
        f'data-exam-title="{exam_title}" style="display:none"></div>'
    )
    if INTRO_MARKER in content:
        idx = content.index(INTRO_MARKER)
        return content[:idx] + meta_line + "\n\n" + content[idx:]
    m = _QUIZ_RE.search(content)
    if not m:
        return content
    insert_at   = m.start()
    new_content = content[:insert_at] + meta_line + "\n\n" + INTRO_MARKER + "\n\n" + content[insert_at:]
    if RESULTS_MARKER not in new_content:
        last = new_content.rfind("</quiz>")
        if last != -1:
            end         = last + len("</quiz>")
            new_content = new_content[:end] + "\n\n" + RESULTS_MARKER + new_content[end:]
    return new_content


def _find_docs_dir() -> "Path | None":
    if DOCS_DIR:
        candidate = Path(DOCS_DIR)
        if candidate.is_dir() and any(candidate.rglob("*.md")):
            return candidate
        if candidate.is_dir():
            print(f"[sync_exams] DOCS_DIR={DOCS_DIR!r} 目录为空或无 .md 文件，尝试内置备用目录")

    if _BAKED_DOCS_DIR.is_dir() and any(_BAKED_DOCS_DIR.rglob("*.md")):
        print(f"[sync_exams] 使用内置备用文档目录: {_BAKED_DOCS_DIR}")
        return _BAKED_DOCS_DIR

    return None


def sync_exams() -> dict:
    """扫描文档目录，自动修复 .md 文件并同步数据库。"""
    docs_dir = _find_docs_dir()
    if docs_dir is None:
        print(f"[sync_exams] 未找到有效文档目录（DOCS_DIR={DOCS_DIR!r}），跳过扫描")
        return {}

    found:    dict[str, str] = {}   # exam_slug → title
    injected: list[str]      = []

    for md_path in sorted(docs_dir.glob("**/*.md")):
        content = md_path.read_text(encoding="utf-8")
        if not _QUIZ_RE.search(content):
            continue
        meta = _parse_exam_meta(content)
        if meta:
            exam_slug, exam_title = meta
        else:
            exam_slug  = md_path.stem
            exam_title = _derive_title(content, exam_slug)
            md_path.write_text(_inject_exam_meta(content, exam_slug, exam_title), encoding="utf-8")
            injected.append(md_path.name)
            print(f"[sync_exams] 已注入 exam-meta → {md_path.name} (slug={exam_slug})")
        found[exam_slug] = exam_title

    added   = []
    updated = []
    with db() as conn:
        cur = conn.cursor()
        # 获取现有考试（按 title 模糊匹配 exam slug）
        cur.execute("SELECT id, title FROM exams WHERE course_id = %s", (COURSE_ID,))
        existing_exams = cur.fetchall()

        for slug, title in found.items():
            matched = None
            for ex in existing_exams:
                # 尝试匹配：title 包含 slug 或完全相同
                if slug.lower() in ex["title"].lower() or title == ex["title"]:
                    matched = ex
                    break
            if matched:
                if matched["title"] != title:
                    cur.execute("UPDATE exams SET title = %s WHERE id = %s", (title, matched["id"]))
                    updated.append(slug)
                    print(f"[sync_exams] 数据库更新考试标题：{matched['id']} → {title}")
            else:
                cur.execute(
                    "INSERT INTO exams (course_id, title, exam_type, start_at, end_at, created_by) "
                    "VALUES (%s, %s, 'quiz', NOW(), DATE_ADD(NOW(), INTERVAL 30 DAY), 14)",
                    (COURSE_ID, title)
                )
                added.append(slug)
                print(f"[sync_exams] 数据库新增考试：{slug} - {title}")

    print(f"[sync_exams] 完成：发现 {len(found)} 个考试，"
          f"注入 {len(injected)} 个文件，DB 新增 {len(added)}，更新 {len(updated)}")
    return {"injected_meta": injected, "db_added": added, "db_updated": updated,
            "exams": list(found.keys())}
