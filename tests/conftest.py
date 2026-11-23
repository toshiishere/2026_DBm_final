import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.rows import dict_row

from app.db import get_db
from app.main import app


@pytest.fixture
def database():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.fail("Set TEST_DATABASE_URL to a disposable PostgreSQL database (see README)")
    schema = "test_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    options = f"-csearch_path={schema}"

    def connect(*, autocommit=False):
        return psycopg.connect(dsn, options=options, row_factory=dict_row, autocommit=autocommit)

    try:
        with connect() as conn:
            conn.execute(Path("db/001_schema.sql").read_text())
            conn.execute(Path("db/002_seed.sql").read_text())
        yield connect
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def db(database):
    with database(autocommit=True) as conn:
        yield conn


@pytest.fixture
def client(database):
    def override():
        with database() as conn:
            yield conn

    app.dependency_overrides[get_db] = override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
