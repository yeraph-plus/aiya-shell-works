from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import pytest
from PIL import Image

from core import CORE_VERSION, ModuleManager, execute_workflow
from core.config_schema import ConfigValidationError, normalize_config_params

MODULES_DIR = Path(__file__).resolve().parents[1] / "modules"

EXPECTED_MODULES = {
    "cycle-counter",
    "delete-files",
    "exiftool-clean",
    "extract-archive",
    "ffmpeg-convert",
    "flatten-folder",
    "gallery-count",
    "gallery-rename",
    "image-resize-watermark",
    "image-transcode",
    "normalize-extensions",
    "pack-rar",
    "rename-by-pattern",
    "strip-attributes",
    "verify-create-text-file",
    "verify-line-echo",
    "verify-rename-path",
    "verify-run-external-tool",
    "verify-write-summary",
}


def _workflow(name: str, steps: list[tuple[str, dict]]) -> dict:
    return {
        "meta": {"name": name},
        "scope": 1,
        "steps": [{"module": slug, "params": params} for slug, params in steps],
    }


def test_all_builtin_modules_pass_registration_contract() -> None:
    manager = ModuleManager(MODULES_DIR)
    modules = manager.scan_modules()

    assert set(modules) == EXPECTED_MODULES
    assert manager.warnings == []
    assert all(module.core_version == CORE_VERSION for module in modules.values())
    assert modules["ffmpeg-convert"].platforms == ("windows",)
    assert modules["pack-rar"].platforms == ("windows",)
    assert modules["strip-attributes"].platforms == ("windows",)


def test_external_tool_timeout_uses_supported_range_keys() -> None:
    module = ModuleManager(MODULES_DIR).get_module("verify-run-external-tool")
    assert module is not None

    with pytest.raises(ConfigValidationError):
        normalize_config_params(module.config_schema, {"mock_tool_path": "", "timeout_seconds": 0})


def test_workspace_file_modules_run_as_a_chain(tmp_path: Path) -> None:
    source = tmp_path / "gallery"
    nested = source / "nested"
    nested.mkdir(parents=True)
    (nested / "photo.JPEG").write_bytes(b"image-placeholder")
    (nested / "remove.txt").write_text("remove", encoding="utf-8")
    (nested / "asset.dat").write_bytes(b"data")
    output = tmp_path / "output"

    summary = execute_workflow(
        _workflow(
            "workspace modules",
            [
                ("flatten-folder", {}),
                ("delete-files", {}),
                ("normalize-extensions", {}),
                ("gallery-rename", {}),
                ("gallery-count", {}),
            ],
        ),
        output_dir=output,
        files=[source],
        modules_dir=MODULES_DIR,
    )

    assert summary["success"], summary["errors"]
    result_dir = next(path for path in output.iterdir() if path.is_dir())
    assert result_dir.name == "gallery [1DAT]"
    assert {path.name for path in result_dir.iterdir()} == {"001.jpg", "DAT_001.dat"}


def test_rename_by_pattern_replaces_each_filename_once(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "alpha.txt").write_text("alpha", encoding="utf-8")
    (source / "beta.txt").write_text("beta", encoding="utf-8")
    output = tmp_path / "output"

    summary = execute_workflow(
        _workflow(
            "pattern rename",
            [
                (
                    "rename-by-pattern",
                    {
                        "match": ".*",
                        "use_regex": True,
                        "include_extension": False,
                        "replace": "item_${increment=1,padding=3,start=1}",
                    },
                )
            ],
        ),
        output_dir=output,
        files=[source],
        modules_dir=MODULES_DIR,
    )

    assert summary["success"], summary["errors"]
    assert {path.name for path in output.rglob("*.txt")} == {"item_001.txt", "item_002.txt"}


def test_archive_and_image_modules_follow_workspace_ownership(tmp_path: Path) -> None:
    archive = tmp_path / "sample.zip"
    with ZipFile(archive, "w") as handle:
        handle.writestr("nested/a.jpg", b"a")
        handle.writestr("nested/b.png", b"b")
        handle.writestr("readme.txt", b"text")
    archive_output = tmp_path / "archive-output"

    archive_summary = execute_workflow(
        _workflow("extract", [("extract-archive", {"extract_count": 2, "category": "Test"})]),
        output_dir=archive_output,
        files=[archive],
        modules_dir=MODULES_DIR,
    )

    assert archive_summary["success"], archive_summary["errors"]
    assert {path.name for path in (archive_output / "sample").iterdir()} == {"a.jpg", "b.png", "info.json"}

    image_source = tmp_path / "image.png"
    Image.new("RGB", (64, 32), (0, 128, 255)).save(image_source)
    image_output = tmp_path / "image-output"
    image_summary = execute_workflow(
        _workflow(
            "image chain",
            [
                (
                    "image-resize-watermark",
                    {
                        "max_width_in": 16,
                        "quality": 80,
                        "output_format": "keep",
                        "watermark_enabled": False,
                        "watermark_content": "",
                        "watermark_opacity": 0.5,
                        "watermark_positions": "bottom-right",
                        "watermark_margin": 2,
                        "watermark_font_size": 12,
                        "watermark_scale": 15,
                    },
                ),
                ("image-transcode", {"mode": "jpg", "quality": 90, "jpeg_background": "FFFFFF"}),
            ],
        ),
        output_dir=image_output,
        files=[image_source],
        modules_dir=MODULES_DIR,
    )

    assert image_summary["success"], image_summary["errors"]
    result = image_output / "image.jpg"
    assert result.is_file()
    with Image.open(result) as image:
        assert image.size == (16, 8)
