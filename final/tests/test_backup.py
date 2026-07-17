import json
import os
import sys

PYTHON_SRC = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "app", "src", "main", "python")
)
if PYTHON_SRC not in sys.path:
    sys.path.insert(0, PYTHON_SRC)

import pytest  # noqa: E402

import encryption  # noqa: E402


@pytest.fixture(autouse=True)
def _key(tmp_path):
    encryption.init("11" * 32)
    yield
    encryption._KEY = None


def test_full_backup_bundles_config_and_scripts(tmp_path):
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    (files_dir / "config.json").write_text(json.dumps({"theme_bg": "#123456"}))
    scripts_dir = files_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "hello.py").write_text("print('hi')")

    backups_dir = files_dir / "backups"
    result = encryption.encrypted_full_backup(str(files_dir), str(backups_dir))

    assert "error" not in result
    assert "config.json" in result["included"]
    assert "scripts/hello.py" in result["included"]
    assert os.path.exists(result["backup"])
    assert result["backup"].endswith(".zip.enc")


def test_full_backup_with_nothing_to_back_up_reports_error(tmp_path):
    files_dir = tmp_path / "empty_files"
    files_dir.mkdir()
    result = encryption.encrypted_full_backup(str(files_dir), str(files_dir / "backups"))
    assert "error" in result


def test_restore_full_backup_extracts_without_touching_live_files(tmp_path):
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    (files_dir / "config.json").write_text(json.dumps({"theme_bg": "#654321"}))
    scripts_dir = files_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "keep_me.py").write_text("print('preserved')")

    backup_result = encryption.encrypted_full_backup(str(files_dir), str(files_dir / "backups"))

    # simulate data loss: the live config/scripts get wiped (e.g. reinstall)
    (files_dir / "config.json").unlink()
    (scripts_dir / "keep_me.py").unlink()

    restore_result = encryption.restore_full_backup(backup_result["backup"], str(files_dir))

    assert "error" not in restore_result
    restored_dir = restore_result["restored_to"]
    assert os.path.exists(os.path.join(restored_dir, "config.json"))
    assert os.path.exists(os.path.join(restored_dir, "scripts", "keep_me.py"))
    with open(os.path.join(restored_dir, "config.json")) as f:
        assert json.load(f)["theme_bg"] == "#654321"

    # the live (now-empty) locations were NOT silently repopulated
    assert not (files_dir / "config.json").exists()


def test_restore_full_backup_missing_file_reports_error(tmp_path):
    result = encryption.restore_full_backup(str(tmp_path / "nope.zip.enc"), str(tmp_path))
    assert "error" in result
