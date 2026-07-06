# AGENTS.md — Shell Worker Platform

> **面向对象**：AI 编码代理（opencode）、项目贡献者。
> **定位**：自包含的架构参考。阅读本文档即可在不探索项目的前提下理解代码结构并开始修改。

---

## 1. 项目概述

| 项 | 值 |
|---|---|
| 项目名称 | Shell Worker Platform |
| 版本 | `CORE_VERSION = "2.0.0"`（`core/__init__.py`） |
| Python 要求 | 3.11+ |
| 入口 | `main_cli.py`（命令行，无 PySide6）+ `main_gui.pyw`（可选桌面） |
| 运行时核心依赖 | PyYAML |
| 可选 GUI 依赖 | PySide6 ≥ 6.9 |
| 可选 Windows PTY | pywinpty ≥ 2.0 |
| Linux/macOS PTY | stdlib `pty`（无第三方依赖） |
| 测试 | pytest ≥ 8.0 |

`core/` 与 GUI 完全解耦，**可在 Linux 无桌面服务器上以 CLI 模式运行**。`execute_workflow()` 纯函数可在无 GUI 环境下调用，模块通过 `run(ctx, cfg, runtime)` 接收 runtime 而非 GUI 句柄。

---

## 2. 系统设计

### 2.1 输入参数

系统通过 atom × scope × recurse 三个维度定义输入如何被切分和分发。

#### atom — 原子粒度

> atom 决定"一个处理单元是什么"。

| atom | 含义 | 输入来源 | 每个单元 |
|---|---|---|---|
| `file` | 文件原子 | `--files`（文件/文件夹） | 一个文件 |
| `folder` | 文件夹原子 | `--files`（仅文件夹，recurse=false 时 file workflow 由 executor 派生） | 一个文件夹整体 |
| `line` | 文本行原子 | `--lines` / `--lines-file` / stdin | 一行非空文本 |
| `none` | 空原子 | 无输入 | 1 个空单元 |

- `--recurse` 仅 atom=file 时有意义：
  - `recurse=true` → 文件夹输入被递归展开为内部文件的单元，保留 `source_root` 以维持相对路径复制语义。
  - `recurse=false` → 文件夹单元作为整体（folder 模式）。
- `--files` 覆盖 `--lines`；两者都没提供时 atom=none（空单元）。

#### scope — 上下文分发

> scope 决定"多少个输入共享一个处理上下文 (ctx)"。

| scope | 含义 | 单元构造 | ctx / EventBus 生命周期 |
|---|---|---|---|
| `1` (per-unit) | 每个输入 = 1 个独立任务 | 每个 file/folder/line 独立构造 ctx | 每 unit 独立 bus，独立 ctx.shared；下一个 unit 完全重置 |
| `0` (shared) | 全部输入合并为 1 个任务 | 所有输入复制进 `output_dir` 形成合并树，`working_path = output_dir` | 单个 ctx，单个 bus，模块内部 rglob 合并树自行遍历 |

scope 值 > 1 预留用于按批次截取 N 个输入为 1 任务（分片），当前视为 scope=1 执行。

### 2.2 核心设计逻辑

#### 单向管道：严格顺序执行

工作流中的步骤按 YAML 定义顺序**严格依次执行**，无分支、无循环。每个模块的 `run()` 返回值（ctx 或 None）直接传给下一个模块。执行流程：

```
模块 A.run(ctx, cfg, runtime) → ctx'
模块 B.run(ctx', cfg, runtime) → ctx''
模块 C.run(ctx'', cfg, runtime) → ...
```

#### scope 切分机制

- **scope=1 (per-unit)**：executor 遍历所有输入，为每个输入构造独立 `PipelineContext`，对每个 unit 依次执行全部 steps。不同 unit 之间 EventBus 完全隔离（通过 `runtime.replace_bus()`），ctx.shared 不跨 unit。
- **scope=0 (shared)**：executor 将所有输入复制进输出目录形成合并树，构造唯一一个 `PipelineContext`（`working_path = output_dir`），执行全部 steps。模块通过 `rglob` 自行遍历合并树。

