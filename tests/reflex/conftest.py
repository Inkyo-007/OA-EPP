"""tests/reflex/conftest.py — pytest 公共 fixtures

所有 TDD 测试共享此文件中的 fixtures：
- mem_db: 每次测试使用独立 SQLite 内存数据库 Session
- REFLEX_DB_URL: 强制使用内存数据库，不依赖生产环境
"""
import os
import pytest
try:
    import sqlmodel  # type: ignore
except Exception:
    sqlmodel = None

# 强制使用 SQLite 内存数据库
os.environ.setdefault("REFLEX_DB_URL", "sqlite:///:memory:")


@pytest.fixture(scope="function")
def mem_db():
    """提供测试用的内存 DB session。

    若环境中未安装 `sqlmodel`，返回一个占位对象（None），以便依赖 DB 的测试
    可以在不真正访问数据库的情况下运行 TDD 检查。
    """
    if sqlmodel is None:
        yield None
        return

    engine = sqlmodel.create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    # 尝试初始化已导入模型的表结构（实现存在时生效）
    try:
        import oaepp.models  # noqa: F401
    except ImportError:
        pass
    sqlmodel.SQLModel.metadata.create_all(engine)
    with sqlmodel.Session(engine) as session:
        yield session
        session.rollback()
