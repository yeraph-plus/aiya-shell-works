"""调用 WinRAR (rar.exe) 将文件夹打包为 .rar 压缩包。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.context import PipelineContext
    from core.runtime import PipelineRuntime

MODULE_META = {
    "slug": "pack-rar",
    "name": "RAR 打包",
    "core_version": "2.0.0",
    "tags": ["archive", "compress", "rar"],
    "atom": ["folder"],
    "description": "调用 WinRAR 将文件夹打包为 .rar 压缩包。",
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "winrar_path": {
            "type": "file_path",
            "title": "WinRAR 路径",
            "default": "",
            "description": "rar.exe 的完整路径，必须填写。",
            "required": True,
        },
        "archive_name": {
            "type": "str",
            "title": "压缩包名称",
            "default": "",
            "description": "压缩包名称（不含后缀），留空则使用文件夹名。",
        },
        "compression_level": {
            "type": "select",
            "title": "压缩级别",
            "default": "3",
            "options": ["0", "1", "2", "3", "4", "5"],
            "description": "0=仅存储, 1=最快, 3=默认, 5=最优压缩。",
        },
        "solid_archive": {
            "type": "bool",
            "title": "固实压缩",
            "default": False,
            "description": "启用固实压缩以获得更高压缩率（-s 参数）。",
        },
        "delete_after": {
            "type": "bool",
            "title": "打包后删除源文件夹",
            "default": False,
            "description": "压缩成功后删除源文件夹。",
        },
        "password": {
            "type": "str",
            "title": "加密密码",
            "default": "",
            "description": "设置压缩包密码（-hp 参数，同时加密文件数据和文件头），留空则不加密。",
        },
        "comment": {
            "type": "str",
            "title": "压缩包备注",
            "default": "",
            "description": "写入压缩包的备注文本（-z 参数），支持换行（\\n），留空则不写入。",
        },
    },
}


def _build_command(cfg: dict, rar_exe: str, archive_path: Path, source_path: Path) -> list[str]:
    cmd = [rar_exe, "a"]

    level = cfg.get("compression_level", "3")
    cmd.extend(["-m" + str(level)])

    if cfg.get("solid_archive", False):
        cmd.append("-s")

    password = cfg.get("password", "").strip()
    if password:
        cmd.append("-hp" + password)

    comment = cfg.get("comment", "").strip()
    comment_file: Path | None = None
    if comment:
        comment_file = archive_path.parent / ".rar_comment.txt"
        comment_file.write_text(comment, encoding="utf-8")
        cmd.extend(["-z", str(comment_file)])

    cmd.append(str(archive_path))
    cmd.append(str(source_path))
    return cmd


def run(ctx: PipelineContext, cfg: dict[str, Any], runtime: PipelineRuntime) -> PipelineContext | None:
    rar_exe = cfg.get("winrar_path", "").strip()
    if not rar_exe:
        runtime.log("pack-rar", "error", "未配置 WinRAR 路径，请在工作流中指定 rar.exe 的完整路径。")
        return ctx

    rar_path = Path(rar_exe)
    if not rar_path.is_file():
        runtime.log("pack-rar", "error", f"WinRAR 可执行文件不存在: {rar_exe}")
        return ctx

    source = Path(ctx.working_path)
    if not source.exists():
        runtime.log("pack-rar", "error", f"源文件夹不存在: {source}")
        return ctx

    archive_name = cfg.get("archive_name", "").strip()
    if not archive_name:
        archive_name = source.name
    archive_path = ctx.output_dir / (archive_name + ".rar")

    cmd = _build_command(cfg, rar_exe, archive_path, source)
    comment_file = archive_path.parent / ".rar_comment.txt"

    runtime.log("pack-rar", "hint", f"WinRAR 命令行: {' '.join(cmd)}")
    runtime.log("pack-rar", "message", f"开始打包: {source.name} → {archive_path.name} ...")

    try:
        result = runtime.spawn(cmd)
    except OSError as e:
        runtime.log("pack-rar", "error", f"WinRAR 启动失败: {e}")
        return ctx
    finally:
        if comment_file.exists():
            try:
                comment_file.unlink()
            except OSError:
                pass

    if result.is_success:
        ctx.track_extra_file(archive_path)
        runtime.log("pack-rar", "success", f"打包完成: {archive_path.name}")

        if cfg.get("delete_after", False):
            try:
                import shutil

                shutil.rmtree(source)
                runtime.log("pack-rar", "message", f"已删除源文件夹: {source.name}")
            except OSError as e:
                runtime.log("pack-rar", "warning", f"删除源文件夹失败: {e}")
    else:
        runtime.log("pack-rar", "error", f"WinRAR 返回非零退出码: {result.exit_code}")

    return ctx