#### 并发模型

- 单进程顺序执行，不内置并发调度。
- `LogSink` 设计为 Protocol，预留 multiprocessing.Pool transport 接口用于未来的并发场景。
- Engine 本身无全局状态锁，可在多进程或守护进程中复用。

#### 跨步骤数据传递

模块间的数据传递通过 `ctx.shared` 字典显式完成：
- 上游模块写入 `ctx.shared["key"]`。
- 下游模块读取 `ctx.shared.get("key")`。
- 不同 unit 间 ctx.shared 完全隔离，不串流。

### 2.3 目录结构

```
shell-worker/
├── AGENTS.md                    # 本文件
├── README.md
├── pyproject.toml              # 含 optional extras: gui / win / dev
├── requirements.txt
├── main_cli.py                 # argparse CLI 入口（无 PySide6）
├── main_gui.pyw                # 可选 PySide6 GUI 入口
│
├── core/                       # 内核（无 GUI 依赖）
│   ├── __init__.py             # 包级导出
│   ├── exceptions.py           # 统一异常层级
│   ├── events.py               # EventBus + LogSink (JSONLFileSink/NullSink/InMemorySink)
│   ├── context.py              # PipelineContext（业务对象，无控制流）
│   ├── runtime.py              # PipelineRuntime（EventBus + TerminalSessionRegistry + spawn + log sink + 取消信号）
│   ├── terminal.py             # 跨平台 PTY：winpty / pty / subprocess fallback
│   ├── input.py                # InputPlan 解析
│   ├── input_inspector.py      # GUI 前期路径校验（InputInspector，不展开目录）
│   ├── files.py                # WorkingCopier + 单元构建（path/lines/none/shared）
│   ├── config_schema.py        # 8 种参数类型校验
│   ├── module_manager.py       # 模块扫描，校验 atom + scope（含 VALID_ATOMS、VALID_SCOPES）
│   ├── workflow_loader.py      # YAML 加载/保存/校验（含 VALID_ATOMS、VALID_SCOPES）
│   ├── executor.py             # PipelineExecutor，scope=0/1 分发 + per-unit bus 隔离 + 取消检查
│   └── tools.py                # 公用模块助手（collect_file_targets 按 ctx.atom 路由）
│
├── gui/                        # 可选桌面层（PySide6）
│   ├── __init__.py
│   ├── main_window.py
│   ├── workflow_editor.py
│   ├── workflow_editor_state.py
│   └── widgets/
│       ├── __init__.py
│       ├── dynamic_form.py
│       ├── drop_zone.py
│       └── terminal_window.py
│
├── modules/                    # 示例模块
│   ├── verify_create_text_file.py     # none: 创建文件
│   ├── verify_rename_path.py          # file/folder: 重命名 + 写 ctx.shared["renames"]
│   ├── verify_write_summary.py        # file/folder/none: 读 ctx.shared["renames"] 写摘要
│   ├── cycle_counter.py               # file + scope=0: rglob 计数
│   ├── verify_line_echo.py            # line: 读 ctx.shared["input_line"]
│   └── verify_run_external_tool.py    # file: runtime.spawn (跨平台 PTY)
│
├── workflows/                  # 示例 YAML
│   └── example-*.yaml
│
├── resources/
│   ├── mock_tool.bat          # 验收与 demo 用 Windows
│   └── mock_tool.sh           # Linux/macOS
│
├── tests/                      # 单测（零 GUI 依赖）
│   ├── fixtures/
│   │   └── mock_module.py
│   ├── test_runtime.py
│   ├── test_executor.py
│   ├── test_terminal.py
│   ├── test_input.py
│   ├── test_files.py
│   ├── test_module_manager.py
│   ├── test_workflow_loader.py
│   ├── test_config_schema.py
│   └── test_cli.py
│
└── scripts/
    └── verify.py              # 端到端验收（subprocess 调 main_cli.py，跨平台）
```

