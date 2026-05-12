"""Initialize PostgreSQL schema without Docker.

This applies SQL files in `db/init/` to the configured database.

Usage (PowerShell):
  python -m venv .venv
  .\\.venv\\Scripts\\pip install -r requirements.txt
  $env:DATABASE_URL="postgresql://user:pass@host:5432/db"
  .\\.venv\\Scripts\\python scripts\\init_postgres_schema.py
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv(usecwd=True))


def _read_sql_files(init_dir: Path) -> list[tuple[str, str]]:
    files = sorted([p for p in init_dir.glob("*.sql")])
    out: list[tuple[str, str]] = []
    for p in files:
        out.append((p.name, p.read_text(encoding="utf-8")))
    return out


async def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    os.chdir(repo_root)

    from app.utils.config import settings

    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    init_dir = Path("db") / "init"
    if not init_dir.exists():
        raise RuntimeError(f"missing db init dir: {init_dir}")

    sql_files = _read_sql_files(init_dir)
    if not sql_files:
        raise RuntimeError(f"no .sql files found under: {init_dir}")

    import asyncpg

    print(f"[init_postgres_schema] dsn: {dsn}")
    print(f"[init_postgres_schema] applying {len(sql_files)} files from {init_dir}")

    conn = await asyncpg.connect(dsn)
    try:
        for name, sql in sql_files:
            if not sql.strip():
                continue
            print(f"[init_postgres_schema] -> {name}")
            await conn.execute(sql)
        print("[init_postgres_schema] done")
    finally:
        await conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

