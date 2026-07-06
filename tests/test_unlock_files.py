from __future__ import annotations

from pathlib import Path

from modules import unlock_files


def test_find_locked_files_does_not_rename_targets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "photo.jpg"
    target.write_text("demo", encoding="utf-8")

    monkeypatch.setattr(
        unlock_files,
        "_is_locked",
        lambda path: path == target,
    )

    locked = unlock_files._find_locked_files([target])

    assert locked == [target]
    assert target.exists()
    assert target.name == "photo.jpg"
    assert list(tmp_path.glob("__swp_lock_test_*")) == []
