"""GUI project directory resolution and persisted selection."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSettings

SETTINGS_ORGANIZATION = "ShellWorker"
SETTINGS_APPLICATION = "ShellWorker"
PROJECT_ROOT_KEY = "project/root"


@dataclass(frozen=True, slots=True)
class ProjectPaths:
    root: Path
    workflows_dir: Path
    modules_dir: Path

    @classmethod
    def from_root(cls, root: str | Path) -> ProjectPaths:
        resolved_root = Path(root).resolve()
        if not resolved_root.is_dir():
            raise ValueError(f"项目目录不存在: {resolved_root}")

        workflows_dir = resolved_root / "workflows"
        workflows_dir.mkdir(parents=True, exist_ok=True)
        project_modules = resolved_root / "modules"
        modules_dir = project_modules if project_modules.is_dir() else _installed_modules_dir()
        if modules_dir is None:
            raise ValueError(f"项目目录缺少 modules/: {resolved_root}")
        return cls(root=resolved_root, workflows_dir=workflows_dir, modules_dir=modules_dir)


class GuiProjectSettings:
    """Resolve and remember the active GUI project."""

    def __init__(self, settings: QSettings | None = None) -> None:
        self._settings = settings or QSettings(SETTINGS_ORGANIZATION, SETTINGS_APPLICATION)

    def remembered_root(self) -> Path | None:
        value = str(self._settings.value(PROJECT_ROOT_KEY, "")).strip()
        return Path(value).resolve() if value else None

    def remember(self, paths: ProjectPaths) -> None:
        self._settings.setValue(PROJECT_ROOT_KEY, str(paths.root))
        self._settings.sync()

    def resolve(
        self,
        *,
        explicit_root: str | Path | None = None,
        default_root: str | Path | None = None,
    ) -> ProjectPaths | None:
        candidates = (explicit_root, self.remembered_root(), default_root)
        for candidate in candidates:
            if candidate is None:
                continue
            try:
                return ProjectPaths.from_root(candidate)
            except ValueError:
                continue
        return None


def _installed_modules_dir() -> Path | None:
    spec = importlib.util.find_spec("modules")
    if spec is None:
        return None
    locations = spec.submodule_search_locations
    if locations:
        candidate = Path(next(iter(locations))).resolve()
        return candidate if candidate.is_dir() else None
    if spec.origin:
        candidate = Path(spec.origin).resolve().parent
        return candidate if candidate.is_dir() else None
    return None
