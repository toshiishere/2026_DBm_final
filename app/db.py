import os
from collections.abc import Iterator

import psycopg
from psycopg.rows import dict_row


def get_db() -> Iterator[psycopg.Connection]:
    # Exiting commits on success, rolls back on error, and closes the connection.
    # PG* environment variables are used by libpq when DATABASE_URL is absent.
    with psycopg.connect(os.getenv("DATABASE_URL", ""), row_factory=dict_row) as conn:
        yield conn