---

## 3. 内核内部

### 3.1 执行管线

```
工作流 YAML ──→ WorkflowLoader.load() ──→ WorkflowDefinition (atom/scope/recurse)
                       │
                       ▼
                PipelineExecutor.execute(workflow, output_dir=..., InputPlan...)
                       │
              ┌────────┴─────────┬────────────────────┐
              ▼                  ▼                    ▼
        _prepare_steps      _build_units          per-unit：
        (校验 atom/scope    (path/line/none +    runtime.replace_bus()
         都对得上模块)         shared 树合并)       listener 自动迁移
              │                                       │
              ▼                                       ▼
        PreparedStep[]                         ctx = WorkingCopier
                                                  .prepare_*_unit(...)
              │                                       │
              └──────► for step in prepared_steps:
                         runtime.log("开始步骤 ...")
                         module.run(ctx, cfg, runtime)
                         ▼
                          runtime.spawn(["exiftool", ...])  ← 跨平台 PTY
                         ▼
                         TerminalSession.run()
                         ▼
                         bus.log("terminal:output", ...)  → 持久 listener → GUI 终端窗口
                         ▼
                         ctx = _resolve_step_result(...)   ← PipelineContext | None | dict["context"]
```

#### per-unit bus 隔离

- executor 在每个 unit 开始时调用 `runtime.replace_bus()`。新 bus 不含历史事件，但持久 listener 自动重新挂载到新 bus。GUI 用 `runtime.subscribe(callback)` 一次性订阅后能持续接收所有 unit 的事件流，但通过 `runtime.bus.iterate()` 看到的只是当前单元的事件。
- 持久 listener 是 runtime 的私有列表，clone() 不传播 events 与 listener。
- 任务间：shared 重置、events 隔离、TerminalSessionRegistry 内遗留 session 在 `runtime.close()` 统一清理。

#### shared scope（scope=0）

- 调用 `prepare_shared_path_unit(paths, recurse=...)`：把所有输入复制进 `output_dir` 形成合并树；构造单 `ctx`，`working_path = output_dir`。
- `direct_mode` + shared 不兼容：合并树需要拷贝落地，触发 `FileHandlingError`。
- 模块自行 rglob 合并树遍历（如 `cycle_counter`）。

### 3.2 PipelineContext

**`core/context.py`** — mutable slots dataclass，每个处理单元一个实例。模块通过它读路径、读 `shared`、追加 `extra_files`。控制面（事件、进程）由 `PipelineRuntime` 管理。

| 字段 | 类型 | 说明 |
|---|---|---|
| `original_input` | `Path \| None` | 原始输入路径（line/none 模式为 None） |
| `working_path` | `Path` | 当前工作副本路径 |
| `output_dir` | `Path` | 产物目录 |
| `atom` | `Atom` (`Literal["file","folder","line","none"]`) | 当前单元原子粒度 |
| `shared` | `dict[str, Any]` | 单 unit 内跨步骤共享数据 |
| `extra_files` | `list[Path]` | 已追踪的额外产出文件 |
| `source_root` | `Path \| None` | recurse=true 时保留相对路径的源根 |
| `is_file` | `bool` | working_path 是否为文件（自动计算） |
| `is_dir` | `bool` | working_path 是否为目录（自动计算） |

方法：
- `clone(**changes)` — 浅拷贝 + 字段覆盖；传入 `events` 会抛 `TypeError`。
- `track_extra_file(path)` — 追踪产物文件。

### 3.3 PipelineRuntime

**`core/runtime.py`** — 单次执行的控制平面对象。持有 EventBus + TerminalSessionRegistry + 取消/恢复信号 + log sink。模块以 `runtime` 参数接收，不从 context 获取。

