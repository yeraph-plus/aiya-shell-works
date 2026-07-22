"""按模式重命名：匹配 + 替换表达式重命名文件，支持正则、日期变量、计数器、UUID 和随机字符串。

替换表达式语法：
  $Y   4位年     2026          $y   2位年     26
  $M   2位月     07            $m   月(无补零)  7
  $D   2位日     14            $d   日(无补零)  14
  $h   24小时制  14            $t   12小时制   02
  $i   分钟      30            $s   秒        45

  ${increment=3,padding=4,start=900}  计数器 (初始/步长/位数)
  ${ruuidv4}                         随机 UUID v4
  ${rstringalnum=9}                  随机字母数字 (长度)

$$ 转义为字面量 $。
"""

from __future__ import annotations

import random
import re
import string
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.context import PipelineContext
    from core.runtime import PipelineRuntime

MODULE_META = {
    "slug": "rename-by-pattern",
    "name": "按模式重命名",
    "description": "使用匹配模式与替换表达式重命名文件，支持正则、日期变量、计数器、UUID 和随机字符串。",
    "core_version": "2.0.0",
    "tags": ["rename"],
    "access": "read_write",
    "platforms": None,
    "scope": 1,
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "match": {
            "type": "str",
            "title": "匹配模式",
            "default": ".*",
            "description": "要匹配的文本或正则表达式 (启用正则匹配时)。",
        },
        "use_regex": {
            "type": "bool",
            "title": "使用正则匹配",
            "default": True,
            "description": "启用后，匹配模式将被视为正则表达式。",
        },
        "include_extension": {
            "type": "bool",
            "title": "包含文件拓展名",
            "default": False,
            "description": "启用后，重命名操作将包含文件后缀名。",
        },
        "replace": {
            "type": "str",
            "title": "替换为",
            "default": "",
            "description": (
                "替换表达式。支持 $Y/$M/$D/$y/$m/$d/$h/$t/$i/$s 日期令牌、"
                "${increment} 计数器、${ruuidv4} UUID、${rstringalnum} 随机字符串。"
                "$$ 转义为 $。"
            ),
        },
    },
}

_DATE_EXPANDERS: dict[str, Any] = {
    "Y": lambda dt: dt.strftime("%Y"),
    "M": lambda dt: dt.strftime("%m"),
    "D": lambda dt: dt.strftime("%d"),
    "y": lambda dt: dt.strftime("%y"),
    "m": lambda dt: str(dt.month),
    "d": lambda dt: str(dt.day),
    "h": lambda dt: dt.strftime("%H"),
    "t": lambda dt: dt.strftime("%I"),
    "i": lambda dt: dt.strftime("%M"),
    "s": lambda dt: dt.strftime("%S"),
}


def _expand_date_tokens(letters: str, dt: datetime) -> str:
    parts: list[str] = []
    for ch in letters:
        if ch in _DATE_EXPANDERS:
            parts.append(_DATE_EXPANDERS[ch](dt))
        else:
            parts.append("$" + ch)
    return "".join(parts)


def _parse_counter_params(params_str: str) -> dict[str, int]:
    result: dict[str, int] = {}
    positional_index = 0
    for part in params_str.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            key, val = part.split("=", 1)
            try:
                result[key.strip()] = int(val.strip())
            except ValueError:
                pass
        else:
            try:
                val = int(part)
            except ValueError:
                continue
            if positional_index == 0:
                result["increment"] = val
            elif positional_index == 1:
                result["padding"] = val
            elif positional_index == 2:
                result["start"] = val
            positional_index += 1
    return result


