"""A copy of the database somewhere that is not this PC (updater.backup_database,
the scheduler's backup job, Settings → Backup)."""
import os
import sqlite3

import pytest

import dashboard
import scheduler
import updater


@pytest.fixture
def client(db):
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


@pytest.fixture
def books(tmp_path, monkeypatch):
    """An auctions.db with something in it, and an empty folder to copy it to."""
    path = tmp_path / "auctions.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE listings (id TEXT)")
    conn.execute("INSERT INTO listings VALUES ('citius:1')")
    conn.commit()
    conn.close()
    monkeypatch.setenv("AUCTION_SCANNER_DB", str(path))
    return str(tmp_path / "onedrive")


def test_the_copy_is_a_database_you_can_open(books):
    made = updater.backup_database(folder=books)
    assert os.path.dirname(made) == os.path.abspath(books)
    assert sqlite3.connect(made).execute("SELECT id FROM listings").fetchone()[0] == "citius:1"


def test_only_the_last_few_copies_are_kept(books):
    os.makedirs(books, exist_ok=True)
    for name in ("auctions-20260101-000000.db", "auctions-20260102-000000.db",
                 "auctions-20260103-000000.db"):
        open(os.path.join(books, name), "w").close()
    updater.backup_database(folder=books, keep=2)
    left = sorted(os.listdir(books))
    assert len(left) == 2 and "auctions-20260101-000000.db" not in left


def test_no_folder_means_no_copy_and_no_complaint(books, monkeypatch):
    import config
    config.save_config({"backup": {"folder": ""}})
    scheduler.run_backup()                       # nothing to do, and nothing raised
    assert not os.path.exists(books)


def test_the_daily_job_copies_the_database(books):
    import config
    config.save_config({"backup": {"folder": books, "keep": 3}})
    scheduler.run_backup()
    assert len(os.listdir(books)) == 1
    assert "backup" in scheduler.JOBS and scheduler.JOB_FUNCS["backup"] is scheduler.run_backup


def test_an_unplugged_drive_does_not_stop_the_scheduler(books, monkeypatch):
    import config
    config.save_config({"backup": {"folder": books}})
    monkeypatch.setattr(updater, "backup_database",
                        lambda **kw: (_ for _ in ()).throw(OSError("drive not ready")))
    scheduler.run_backup()                       # logged, not raised


def test_settings_holds_the_folder_and_the_button_copies(client, books):
    import config
    assert client.get("/api/backup").get_json() == {"folder": "", "keep": 14, "copies": [], "error": None}
    assert client.post("/api/backup").status_code == 400          # no folder yet

    client.post("/api/settings", json={"backup": {"folder": books, "keep": 5}})
    assert config.load_config()["backup"]["folder"] == books
    made = client.post("/api/backup").get_json()
    assert made["ok"] and os.path.exists(made["path"])

    state = client.get("/api/backup").get_json()
    assert state["folder"] == books and len(state["copies"]) == 1
    assert state["copies"][0]["name"].startswith("auctions-") and state["error"] is None


def test_a_folder_that_is_not_there_is_reported(client, tmp_path):
    client.post("/api/settings", json={"backup": {"folder": str(tmp_path / "gone")}})
    assert "does not exist" in client.get("/api/backup").get_json()["error"]
