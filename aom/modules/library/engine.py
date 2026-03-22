from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List

from ...core.fingerprint import factor_fingerprint


class LibraryError(ValueError):
    pass


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # 1. 增加 timeout 到 30 秒，防止多线程写入时报 "database is locked"
    # 2. 增加 check_same_thread=False，允许在并发引擎的多线程中使用同一个连接
    conn = sqlite3.connect(str(db_path), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS factors (
            fingerprint TEXT PRIMARY KEY,
            expression TEXT NOT NULL,
            settings_json TEXT NOT NULL,
            status TEXT NOT NULL,
            metrics_json TEXT,
            created_at TEXT NOT NULL,
            last_submitted_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT,
            submission_id TEXT,
            status TEXT,
            result_json TEXT,
            updated_at TEXT
        )
        """
    )
    conn.commit()


def load_state(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise LibraryError("state file must be a JSON object")
    return data


def load_factors(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise LibraryError("factors file must be a JSON list")
    return data


def archive_from_state(conn: sqlite3.Connection, state: Dict[str, Any]) -> int:
    completed = state.get("completed", [])
    if not isinstance(completed, list):
        raise LibraryError("state.completed must be a list")

    run_id = state.get("run_id")
    inserted = 0
    for item in completed:
        fingerprint = item.get("fingerprint") or factor_fingerprint(item["expression"], item["settings"])
        inserted += _upsert_factor(
            conn,
            fingerprint=fingerprint,
            expression=item["expression"],
            settings=item["settings"],
            status=item.get("status", "completed"),
            metrics=item.get("result"),
            submitted_at=item.get("updated_at"),
        )
        _insert_submission(
            conn,
            run_id=run_id,
            submission_id=item.get("submission_id"),
            status=item.get("status"),
            result=item.get("result"),
        )
    conn.commit()
    return inserted


def archive_from_factors(conn: sqlite3.Connection, factors: Iterable[Dict[str, Any]]) -> int:
    inserted = 0
    for item in factors:
        fingerprint = item.get("fingerprint") or factor_fingerprint(item["expression"], item["settings"])
        inserted += _upsert_factor(
            conn,
            fingerprint=fingerprint,
            expression=item["expression"],
            settings=item["settings"],
            status="new",
            metrics=None,
            submitted_at=None,
        )
    conn.commit()
    return inserted


def load_fingerprints(conn: sqlite3.Connection) -> set[str]:
    cur = conn.cursor()
    rows = cur.execute("SELECT fingerprint FROM factors").fetchall()
    return {row[0] for row in rows}


def find_by_fingerprint(conn: sqlite3.Connection, fingerprint: str) -> bool:
    cur = conn.cursor()
    row = cur.execute("SELECT 1 FROM factors WHERE fingerprint = ?", (fingerprint,)).fetchone()
    return row is not None


def stats(conn: sqlite3.Connection) -> Dict[str, Any]:
    cur = conn.cursor()
    total = cur.execute("SELECT COUNT(*) FROM factors").fetchone()[0]
    completed = cur.execute("SELECT COUNT(*) FROM factors WHERE status = 'completed'").fetchone()[0]
    return {"total": total, "completed": completed}


def list_factors(conn: sqlite3.Connection, limit: int = 500, offset: int = 0) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    # 按照最后提交时间降序排列
    rows = cur.execute(
        "SELECT * FROM factors ORDER BY last_submitted_at DESC, created_at DESC LIMIT ? OFFSET ?", (limit, offset)
    ).fetchall()
    results = []
    for row in rows:
        item = dict(row)
        # 解析 JSON 字段
        item["settings"] = json.loads(item["settings_json"]) if item.get("settings_json") else {}
        item["metrics"] = json.loads(item["metrics_json"]) if item.get("metrics_json") else {}
        
        # 提取用于表格展示的平铺字段
        item["display_region"] = item["settings"].get("region", "-")
        item["display_universe"] = item["settings"].get("universe", "-")
        
        # 尝试提取 Sharpe 指标 (WorldQuant 核心指标)
        metrics = item["metrics"]
        if isinstance(metrics, dict):
            # 兼容不同层级的指标结构
            alpha_data = metrics.get("alpha", metrics)
            item["display_sharpe"] = alpha_data.get("sharpe", "-")
            item["display_fitness"] = alpha_data.get("fitness", "-")
        else:
            item["display_sharpe"] = "-"
            item["display_fitness"] = "-"
            
        results.append(item)
    return results


def delete_factor(conn: sqlite3.Connection, fingerprint: str) -> bool:
    cur = conn.cursor()
    cur.execute("DELETE FROM factors WHERE fingerprint = ?", (fingerprint,))
    conn.commit()
    return cur.rowcount > 0


def clear_library(conn: sqlite3.Connection) -> None:
    """危险操作：清空因子库所有记录"""
    cur = conn.cursor()
    cur.execute("DELETE FROM factors")
    conn.commit()


def _upsert_factor(
    conn: sqlite3.Connection,
    fingerprint: str,
    expression: str,
    settings: Dict[str, Any],
    status: str,
    metrics: Dict[str, Any] | None,
    submitted_at: str | None,
) -> int:
    cur = conn.cursor()
    now = _now()
    settings_json = json.dumps(settings, ensure_ascii=False)
    metrics_json = json.dumps(metrics, ensure_ascii=False) if metrics is not None else None

    cur.execute(
        """
        INSERT INTO factors (fingerprint, expression, settings_json, status, metrics_json, created_at, last_submitted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(fingerprint) DO UPDATE SET
            status=excluded.status,
            metrics_json=COALESCE(excluded.metrics_json, factors.metrics_json),
            last_submitted_at=COALESCE(excluded.last_submitted_at, factors.last_submitted_at)
        """,
        (fingerprint, expression, settings_json, status, metrics_json, now, submitted_at),
    )
    return 1


def _insert_submission(
    conn: sqlite3.Connection,
    run_id: str | None,
    submission_id: str | None,
    status: str | None,
    result: Dict[str, Any] | None,
) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO submissions (run_id, submission_id, status, result_json, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            run_id,
            submission_id,
            status,
            json.dumps(result, ensure_ascii=False) if result is not None else None,
            _now(),
        ),
    )


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
