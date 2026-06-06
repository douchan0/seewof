"""SQLite + SQLAlchemy 封装."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


DEFAULT_DB_PATH = os.environ.get("SEEWOF_DB", "data/seewof.db")


def _ensure_dir(path: str) -> None:
    p = Path(path).parent
    p.mkdir(parents=True, exist_ok=True)


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal: sessionmaker | None = None


def init_engine(db_path: str = DEFAULT_DB_PATH) -> None:
    """初始化全局 engine. 只调用一次."""
    global _engine, _SessionLocal
    if db_path != ":memory:":
        _ensure_dir(db_path)
    _engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    # 第一次启动建表
    from . import models  # noqa: F401  注册 ORM
    Base.metadata.create_all(_engine)


def get_engine():
    if _engine is None:
        init_engine()
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    s = _SessionLocal() if _SessionLocal else None
    if s is None:
        init_engine()
        s = _SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db() -> Iterator[Session]:
    """FastAPI 依赖."""
    s = _SessionLocal() if _SessionLocal else None
    if s is None:
        init_engine()
        s = _SessionLocal()
    try:
        yield s
    finally:
        s.close()