| API | 用途 |
|---|---|
| `runtime.bus` | 当前 EventBus（可读/订阅） |
| `runtime.log(slug, type, text, data)` | 转发当前 bus |
| `runtime.subscribe(listener) -> unsubscribe` | 持久订阅：跨 `replace_bus()` 自动重新挂到新 bus（GUI 合约） |
| `runtime.replace_bus(*, sink=None) -> EventBus` | 切换 bus 用于 per-unit 隔离；持久 listener 自动迁移；返回旧 bus |
| `runtime.spawn(command, **opts) -> TerminalResult` | 跨平台 PTY spawn + 自动注册到 `runtime.sessions` |
| `runtime.sessions` | `TerminalSessionRegistry` 实例 |
| `runtime.request_cancel()` / `is_cancelled()` | 取消控制 |
| `runtime.set_resuming(bool)` / `is_resuming()` | 恢复控制（预留，executor 当前忽略） |

### 3.4 EventBus

**`core/events.py`** — 事件存储与分发。跨步骤数据通过 `ctx.shared` 显式传递，不依赖 EventBus。

| API | 用途 |
|---|---|
| `log(slug, type, text, data)` | 记录事件 |
| `subscribe(listener) -> unsubscribe` | 注册监听器 |
| `unsubscribe(listener)` | 移除监听器 |
| `iterate() -> Iterator[PipelineEvent]` | 遍历事件 |
| `has_errors()` | 供 executor 短路判断 |
| `reset()` | 清空事件 |

**Listener 异常隔离**：`log()` 中 listener 抛异常必须被 try/except 吞掉，只走 `LOGGER.exception`，绝不重新 raise。这是 GUI bug → pipeline 崩溃的唯一隔离层。

### 3.5 LogSink

```
LogSink(Protocol)
├── NullSink       (默认 no-op)
├── InMemorySink   (测试用)
└── JSONLFileSink  (CLI --log-file 持久化 / 断点续传)
```

LogSink 为 Protocol，可扩展新的 sink 实现。EventBus 在分发事件时同时写入当前绑定的 sink。

### 3.6 WorkflowDefinition

**`core/workflow_loader.py`** — frozen dataclass。

```python
@dataclass(frozen=True)
class WorkflowDefinition:
    meta: WorkflowMeta
    atom: str
    scope: int
    recurse: bool = False
    steps: tuple[WorkflowStep, ...]
    source_path: Path | None = None
```

**YAML 顶层字段**：`meta / atom / scope / recurse / steps`。

### 3.7 ModuleDefinition

**`core/module_manager.py`** — frozen slots dataclass。

```python
@dataclass(frozen=True, slots=True)
class ModuleDefinition:
    slug: str
    name: str
    core_version: str
    tags: tuple[str, ...]
    atom: tuple[str, ...]
    scope: int = 1
    parent: str | None = None
    description: str | None = None
    module_meta: dict
    config_schema: dict
    run: Callable
    path: Path
    module: types.ModuleType
```

`MODULE_META` 中 `atom: list[str]` 声明模块适配的原子粒度；`scope`（可选 int，默认 1）声明批处理模式，缺失或值非法时该模块被忽略。

---

## 4. 模块编写规范

### 4.1 模块三要素

每个 `modules/*.py` 必须导出：

```python
MODULE_META: dict = { "slug", "name", "core_version", "tags", "atom", "scope", ... }
CONFIG_SCHEMA: dict = { "type": "object", "properties": {...}, "required": [...] }

def run(ctx, cfg, runtime):
    ...
    return ctx            # 或 None 或 {"context": ctx}
```

### 4.2 MODULE_META 字段

```python
MODULE_META = {
    "slug": "my-module",          # str，必填，唯一标识
    "name": "My Module",          # str，必填
    "core_version": "2.0.0",      # str，必填
    "tags": ["foo", "bar"],       # list[str]，必填
    "atom": ["file", "folder"],   # list[str]，必填，合法值见 §2.1
    "scope": 1,                   # int，可选，默认 1，合法值 {0, 1}
    "parent": "other-slug",       # str | None，可选，编辑器中插父模块之后
    "description": "...",         # str，可选
}
```

