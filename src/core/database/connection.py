"""Shared PostgreSQL connection configuration and pool factory."""

from src.config.settings import (
    MEMORY_DB_HOST,
    MEMORY_DB_NAME,
    MEMORY_DB_PASSWORD,
    MEMORY_DB_PORT,
    MEMORY_DB_URL,
    MEMORY_DB_USER,
)


def connection_kwargs() -> dict:
    if MEMORY_DB_URL:
        from psycopg.conninfo import conninfo_to_dict

        return conninfo_to_dict(MEMORY_DB_URL)
    return {
        "host": MEMORY_DB_HOST,
        "port": MEMORY_DB_PORT,
        "dbname": MEMORY_DB_NAME,
        "user": MEMORY_DB_USER,
        "password": MEMORY_DB_PASSWORD,
    }


def connection_info() -> str:
    from psycopg.conninfo import make_conninfo

    return make_conninfo("", **connection_kwargs())


def create_pool(*, min_size: int = 1, max_size: int = 4):
    from psycopg_pool import ConnectionPool

    return ConnectionPool(
        conninfo=connection_info(),
        min_size=min_size,
        max_size=max_size,
        open=True,
    )
