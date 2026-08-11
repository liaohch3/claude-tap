"""Resolve Hermes model requests to local session lineage metadata.

Hermes stores the conversation lineage in ``state.db``.  A model request does
not carry the Hermes session id, so the capture side has to correlate the
first user message with the rows in that database.  This module deliberately
keeps the correlation best-effort: a root can be retained when all matching
rows share it, while an ambiguous leaf is never guessed silently.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

HermesResolution = Literal["exact", "root_only", "ambiguous", "unresolved"]


@dataclass(frozen=True, slots=True)
class HermesSessionMatch:
    """The Hermes session identity inferred for one captured request.

    ``root_session_id`` is the value kept in the historical
    ``hermes_session_id`` capture field.  ``leaf_session_id`` is the concrete
    session containing the matching user message; ``parent_session_id`` and
    ``source`` describe that leaf.  The latter three are ``None`` when the
    candidate set cannot identify a unique leaf.
    """

    root_session_id: str | None = None
    leaf_session_id: str | None = None
    parent_session_id: str | None = None
    source: str | None = None
    root_turn: int | None = None
    resolution: HermesResolution = "unresolved"

    @property
    def hermes_session_id(self) -> str | None:
        """Backward-compatible alias for the root id."""

        return self.root_session_id

    def as_dict(self) -> dict[str, str | int | None]:
        """Return the stable capture-field names used by :class:`TraceWriter`."""

        return {
            "hermes_session_id": self.root_session_id,
            "hermes_root_session_id": self.root_session_id,
            "hermes_leaf_session_id": self.leaf_session_id,
            "hermes_parent_session_id": self.parent_session_id,
            "hermes_session_source": self.source,
            "hermes_root_turn": self.root_turn,
            "hermes_session_resolution": self.resolution,
            "root": self.root_session_id,
            "leaf": self.leaf_session_id,
            "parent": self.parent_session_id,
        }

    def __getitem__(self, key: str) -> str | int | None:
        """Allow callers/tests that prefer mapping-style access.

        The resolver historically returned a scalar.  Mapping-style access is
        a small compatibility convenience for integrations that model the new
        result as a JSON object.
        """

        values = {
            "hermes_session_id": self.root_session_id,
            "hermes_root_session_id": self.root_session_id,
            "hermes_leaf_session_id": self.leaf_session_id,
            "hermes_parent_session_id": self.parent_session_id,
            "hermes_session_source": self.source,
            "hermes_session_resolution": self.resolution,
            "root_session_id": self.root_session_id,
            "leaf_session_id": self.leaf_session_id,
            "parent_session_id": self.parent_session_id,
            "source": self.source,
            "root_turn": self.root_turn,
            "resolution": self.resolution,
        }
        try:
            return values[key]
        except KeyError:
            raise KeyError(key) from None


# Names used by early downstream experiments are kept as aliases.  The
# canonical public name is HermesSessionMatch.
HermesSessionResolution = HermesSessionMatch
HermesSessionIdentity = HermesSessionMatch


@dataclass(frozen=True, slots=True)
class _SessionCandidate:
    session_id: str
    parent_session_id: str | None
    source: str | None
    message_timestamp: float | None
    started_at: float | None
    ended_at: float | None
    message_count: int | None
    root_session_id: str
    root_user_timestamps: tuple[float, ...]


def hermes_request_first_user(record: dict[str, Any]) -> str:
    request = record.get("request")
    body = request.get("body") if isinstance(request, dict) else None
    messages = body.get("messages") if isinstance(body, dict) else None
    if not isinstance(messages, list):
        return ""
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        text = _content_text(message.get("content"))
        if text:
            return text
    return ""


def hermes_request_message_count(record: dict[str, Any]) -> int:
    request = record.get("request")
    body = request.get("body") if isinstance(request, dict) else None
    messages = body.get("messages") if isinstance(body, dict) else None
    return len(messages) if isinstance(messages, list) else 0


def hermes_request_timestamp(record: dict[str, Any]) -> float | None:
    """Extract the request-start timestamp from a captured record.

    Trace records expose the response completion timestamp at the top level.
    When ``duration_ms`` is available, subtract it so parallel Hermes child
    requests are compared at their actual start rather than at completion.
    A few integrations put a request timestamp under ``request`` instead, so
    those forms are accepted as fallbacks.  If duration is unavailable, the
    completion timestamp is the best available signal.
    """

    if not isinstance(record, dict):
        return None
    completion = _timestamp(record.get("timestamp"))
    if completion is None and isinstance(record.get("request"), dict):
        for value in (record["request"].get("timestamp"), record["request"].get("started_at")):
            request_start = _timestamp(value)
            if request_start is not None:
                return request_start
    if completion is None:
        return None
    duration_ms = _timestamp(record.get("duration_ms"))
    if duration_ms is not None and duration_ms >= 0:
        return completion - duration_ms / 1000.0
    return completion


# Explicit spelling for callers that want to document the request-start
# semantics.  ``hermes_request_timestamp`` remains the short compatibility
# helper imported by TraceWriter.
hermes_request_start_timestamp = hermes_request_timestamp


def is_hermes_model_request(record: dict[str, Any]) -> bool:
    request = record.get("request")
    path = request.get("path") if isinstance(request, dict) else ""
    if not isinstance(path, str):
        return False
    clean_path = path.lower().split("?", 1)[0].rstrip("/")
    return clean_path in {"/v1/chat/completions", "/chat/completions"}


class HermesSessionResolver:
    """Match captured requests to Hermes session lineage.

    The resolver opens Hermes databases read-only and tolerates older/minimal
    schemas.  Matching is intentionally conservative: if multiple leaves have
    the same temporal/message-count evidence, the result is marked ambiguous
    instead of assigning every child request to one arbitrary leaf.
    """

    def __init__(self, home: Path | None = None):
        configured_home = os.environ.get("HERMES_HOME", "").strip()
        self.home = home or (Path(configured_home).expanduser() if configured_home else Path.home() / ".hermes")

    def resolve_session(
        self,
        first_user: str,
        *,
        request_timestamp: float | str | datetime | None = None,
        message_count: int | None = None,
    ) -> HermesSessionMatch:
        """Resolve a first-user prompt to root/leaf/parent/source metadata.

        ``request_timestamp`` should be the request-start timestamp derived
        from the capture record (``timestamp - duration_ms``).  The
        ``message_count`` value is only a secondary tie-breaker because a
        Hermes session's stored count is its final cumulative count.
        """

        if not first_user:
            return HermesSessionMatch()
        request_time = _timestamp(request_timestamp)
        request_count = _positive_int(message_count)
        candidates: list[_SessionCandidate] = []
        for path in self._state_db_paths():
            candidates.extend(self._candidates_in_db(path, first_user))
        if not candidates:
            return HermesSessionMatch()

        # A session can contain the same user prompt more than once.  Keep the
        # most recent matching message per session before comparing leaves.
        by_session: dict[str, _SessionCandidate] = {}
        for candidate in candidates:
            current = by_session.get(candidate.session_id)
            if current is None or _candidate_event_key(candidate) > _candidate_event_key(current):
                by_session[candidate.session_id] = candidate
        candidates = list(by_session.values())

        selected, ambiguous = self._select_candidate(candidates, request_time, request_count)
        if ambiguous:
            shared_roots = {candidate.root_session_id for candidate in candidates}
            root = shared_roots.pop() if len(shared_roots) == 1 else None
            root_turns = {
                _root_turn(candidate, request_time) for candidate in candidates if candidate.root_session_id == root
            }
            root_turns.discard(None)
            root_turn = root_turns.pop() if len(root_turns) == 1 else None
            return HermesSessionMatch(root_session_id=root, root_turn=root_turn, resolution="ambiguous")
        if selected is None:
            return HermesSessionMatch()
        return HermesSessionMatch(
            root_session_id=selected.root_session_id,
            leaf_session_id=selected.session_id,
            parent_session_id=selected.parent_session_id,
            source=selected.source,
            root_turn=_root_turn(selected, request_time),
            resolution="exact" if selected.session_id == selected.root_session_id else "exact",
        )

    # Short aliases make the richer resolver convenient without changing the
    # old scalar method below.
    resolve = resolve_session
    resolve_identity = resolve_session

    def resolve_root_session(
        self,
        first_user: str,
        *,
        request_timestamp: float | str | datetime | None = None,
        message_count: int | None = None,
    ) -> str | None:
        """Return only the root id (the pre-lineage compatibility API)."""

        return self.resolve_session(
            first_user,
            request_timestamp=request_timestamp,
            message_count=message_count,
        ).root_session_id

    def _state_db_paths(self) -> list[Path]:
        paths = [self.home / "state.db"]
        profiles = self.home / "profiles"
        try:
            paths.extend(sorted(profiles.glob("*/state.db")))
        except OSError:
            pass
        return [path for path in paths if path.is_file()]

    def _candidates_in_db(self, path: Path, first_user: str) -> list[_SessionCandidate]:
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0.05)
            session_columns = self._table_columns(conn, "sessions")
            message_columns = self._table_columns(conn, "messages")
            required_session_columns = {"id"}
            if not required_session_columns.issubset(session_columns) or not {"role", "content", "timestamp"}.issubset(
                message_columns
            ):
                return []

            parent_expr = "s.parent_session_id" if "parent_session_id" in session_columns else "NULL"
            source_expr = "s.source" if "source" in session_columns else "NULL"
            started_expr = "s.started_at" if "started_at" in session_columns else "NULL"
            ended_expr = "s.ended_at" if "ended_at" in session_columns else "NULL"
            if "message_count" in session_columns:
                count_expr = "s.message_count"
            else:
                active_filter = " AND COALESCE(m2.active, 1) = 1" if "active" in message_columns else ""
                count_expr = (
                    "(SELECT COUNT(*) FROM messages m2 "
                    "WHERE m2.session_id = s.id AND m2.role IS NOT NULL"
                    f"{active_filter})"
                )
            active_filter = " AND COALESCE(m.active, 1) = 1" if "active" in message_columns else ""
            rows = conn.execute(
                f"""
                SELECT s.id, {parent_expr}, {source_expr}, {started_expr}, {ended_expr},
                       {count_expr}, m.timestamp
                FROM messages m
                JOIN sessions s ON s.id = m.session_id
                WHERE m.role = 'user' AND m.content = ?{active_filter}
                ORDER BY m.timestamp DESC, m.id DESC
                """,
                (first_user,),
            ).fetchall()
            parent_map = self._session_parent_map(conn, session_columns)
            root_user_timestamps: dict[str, tuple[float, ...]] = {}
            candidates: list[_SessionCandidate] = []
            for row in rows:
                session_id = _nonempty_str(row[0])
                if not session_id:
                    continue
                parent_id = _nonempty_str(row[1])
                root_id = self._root_session_id(session_id, parent_id, parent_map)
                if root_id not in root_user_timestamps:
                    root_user_timestamps[root_id] = self._root_user_timestamps(conn, root_id, message_columns)
                candidates.append(
                    _SessionCandidate(
                        session_id=session_id,
                        parent_session_id=parent_id,
                        source=_nonempty_str(row[2]),
                        started_at=_timestamp(row[3]),
                        ended_at=_timestamp(row[4]),
                        message_count=_positive_int(row[5]),
                        message_timestamp=_timestamp(row[6]),
                        root_session_id=root_id,
                        root_user_timestamps=root_user_timestamps[root_id],
                    )
                )
            return candidates
        except (OSError, sqlite3.Error, ValueError, TypeError):
            return []
        finally:
            if conn is not None:
                conn.close()

    @staticmethod
    def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall() if len(row) > 1}

    @staticmethod
    def _session_parent_map(conn: sqlite3.Connection, columns: set[str]) -> dict[str, str | None]:
        if "parent_session_id" not in columns:
            return {}
        rows = conn.execute("SELECT id, parent_session_id FROM sessions").fetchall()
        return {_nonempty_str(row[0]) or "": _nonempty_str(row[1]) for row in rows}

    @staticmethod
    def _root_user_timestamps(
        conn: sqlite3.Connection,
        root_id: str,
        message_columns: set[str],
    ) -> tuple[float, ...]:
        active_filter = " AND COALESCE(active, 1) = 1" if "active" in message_columns else ""
        rows = conn.execute(
            f"SELECT timestamp FROM messages WHERE session_id = ? AND role = 'user'{active_filter} "
            "ORDER BY timestamp, id",
            (root_id,),
        ).fetchall()
        timestamps = [_timestamp(row[0]) for row in rows]
        return tuple(timestamp for timestamp in timestamps if timestamp is not None)

    @staticmethod
    def _root_session_id(session_id: str, parent_id: str | None, parents: dict[str, str | None]) -> str:
        current = session_id
        parent = parent_id
        seen = {current}
        while parent and parent not in seen:
            seen.add(parent)
            current = parent
            parent = parents.get(current)
        return current

    @classmethod
    def _select_candidate(
        cls,
        candidates: list[_SessionCandidate],
        request_time: float | None,
        request_count: int | None,
    ) -> tuple[_SessionCandidate | None, bool]:
        if len(candidates) == 1:
            return candidates[0], False

        working = candidates
        if request_time is not None:
            # A response timestamp inside exactly one session's lifetime is a
            # strong signal.  Ended sessions get a small clock-skew tolerance.
            in_interval = [candidate for candidate in working if cls._in_session_interval(candidate, request_time)]
            if len(in_interval) == 1:
                return in_interval[0], False
            if in_interval:
                working = in_interval

            def temporal_key(candidate: _SessionCandidate) -> float:
                points = [point for point in (candidate.message_timestamp, candidate.started_at) if point is not None]
                return min((abs(request_time - point) for point in points), default=float("inf"))

            distances = [temporal_key(candidate) for candidate in working]
            if distances:
                best_distance = min(distances)
                nearest = [candidate for candidate, distance in zip(working, distances) if distance == best_distance]
                if len(nearest) == 1:
                    return nearest[0], False
                working = nearest

        # Message count is a secondary signal only after request-start time
        # leaves a tie.  Hermes stores the final session count, so using an
        # exact count before time can incorrectly select an older session.
        if request_count is not None:
            with_counts = [candidate for candidate in working if candidate.message_count is not None]
            if with_counts:
                minimum = min(abs((candidate.message_count or 0) - request_count) for candidate in with_counts)
                nearest_count = [
                    candidate
                    for candidate in with_counts
                    if abs((candidate.message_count or 0) - request_count) == minimum
                ]
                if len(nearest_count) == 1:
                    return nearest_count[0], False
                working = nearest_count

        # If no signal separates the candidates, report ambiguity.  A single
        # root is still safe to retain for tap-session reuse (the writer clears
        # leaf/parent/source in this case).
        if len(working) != 1:
            return None, True
        return working[0], False

    @staticmethod
    def _in_session_interval(candidate: _SessionCandidate, request_time: float) -> bool:
        if candidate.started_at is None:
            return False
        tolerance = 5.0
        if request_time < candidate.started_at - tolerance:
            return False
        return candidate.ended_at is None or request_time <= candidate.ended_at + tolerance


def _candidate_event_key(candidate: _SessionCandidate) -> tuple[float, str]:
    return (
        candidate.message_timestamp if candidate.message_timestamp is not None else float("-inf"),
        candidate.session_id,
    )


def _root_turn(candidate: _SessionCandidate, request_time: float | None) -> int | None:
    """Return the one-based active user-message ordinal in the root session."""

    if request_time is None or not candidate.root_user_timestamps:
        return None
    count = sum(timestamp <= request_time for timestamp in candidate.root_user_timestamps)
    return count or None


def _timestamp(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        parsed = value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            pass
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return None


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _nonempty_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") != "tool_result":
                text = _content_text(item.get("text") or item.get("content"))
                if text:
                    parts.append(text)
        return "\n".join(parts)
    if isinstance(value, dict):
        return _content_text(value.get("text") or value.get("content"))
    return ""
