"""Batch 02: isolated configuration acknowledgement, atomicity and writer regressions."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import sys

import pytest

import config


@pytest.mark.parametrize("value", [None, [], "not an object", 1])
def test_update_rejects_non_object_changes(value):
    with pytest.raises(ValueError, match="JSON object"):
        config.update_config(value)


@pytest.mark.parametrize("value", [None, [], "not an object", 1])
def test_save_rejects_non_object_replacement(value):
    with pytest.raises(ValueError, match="JSON object"):
        config.save_config(value)


def test_acknowledgement_belongs_to_writer_even_if_another_write_follows(monkeypatch):
    config.save_config({"max_price": 10000})
    real_transaction = config._config_transaction

    @contextmanager
    def transaction_then_other_writer():
        with real_transaction():
            yield
        # Deterministically simulate another writer immediately after unlock.
        with real_transaction():
            config._write_config_unlocked({"max_price": 20000})

    monkeypatch.setattr(config, "_config_transaction", transaction_then_other_writer)
    acknowledged = config.update_config({"max_price": 15000})
    assert acknowledged["max_price"] == 15000
    assert config.load_config()["max_price"] == 20000


def test_thread_updates_preserve_unrelated_keys():
    config.save_config({"seed": "synthetic"})

    def update(number):
        return config.update_config({f"worker_{number}": number})

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(update, range(16)))
    result = config.load_user_config()
    assert result["seed"] == "synthetic"
    assert all(result[f"worker_{number}"] == number for number in range(16))


def test_separate_process_updates_preserve_unrelated_keys(tmp_path):
    root = Path(config.__file__).resolve().parent
    target = tmp_path / "multiprocess-config.json"
    worker = r'''
import sys
sys.path.insert(0, sys.argv[1])
import config
config.CONFIG_PATH = sys.argv[2]
number = int(sys.argv[3])
config.update_config({"worker_" + str(number): number})
'''
    processes = []
    try:
        for number in range(4):
            processes.append(subprocess.Popen(
                [sys.executable, "-c", worker, str(root), str(target), str(number)],
                cwd=tmp_path, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True))
        for process in processes:
            _, error = process.communicate(timeout=60)
            assert process.returncode == 0, error
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate()
    result = json.loads(target.read_text(encoding="utf-8"))
    assert result == {f"worker_{number}": number for number in range(4)}
    assert not list(tmp_path.glob(".config-*.tmp"))


def test_replace_failure_keeps_last_good_file_and_cleans_temp(monkeypatch):
    config.save_config({"seed": "original"})
    before = Path(config.CONFIG_PATH).read_bytes()

    def fail_replace(source, target):
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(config.os, "replace", fail_replace)
    with pytest.raises(OSError, match="synthetic replace failure"):
        config.update_config({"seed": "replacement"})
    assert Path(config.CONFIG_PATH).read_bytes() == before
    assert not list(Path(config.CONFIG_PATH).parent.glob(".config-*.tmp"))


def test_serialization_failure_keeps_original_and_releases_lock():
    config.save_config({"seed": "original"})
    before = Path(config.CONFIG_PATH).read_bytes()
    with pytest.raises(TypeError):
        config.update_config({"invalid": object()})
    assert Path(config.CONFIG_PATH).read_bytes() == before
    assert not list(Path(config.CONFIG_PATH).parent.glob(".config-*.tmp"))
    config.update_config({"later": "works"})
    assert config.load_user_config()["later"] == "works"


def test_returned_configuration_is_detached():
    changes = {"filters": {"countries": ["PT"]}}
    acknowledged = config.update_config(changes)
    changes["filters"]["countries"].append("ES")
    acknowledged["filters"]["countries"].append("FR")
    assert config.load_user_config()["filters"]["countries"] == ["PT"]
