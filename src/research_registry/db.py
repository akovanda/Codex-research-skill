from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover - optional dependency for postgres-only paths
    psycopg = None
    dict_row = None


@dataclass(frozen=True)
class DatabaseTarget:
    url: str
    kind: str
    sqlite_path: Path | None = None

    @property
    def label(self) -> str:
        return str(self.sqlite_path) if self.sqlite_path is not None else self.url


def resolve_database_target(target: str | Path) -> DatabaseTarget:
    if isinstance(target, Path):
        path = target.expanduser().resolve()
        return DatabaseTarget(url=f"sqlite:///{path}", kind="sqlite", sqlite_path=path)
    raw = str(target).strip()
    if "://" not in raw:
        path = Path(raw).expanduser().resolve()
        return DatabaseTarget(url=f"sqlite:///{path}", kind="sqlite", sqlite_path=path)
    if raw.startswith("sqlite:///"):
        path = Path(raw.removeprefix("sqlite:///")).expanduser().resolve()
        return DatabaseTarget(url=f"sqlite:///{path}", kind="sqlite", sqlite_path=path)
    if raw.startswith("postgresql://") or raw.startswith("postgres://"):
        return DatabaseTarget(url=raw, kind="postgres")
    raise ValueError(f"unsupported database target: {raw}")


class DbCursor:
    def __init__(self, cursor: Any):
        self._cursor = cursor

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()


class DbConnection:
    def __init__(self, target: DatabaseTarget, raw_connection: Any):
        self.target = target
        self.raw_connection = raw_connection

    def execute(self, sql: str, params: Any = ()) -> DbCursor:
        if self.target.kind == "postgres":
            translated = self._translate_sql(sql) if params else sql
            cursor = self.raw_connection.execute(translated, params)
            return DbCursor(cursor)
        cursor = self.raw_connection.execute(sql, params)
        return DbCursor(cursor)

    def executescript(self, script: str) -> None:
        if self.target.kind == "sqlite":
            self.raw_connection.executescript(script)
            return
        for statement in split_sql_script(script, dialect="postgres"):
            self.execute(statement)

    def commit(self) -> None:
        self.raw_connection.commit()

    def rollback(self) -> None:
        self.raw_connection.rollback()

    def close(self) -> None:
        self.raw_connection.close()

    def _translate_sql(self, sql: str) -> str:
        return sql.replace("?", "%s")


_DOLLAR_QUOTE = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")


def split_sql_script(
    script: str, *, dialect: str | None = None
) -> list[str]:
    if dialect == "sqlite":
        return _split_sqlite_script(script)
    if dialect not in {None, "postgres"}:
        raise ValueError(f"unsupported SQL dialect: {dialect}")
    return _split_postgres_script(script)


def _split_sqlite_script(script: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    for char in script:
        current.append(char)
        if char != ";":
            continue
        candidate = "".join(current).strip()
        if candidate and sqlite3.complete_statement(candidate):
            statements.append(candidate)
            current = []
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def _split_postgres_script(script: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    quote_char: str | None = None
    dollar_quote: str | None = None
    line_comment = False
    block_comment_depth = 0
    index = 0
    while index < len(script):
        char = script[index]

        if line_comment:
            current.append(char)
            index += 1
            if char == "\n":
                line_comment = False
            continue

        if block_comment_depth:
            if script.startswith("/*", index):
                current.append("/*")
                block_comment_depth += 1
                index += 2
                continue
            if script.startswith("*/", index):
                current.append("*/")
                block_comment_depth -= 1
                index += 2
                continue
            current.append(char)
            index += 1
            continue

        if dollar_quote is not None:
            if script.startswith(dollar_quote, index):
                current.append(dollar_quote)
                index += len(dollar_quote)
                dollar_quote = None
            else:
                current.append(char)
                index += 1
            continue

        if quote_char is not None:
            current.append(char)
            index += 1
            if char == "\\" and index < len(script):
                current.append(script[index])
                index += 1
                continue
            if char != quote_char:
                continue
            if index < len(script) and script[index] == quote_char:
                current.append(script[index])
                index += 1
            else:
                quote_char = None
            continue

        if script.startswith("--", index):
            current.append("--")
            line_comment = True
            index += 2
            continue
        if script.startswith("/*", index):
            current.append("/*")
            block_comment_depth = 1
            index += 2
            continue
        if char in {"'", '"'}:
            current.append(char)
            quote_char = char
            index += 1
            continue
        if char == "$":
            match = _DOLLAR_QUOTE.match(script, index)
            if match is not None:
                dollar_quote = match.group(0)
                current.append(dollar_quote)
                index = match.end()
                continue
        if char == ";":
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


@contextmanager
def connect_database(target: DatabaseTarget) -> Iterator[DbConnection]:
    if target.kind == "sqlite":
        assert target.sqlite_path is not None
        target.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        raw = sqlite3.connect(target.sqlite_path)
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA foreign_keys = ON")
    else:
        if psycopg is None or dict_row is None:  # pragma: no cover - import error path
            raise RuntimeError("psycopg is required for postgres database URLs")
        raw = psycopg.connect(target.url, row_factory=dict_row)
    connection = DbConnection(target, raw)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
