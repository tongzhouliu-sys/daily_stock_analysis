# -*- coding: utf-8 -*-
"""Login audit log repository."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, delete, desc, func, select

from src.storage import DatabaseManager, LoginAuditLog


class LoginAuditRepository:
    """DB access layer for login audit log."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def create(self, fields: Dict[str, Any]) -> int:
        """Insert a new audit log row and return its id."""
        with self.db.get_session() as session:
            row = LoginAuditLog(**fields)
            session.add(row)
            session.commit()
            return row.id

    def update_geo(self, row_id: int, fields: Dict[str, Any]) -> bool:
        """Update geo-related fields for a row. Returns True if updated."""
        with self.db.get_session() as session:
            row = session.execute(
                select(LoginAuditLog).where(LoginAuditLog.id == row_id).limit(1)
            ).scalar_one_or_none()
            if row is None:
                return False
            for key, value in fields.items():
                setattr(row, key, value)
            session.commit()
            return True

    def list_paginated(
        self,
        *,
        result: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[LoginAuditLog], int]:
        """Return paginated rows with total count. result: all/success/failure."""
        conditions = []
        if result == "success":
            conditions.append(LoginAuditLog.success.is_(True))
        elif result == "failure":
            conditions.append(LoginAuditLog.success.is_(False))

        where_clause = and_(*conditions) if conditions else True
        with self.db.get_session() as session:
            total = session.execute(
                select(func.count(LoginAuditLog.id))
                .select_from(LoginAuditLog)
                .where(where_clause)
            ).scalar() or 0

            rows = session.execute(
                select(LoginAuditLog)
                .where(where_clause)
                .order_by(desc(LoginAuditLog.created_at), desc(LoginAuditLog.id))
                .offset(offset)
                .limit(limit)
            ).scalars().all()
            return list(rows), int(total)

    def cleanup(self, *, retention_days: int, max_rows: int) -> int:
        """Delete rows older than retention_days or exceeding max_rows. Returns deleted count."""
        cutoff = datetime.utcnow() - timedelta(days=retention_days)
        deleted = 0

        with self.db.get_session() as session:
            # Delete by age
            result = session.execute(
                delete(LoginAuditLog).where(LoginAuditLog.created_at < cutoff)
            )
            deleted += result.rowcount
            session.commit()

        # Delete by row count (keep newest max_rows)
        with self.db.get_session() as session:
            total = session.execute(
                select(func.count(LoginAuditLog.id)).select_from(LoginAuditLog)
            ).scalar() or 0

            if total > max_rows:
                excess = total - max_rows
                # Get the oldest 'excess' rows
                oldest_ids = session.execute(
                    select(LoginAuditLog.id)
                    .order_by(LoginAuditLog.created_at.asc(), LoginAuditLog.id.asc())
                    .limit(excess)
                ).scalars().all()

                if oldest_ids:
                    result = session.execute(
                        delete(LoginAuditLog).where(LoginAuditLog.id.in_(oldest_ids))
                    )
                    deleted += result.rowcount
                session.commit()

        return deleted
