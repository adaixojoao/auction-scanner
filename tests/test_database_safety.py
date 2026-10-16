"""Default database access stays temporary without the explicit db fixture."""
from pathlib import Path

import pytest


def _main_path(connection):
    return next(row[2] for row in connection.execute("PRAGMA database_list")
                if row[1] == "main")


@pytest.mark.parametrize("arguments", [(), (None,), ("",)])
def test_default_database_is_temporary(tmp_path, arguments):
    import db as dbmod
    expected = tmp_path / "default-test.db"
    assert Path(dbmod.DB_PATH).resolve() == expected.resolve()
    assert not expected.exists()  # fixture must not create a database
    connection = dbmod.connect(*arguments)
    try:
        assert Path(_main_path(connection)).resolve() == expected.resolve()
        assert connection.execute("SELECT 1").fetchone()[0] == 1
    finally:
        connection.close()


def test_explicit_temporary_path_is_respected(tmp_path):
    import db as dbmod
    expected = tmp_path / "explicit.db"
    connection = dbmod.connect(str(expected))
    try:
        assert Path(_main_path(connection)).resolve() == expected.resolve()
        assert not (tmp_path / "default-test.db").exists()
    finally:
        connection.close()


def test_in_memory_database_is_preserved(tmp_path):
    import db as dbmod
    connection = dbmod.connect(":memory:")
    try:
        assert _main_path(connection) == ""
        assert not (tmp_path / "default-test.db").exists()
    finally:
        connection.close()


def test_explicit_db_fixture_still_uses_test_db(db, tmp_path):
    assert Path(_main_path(db)).resolve() == (tmp_path / "test.db").resolve()
    assert not (tmp_path / "default-test.db").exists()
