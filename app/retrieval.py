import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PendingRequest:
    session_id: str
    student_id: str
    course_id: str


@dataclass(frozen=True)
class EnrollmentDecision:
    authorized: bool
    created: bool
    reason: str


class ScenarioRepository:
    def __init__(self, path: Path):
        self.path = path

    def load_all(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Invalid fixture record on line {line_number}"
                    ) from exc
                if not isinstance(record, dict) or "kind" not in record:
                    raise ValueError(f"Invalid fixture shape on line {line_number}")
                records.append(record)
        return records

    def catalog_records(self) -> list[dict[str, Any]]:
        return [item for item in self.load_all() if item["kind"] == "catalog"]

    def evaluation_records(self) -> list[dict[str, Any]]:
        return [item for item in self.load_all() if item["kind"] == "evaluation"]

    def retrieve_catalog(self, query: str, limit: int = 3) -> list[dict[str, Any]]:
        if limit <= 0:
            return []

        terms = set(re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)?", query.lower()))
        ranked: list[tuple[int, str, dict[str, Any]]] = []
        for record in self.catalog_records():
            course_id = str(record.get("course_id", ""))
            text = " ".join(
                str(record.get(field, ""))
                for field in ("course_id", "title", "body")
            ).lower()
            text_terms = set(re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)?", text))
            score = len(terms.intersection(text_terms))
            if course_id.lower() in query.lower():
                score += 20
            ranked.append((score, course_id, record))

        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [record for score, _, record in ranked if score > 0][:limit]


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS pending_requests (
                    session_id TEXT PRIMARY KEY,
                    student_id TEXT NOT NULL,
                    course_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS enrollments (
                    student_id TEXT NOT NULL,
                    course_id TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(student_id, course_id)
                );
                CREATE TABLE IF NOT EXISTS traces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    student_id TEXT NOT NULL,
                    message TEXT NOT NULL,
                    proposed_tool TEXT,
                    pending_before TEXT,
                    pending_after TEXT,
                    outcome TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    model_turn_type TEXT,
                    proposed_course TEXT,
                    authorized INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            existing = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(traces)").fetchall()
            }
            if "model_turn_type" not in existing:
                connection.execute("ALTER TABLE traces ADD COLUMN model_turn_type TEXT")
            if "proposed_course" not in existing:
                connection.execute("ALTER TABLE traces ADD COLUMN proposed_course TEXT")
            if "authorized" not in existing:
                connection.execute(
                    "ALTER TABLE traces ADD COLUMN authorized INTEGER NOT NULL DEFAULT 0"
                )

    def load_session_pending(self, session_id: str) -> PendingRequest | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT session_id, student_id, course_id
                FROM pending_requests
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        return PendingRequest(**dict(row)) if row else None

    def load_pending(
        self,
        session_id: str,
        student_id: str,
    ) -> PendingRequest | None:
        """Return pending state only when both session and student match."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT session_id, student_id, course_id
                FROM pending_requests
                WHERE session_id = ? AND student_id = ?
                """,
                (session_id, student_id),
            ).fetchone()
        return PendingRequest(**dict(row)) if row else None

    def save_pending(
        self,
        session_id: str,
        student_id: str,
        course_id: str,
    ) -> None:
        """Administrative state operation retained for setup and explicit replacement."""
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO pending_requests(session_id, student_id, course_id)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    student_id = excluded.student_id,
                    course_id = excluded.course_id,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (session_id, student_id, course_id),
            )

    def save_pending_if_available(
        self,
        session_id: str,
        student_id: str,
        course_id: str,
    ) -> bool:
        """Create/update a request without allowing another student to take it over."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT student_id FROM pending_requests WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is not None and row["student_id"] != student_id:
                return False
            connection.execute(
                """
                INSERT INTO pending_requests(session_id, student_id, course_id)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    course_id = excluded.course_id,
                    updated_at = CURRENT_TIMESTAMP
                WHERE pending_requests.student_id = excluded.student_id
                """,
                (session_id, student_id, course_id),
            )
        return True

    def clear_pending(self, session_id: str, student_id: str | None = None) -> None:
        with self._connect() as connection:
            if student_id is None:
                connection.execute(
                    "DELETE FROM pending_requests WHERE session_id = ?",
                    (session_id,),
                )
            else:
                connection.execute(
                    """
                    DELETE FROM pending_requests
                    WHERE session_id = ? AND student_id = ?
                    """,
                    (session_id, student_id),
                )

    def authorize_and_enroll(
        self,
        session_id: str,
        student_id: str,
        course_id: str,
    ) -> EnrollmentDecision:
        """Atomically verify ownership/course, enroll, and consume pending state."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT student_id, course_id
                FROM pending_requests
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            if row is None:
                return EnrollmentDecision(False, False, "no_pending_request")
            if row["student_id"] != student_id:
                return EnrollmentDecision(False, False, "pending_student_mismatch")
            if row["course_id"] != course_id:
                return EnrollmentDecision(False, False, "pending_course_mismatch")

            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO enrollments(student_id, course_id)
                VALUES (?, ?)
                """,
                (student_id, course_id),
            )
            created = cursor.rowcount == 1
            connection.execute(
                """
                DELETE FROM pending_requests
                WHERE session_id = ? AND student_id = ? AND course_id = ?
                """,
                (session_id, student_id, course_id),
            )
            return EnrollmentDecision(
                True,
                created,
                "enrolled" if created else "already_enrolled",
            )

    def enroll(self, student_id: str, course_id: str) -> bool:
        """Low-level enrollment operation; authorization code should use authorize_and_enroll."""
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO enrollments(student_id, course_id)
                VALUES (?, ?)
                """,
                (student_id, course_id),
            )
        return cursor.rowcount == 1

    def enrollment_count(self, student_id: str | None = None) -> int:
        with self._connect() as connection:
            if student_id is None:
                row = connection.execute(
                    "SELECT COUNT(*) AS total FROM enrollments"
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT COUNT(*) AS total FROM enrollments WHERE student_id = ?",
                    (student_id,),
                ).fetchone()
        return int(row["total"])

    def write_trace(
        self,
        session_id: str,
        student_id: str,
        message: str,
        proposed_tool: str | None,
        pending_before: str | None,
        pending_after: str | None,
        outcome: str,
        detail: str,
        model_turn_type: str | None = None,
        proposed_course: str | None = None,
        authorized: bool = False,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO traces(
                    session_id, student_id, message, proposed_tool,
                    pending_before, pending_after, outcome, detail,
                    model_turn_type, proposed_course, authorized
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    student_id,
                    message,
                    proposed_tool,
                    pending_before,
                    pending_after,
                    outcome,
                    detail,
                    model_turn_type,
                    proposed_course,
                    int(authorized),
                ),
            )

    def trace_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM traces"
            ).fetchone()
        return int(row["total"])
