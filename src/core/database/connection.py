"""Shared PostgreSQL connection configuration and pool factory."""

from src.config.settings import (
    MEMORY_DB_HOST,
    MEMORY_DB_NAME,
    MEMORY_DB_PASSWORD,
    MEMORY_DB_PORT,
    MEMORY_DB_USER,
)


def connection_kwargs() -> dict:
    return {
        "host": MEMORY_DB_HOST,
        "port": MEMORY_DB_PORT,
        "dbname": MEMORY_DB_NAME,
        "user": MEMORY_DB_USER,
        "password": MEMORY_DB_PASSWORD,
    }


def create_pool(*, min_size: int = 1, max_size: int = 4):
    from psycopg.conninfo import make_conninfo
    from psycopg_pool import ConnectionPool

    return ConnectionPool(
        conninfo=make_conninfo("", **connection_kwargs()),
        min_size=min_size,
        max_size=max_size,
        open=True,
    )