### 4.3 run() 签名与返回值

```python
def run(ctx: PipelineContext, cfg: dict[str, Any], runtime: PipelineRuntime) -> PipelineContext | None | dict:
```

**返回值约束**：

| 返回 | 行为 |
|---|---|
| `PipelineContext` | 用新 context 继续后续步骤 |
| `None` | 保留原 ctx 继续步骤 |
| `dict` 含 `"context"` key | 提取 `result["context"]` 继续步骤 |
| 其他 | 抛 `PipelineExecutionError` |

### 4.4 跨步骤数据传递

上游模块把需要暴露给下游的数据写入 `ctx.shared`，下游用同契约读取。不同 unit 间 ctx.shared 隔离，互不串流。

**示例**：

```python
# 模块 A（上游）: 写入
def run(ctx, cfg, runtime):
    ctx.shared["renames"] = [
        {"from": "a.txt", "to": "a_renamed.txt"},
        {"from": "b.jpg", "to": "b_renamed.jpg"},
    ]
    return ctx

# 模块 B（下游）: 读取
def run(ctx, cfg, runtime):
    renames = ctx.shared.get("renames", [])
    for item in renames:
        runtime.log("my-slug", "info", f"{item['from']} → {item['to']}")
    return None
```

line atom 下，executor 自动把当前行注入 `ctx.shared["input_line"]`，模块直接读取。

### 4.5 调用外部程序

所有外部程序调用通过 `runtime.spawn(cmd)`，跨平台 PTY 或 subprocess fallback。

```python
def run(ctx, cfg, runtime):
    exe = cfg.get("exiftool_path") or ""
    tool = Path(exe) if exe else _default_path()
    if not tool.exists():
        runtime.log("my-slug", "error", f"未找到 {tool}")
        raise FileNotFoundError(str(tool))
    result = runtime.spawn([str(tool), str(ctx.working_path)])
    if not result.is_success:
        raise RuntimeError(f"exit={result.exit_code}")
    return ctx
```

跨平台实现：
- Windows → 优先 `pywinpty.PtyProcess`；不可用时 fallback subprocess。
- POSIX → stdlib `pty.fork()`；fallback subprocess。
- 全部平台的 stdout 经 EventBus 推送 `terminal:output` 事件，GUI / CLI log sink 自动接收。

### 4.6 模块示例参考

| 模式 | 文件 | 要点 |
|---|---|---|
| atom=none | `verify_create_text_file.py` | 最小 run()、`atom=["none"]`、`track_extra_file` |
| atom=file + per-unit | `verify_rename_path.py` | `clone(working_path=...)`、写 `ctx.shared["renames"]` |
| 读跨步骤合约 | `verify_write_summary.py` | 读 `ctx.shared["renames"]`、多事件类型、`track_extra_file` |
| atom=file + scope=0 | `cycle_counter.py` | rglob working_path 自行遍历 |
| atom=line + per-unit | `verify_line_echo.py` | 读 `ctx.shared["input_line"]` |
| 调用外部工具 | `verify_run_external_tool.py` | `runtime.spawn`；事件流；exit_code 决策 |

---

## 5. 工作流 YAML 语法

工作流 YAML 文件放在 `workflows/` 目录下，顶层字段固定：

```yaml
meta:
  slug: my-workflow          # 唯一标识
  name: My Workflow          # 显示名称
  description: ...          # 可选
atom: file                   # file | folder | line | none
scope: 1                     # 0 | 1
recurse: false               # 可选，仅 atom=file 时有效
steps:
  - module: my-module-slug
    params:
      key: value
      ...
  - module: another-module
    params: {}
```

