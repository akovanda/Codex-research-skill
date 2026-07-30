from __future__ import annotations

from pathlib import Path
from types import TracebackType

from ..db import (
    DatabaseTarget,
    DbConnection,
    open_database,
    resolve_database_target,
)
from .repositories import DepositRepository, SourceVersionRepository


class UnitOfWork:
    """Explicit transaction boundary for one application operation."""

    def __init__(self, database: str | Path | DatabaseTarget):
        self.database = (
            database
            if isinstance(database, DatabaseTarget)
            else resolve_database_target(database)
        )
        self.connection: DbConnection | None = None
        self.deposit: DepositRepository | None = None
        self.source_versions: SourceVersionRepository | None = None
        self._committed = False

    def __enter__(self) -> UnitOfWork:
        if self.connection is not None:
            raise RuntimeError("unit of work is already active")
        self.connection = open_database(self.database)
        self.deposit = DepositRepository(self.connection)
        self.source_versions = SourceVersionRepository(self.connection)
        return self

    def commit(self) -> None:
        if self.connection is None:
            raise RuntimeError("unit of work is not active")
        self.connection.commit()
        self._committed = True

    def rollback(self) -> None:
        if self.connection is not None and not self._committed:
            self.connection.rollback()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        assert self.connection is not None
        try:
            if exc_type is not None or not self._committed:
                self.connection.rollback()
        finally:
            self.connection.close()
            self.connection = None
            self.deposit = None
            self.source_versions = None