def _expand_compound(expr: str, counter_state: dict[tuple, int], dt: datetime) -> str:
    expr = expr.strip()

    if expr == "ruuidv4":
        return str(uuid.uuid4())

    if expr.startswith("rstringalnum"):
        length = 8
        if "=" in expr:
            try:
                length = int(expr.split("=", 1)[1].strip())
            except ValueError:
                pass
        chars = string.ascii_letters + string.digits
        return "".join(random.choices(chars, k=length))

    if expr.startswith("increment"):
        params = _parse_counter_params(expr.partition("=")[2] if "=" in expr else "")
        inc = params.get("increment", 1)
        pad = params.get("padding", 1)
        start = params.get("start", 1)
        key = (inc, pad, start)
        if key not in counter_state:
            counter_state[key] = start
        value = counter_state[key]
        counter_state[key] += inc
        return f"{value:0{pad}d}"

    return "${" + expr + "}"


def _expand_template(template: str, counter_state: dict[tuple, int], dt: datetime) -> str:
    result: list[str] = []
    i = 0
    n = len(template)

    while i < n:
        ch = template[i]
        if ch != "$":
            result.append(ch)
            i += 1
            continue

        # "$" at position i, look ahead
        if i + 1 >= n:
            result.append("$")
            i += 1
            continue

        next_ch = template[i + 1]

        if next_ch == "$":
            result.append("$")
            i += 2
            continue

        if next_ch == "{":
            # compound: ${...}
            close = template.find("}", i + 2)
            if close == -1:
                result.append("${")
                i += 2
                continue
            expr = template[i + 2 : close]
            result.append(_expand_compound(expr, counter_state, dt))
            i = close + 1
            continue

        # date token: $ followed by one or more date letters
        j = i + 1
        while j < n and template[j] in _DATE_EXPANDERS:
            j += 1
        if j > i + 1:
            letters = template[i + 1 : j]
            result.append(_expand_date_tokens(letters, dt))
            i = j
        else:
            result.append("$" + next_ch)
            i += 2

    return "".join(result)


def run(ctx: PipelineContext, cfg: dict[str, Any], runtime: PipelineRuntime) -> PipelineContext | None:
    match_pattern = cfg.get("match", "")
    use_regex = cfg.get("use_regex", False)
    include_extension = cfg.get("include_extension", False)
    replace_template = cfg.get("replace", "")

    dt = datetime.now()
    counter_state: dict[tuple, int] = {}

    files = sorted(ctx.files(recursive=False), key=lambda entry: entry.name.lower())
    if not files:
        runtime.log("rename-by-pattern", "hint", "没有可重命名的文件。")
        return ctx

    if use_regex and match_pattern:
        try:
            re.compile(match_pattern)
        except re.error as e:
            raise ValueError(f"正则表达式无效: {e}") from e

    renames: list[dict] = []
    renamed_count = 0
    for file_entry in files:
        source_path = file_entry.path
        if include_extension:
            target_str = file_entry.name
            suffix = ""
        else:
            target_str = source_path.stem
            suffix = source_path.suffix

        expanded = _expand_template(replace_template, counter_state, dt)

        if use_regex:
            new_stem = re.sub(match_pattern, expanded, target_str)
        else:
            if match_pattern:
                new_stem = target_str.replace(match_pattern, expanded)
            else:
                new_stem = expanded + target_str

        new_name = new_stem + suffix

        if new_name == file_entry.name:
            continue
        renamed = file_entry.rename(new_name)
        renamed_count += 1
        renames.append(
            {
                "from": str(source_path),
                "to": str(renamed.path),
                "from_name": source_path.name,
                "to_name": renamed.name,
            }
        )
        runtime.log(
            "rename-by-pattern",
            "success",
            f"{source_path.name} -> {renamed.name}",
            {"old": str(source_path), "new": str(renamed.path)},
        )

    if renames:
        existing = list(ctx.shared.get("renames", []))
        existing.extend(renames)
        ctx = ctx.clone(shared={**ctx.shared, "renames": existing})

    if renamed_count > 0:
        runtime.log(
            "rename-by-pattern",
            "message",
            f"重命名完成: {renamed_count} 个文件。",
            {"renamed": renamed_count},
        )
    else:
        runtime.log("rename-by-pattern", "hint", "文件名已符合目标模式，无需重命名。")

    return ctx