**字段说明**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `meta.slug` | str | 是 | 工作流唯一标识 |
| `meta.name` | str | 是 | 显示名称 |
| `meta.description` | str | 否 | 描述 |
| `atom` | str | 是 | 原子粒度，须匹配所有步骤模块的 MODULE_META.atom |
| `scope` | int | 是 | 批处理模式，须匹配所有步骤模块的 MODULE_META.scope |
| `recurse` | bool | 否 | 默认 false，仅 atom=file 时对目录输入递归展开 |
| `steps[].module` | str | 是 | 模块 slug，须已注册 |
| `steps[].params` | dict | 是 | 模块参数，须符合该模块 CONFIG_SCHEMA |

步骤按数组顺序**严格依次执行**，无跳转、无循环、无条件。每个步骤的配置参数在 YAML 加载时即通过 CONFIG_SCHEMA 校验。

---

## 6. 测试

```bash
python -m pytest                          # 单测
python -m pytest --cov=core               # 覆盖率
python scripts/verify.py                  # 端到端验收（6 个工作流）
```

### 6.1 测试原则

- 不测试具体模块行为：tests/ 只测内核边界、模式行为、IO、核心实现。具体模块功能由 `verify.py` 端到端冒烟覆盖。
- 零 GUI 依赖：tests/ 不 import PySide6，可在 Linux CI 仅装 PyYAML 时跑通。
- 跨平台 PTY 测试：`test_terminal.py` 在 Linux 使用 `pty.fork()` 路径，在 win32 使用 winpty（缺失时自动 fallback subprocess，断言保持有效）。

### 6.2 测试文件覆盖

| 文件 | 覆盖 |
|---|---|
| `test_runtime.py` | EventBus 全 API + listener 异常隔离 + JSONL sink + Runtime 生命周期 + bus replace 时持久 listener 自动迁移 |
| `test_executor.py` | atom × scope 矩阵；per-unit bus 隔离；shared 合并树；shared+direct 拒收；cancel 在 step 边界；返回值合约非法拒绝；shared 跨步骤但不跨 unit |
| `test_terminal.py` | spawn 事件流、session 注册、跨平台 mock_tool 调用 |
| `test_input.py` | InputPlan 解析、files/lines 优先级、混合 file/dir 报错 |
| `test_files.py` | WorkingCopier 全套；shared 合并树；direct + shared 拒绝；folder 单元 |
| `test_module_manager.py` | atom/scope 校验；MODULE_META 缺字段；重复 slug；parent；rescan |
| `test_workflow_loader.py` | 新 YAML 拒绝 mode/batch；校验 atom/scope/recurse；保存往返 |
| `test_config_schema.py` | 8 种参数类型校验 |
| `test_cli.py` | argparse、退出码、`--list-*`、子进程运行示例、--lines 文本输入 |

---

## 7. 常用开发路径

| 需求 | 改哪里 |
|---|---|
| 新增 atom 类型 | `context.py:Atom`、`input.py`、`module_manager.py:VALID_ATOMS`、`workflow_loader.py:VALID_ATOMS`、`files.py` 加 prepare 方法 |
| 新增 scope 类型 | `module_manager.py:VALID_SCOPES`、`input.py`、`workflow_loader.py:VALID_SCOPES`、`executor._build_units` 加分支 |
| 新增参数类型 | `config_schema.py` 校验 + `gui/widgets/dynamic_form.py` 渲染 + `gui/workflow_editor_state.py:iter_schema_fields/_normalize_field_type` |
| 新增模块 | `modules/<name>.py` 按第 4 节规范 |
| 新增工作流 | `workflows/<name>.yaml` 按第 5 节规范 |
| 改 GUI 输入 UI 推导 | `gui/main_window.py:_update_input_controls` |
| 改 log 持久化 | `events.py:JSONLFileSink` 或新增实现 `LogSink` Protocol |
| 改跨平台 PTY | `terminal.py:_run_winpty / _run_posix_pty / _run_subprocess_fallback` |
