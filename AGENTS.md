# AGENTS.md — Shell Worker Platform

> **面向对象**：AI 编码代理（opencode）、项目贡献者。
> **定位**：自包含的架构参考。阅读本文档即可在不探索项目的前提下理解代码结构并开始修改。
> **修改时**：本文档记录项目当前状态，而非历史变更。更新时直接按现状改动，不使用"已移除"、"不再"、"新版"等增量表述。

---

## 1. 项目概述

| 项 | 值 |
|---|---|
| 项目名称 | Shell Worker Platform |
| 版本 | `CORE_VERSION = "2.0.0"`（`core/__init__.py`） |
| Python 要求 | 3.11+ |
| 入口 | `main.py`（命令行，无 PySide6）+ `main_gui.pyw`（可选桌面） |
| 运行时核心依赖 | PyYAML, watchdog ≥ 6.0, croniter ≥ 6.0 |
| 可选 GUI 依赖 | PySide6 ≥ 6.9 |
| 可选 Windows PTY | pywinpty ≥ 2.0 |
| Linux/macOS PTY | stdlib `pty`（无第三方依赖） |
| 测试 | pytest ≥ 8.0 |

`core/` 与 GUI 完全解耦，**兼容 Linux 无桌面服务器上以 CLI 模式运行**。`execute_workflow()` 纯函数可在无 GUI 环境下调用，模块通过 `run(ctx, cfg, runtime)` 接收 runtime 而非 GUI 句柄。

---

## 2. 系统设计

### 2.1 输入参数：输入模式 × scope × recurse

内核**根据实际输入自动推导执行形状**，不依赖 YAML 声明的硬约束。CLI 按 `--files` 优先、`--lines` 次之、皆空即"无输入"三层优先级识别输入模式。

#### 输入模式（内核内部，`InputPlan.kind`）

`InputPlan.kind` 仅供 executor + `ExecutionWorkspace` 用于构建单元，**不对外暴露为模块兼容性约束**。

| kind | 含义 | 输入来源 | 每个单元 |
|---|---|---|---|
| `path` | 路径输入 | `--files`（文件/文件夹，可混合） | 一个文件 或 一个文件夹整体（取决 `recurse`） |
| `line` | 文本行输入 | `--lines` / `--lines-file` / stdin | 一行非空文本 |
| `none` | 无输入 | 无 | 1 个空单元（`ctx.current = output_dir`） |

- `--recurse=true` → 文件夹输入被递归展开为内部文件单元，保留 `source_root` 以维持相对路径复制语义。
- `--recurse=false` → 文件夹保持整体单元；文件单元本身仍为单文件。混合文件/文件夹输入各自成单元。
- `--files` 覆盖 `--lines`；支持 `*`、`?`、`[]`、`**` 严格展开，未匹配模式报错；两者都没提供时 kind=none（空单元）。

> **YAML `atom` 字段为可选 GUI 元数据**。内核不读它做执行/兼容性判断；YAML 提供 `atom` 时仅用于：① GUI 选择运行期显示的输入面板（path/line/none），② GUI 编辑器按 `is_file_module` 过滤可用模块。省略 `atom` 等价于"内核按实际输入推导"。

#### scope — 上下文分发

> scope 决定"多少个输入共享一个处理上下文 (ctx)"。

| scope | 含义 | 单元构造 | ctx / EventBus 生命周期 |
|---|---|---|---|
| `1` (per-unit) | 每个输入 = 1 个独立任务 | 每个 path/line 独立构造 ctx | 每 unit 独立 bus，独立 ctx.shared；下一个 unit 完全重置 |
| `0` (shared) | 全部输入合并为 1 个任务 | 所有输入导入 `output_dir` 形成合并树 | 单个 ctx，单个 bus，模块读取 `ctx.files()` |
| `N > 1` (batched) | 每 N 个输入 = 1 个任务 | path 输入进入 `_batch_NNNN` 顶层工作区；line 输入形成 `input_lines` | 每 batch 独立 bus 与 ctx.shared |

> **scope 不作为模块兼容性的硬约束**：executor 不用 `workflow.scope != module.scope` 拒绝模块。模块可在任何 scope 下运行，模块通过 `ctx.current` 和 `ctx.files()` 判断并遍历本单元资源。

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
- **scope=0 (shared)**：executor 将所有输入导入最终 `output_dir` 工作区形成合并树，构造唯一一个 `PipelineContext`，执行全部 steps。发布不再二次复制；顶层重名条目整体改名。

#### 模块与内核解耦

模块**不关心内核的调度逻辑**（单文件 / 一组文件 / scope 值），只处理每次 ctx 传入的数据并传出：
- 模块用 `ctx.current.is_file` / `ctx.current.is_dir` 判定当前资源形态。
- 行输入模块读 `ctx.shared.get("input_line")`。
- 无输入模块通过 `ctx.create_file()` 创造文件。
- 模块通过 `ctx.current`/`ctx.files()` 获取本轮输入，不自行扫描其他 unit 的产物。
- `runtime.log(..., "error", ...)` 仅表示日志严重度；致命失败必须抛异常。
- `MODULE_META.is_file_module`（布尔）区分"path 处理模块"与"非 path 模块"（line/none/报告型），**仅用于 GUI 编辑器的模块过滤**，内核不校验。

#### 并发模型

- `PipelineExecutor` 是唯一单元执行引擎，内部使用 `ThreadPoolExecutor` 提供并发；scheduler 不复制步骤执行逻辑。
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
├── pyproject.toml              # 含 optional extras: gui / win / image / dev
├── main.py                     # argparse CLI 入口（无 PySide6）
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
│   ├── files.py                # ExecutionWorkspace + UnitWorkspace + WorkspaceFile + 单元构建
│   ├── config_schema.py        # 8 种参数类型校验
│   ├── module_manager.py       # 模块扫描，校验 is_file_module + scope（含 VALID_SCOPES）
│   ├── workflow_loader.py      # YAML 加载/保存/校验（atom 可选 GUI 元数据；含 VALID_SCOPES）
│   ├── executor.py             # PipelineExecutor，scope=0/1/N 分发 + unit bus 隔离 + 取消检查
│   ├── scheduler.py            # WorkflowScheduler：并发/监听/定时调度壳
│   └── tools.py                # 公用模块助手（collect_file_targets 按工作区清单路由）
│
├── gui/                        # 可选桌面层（PySide6）
│   ├── __init__.py
│   ├── launcher.py             # 安装后的 GUI 入口
│   ├── main_window.py
│   ├── workflow_editor.py
│   ├── editor/
│   │   ├── __init__.py
│   │   ├── info_tab.py
│   │   ├── state.py
│   │   └── steps_tab.py
│   └── widgets/
│       ├── __init__.py
│       ├── drop_zone.py
│       ├── dynamic_form.py
│       ├── input_panel.py
│       └── terminal_window.py
│
├── modules/                    # 示例模块（MODULE_META.is_file_module 区分 path / non-path）
│   ├── verify_create_text_file.py     # is_file_module=False: 无输入创建文件
│   ├── verify_rename_path.py          # is_file_module=True: 重命名 + 写 ctx.shared["renames"]
│   ├── verify_write_summary.py        # is_file_module=False: 读 ctx.shared["renames"] 写摘要
│   ├── cycle_counter.py               # is_file_module=True + scope=0: rglob 计数
│   ├── verify_line_echo.py            # is_file_module=False: 读 ctx.shared["input_line"]
│   └── verify_run_external_tool.py    # is_file_module=True: runtime.spawn (跨平台 PTY)
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
│   ├── test_scheduler.py
│   ├── test_cli.py
│   └── test_migration_repairs.py
│
└── scripts/
    └── verify.py              # 端到端验收（subprocess 调 main.py，跨平台）
```

---

## 3. 内核内部

### 3.1 执行管线

```
工作流 YAML ──→ WorkflowLoader.load() ──→ WorkflowDefinition (atom 可选/scope/recurse)
                       │
                       ▼
                PipelineExecutor.execute(workflow, output_dir=..., InputPlan...)
                       │
              ┌────────┴─────────┬────────────────────┐
              ▼                  ▼                    ▼
        _prepare_steps      _build_units          per-unit：
        (校验 CONFIG_SCHEMA) (path/line/none +    runtime.replace_bus()
                             shared 树合并)       listener 自动迁移
              │                                       │
              ▼                                       ▼
        PreparedStep[]                         ctx = ExecutionWorkspace
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

- 调用工作区的 shared unit 准备逻辑：把所有输入导入 `output_dir` 形成合并树；构造单 `ctx`，`ctx.current` 指向工作区根目录。
- `direct_mode` + shared 不兼容：合并树需要拷贝落地，触发 `FileHandlingError`。
- 模块通过 `ctx.files()` 获取合并树清单；内核在步骤边界 refresh。

### 3.2 PipelineContext

**`core/context.py`** — mutable slots dataclass，每个处理单元一个实例。所有真实文件操作由 `UnitWorkspace`/`WorkspaceFile` 提供，控制面由 `PipelineRuntime` 管理。

| 字段 | 类型 | 说明 |
|---|---|---|
| `original_input` | `Path \| None` | 原始输入路径（line/none 模式为 None） |
| `workspace` | `UnitWorkspace` | 当前单元的真实文件工作区，直接位于最终 `output_dir` |
| `shared` | `dict[str, Any]` | 单 unit 内跨步骤共享数据 |
| `source_root` | `Path \| None` | recurse=true 时保留相对路径的源根 |
| `current` | `WorkspaceFile` | 当前输入/目录资源 |

方法：`path/file/entries/files/directories/create_file/create_directory/allocate_file/adopt/read_text/read_bytes/write_text/write_bytes/copy/move/rename/delete/refresh/publish/clone`。
外部程序创建产物前先用 `allocate_file()` 取得不冲突的完整路径；由工具自行派生的产物通过 `adopt()` 加入清单，executor 在步骤边界调用 `refresh()`。

### 3.3 PipelineRuntime

**`core/runtime.py`** — 单次执行的控制平面对象。持有 EventBus + TerminalSessionRegistry + 取消/恢复信号 + log sink。模块以 `runtime` 参数接收，不从 context 获取。

| API | 用途 |
|---|---|
| `runtime.bus` | 当前 EventBus（可读/订阅） |
| `runtime.log(slug, type, text, data)` | 转发当前 bus |
| `runtime.subscribe(listener) -> unsubscribe` | 持久订阅：跨 `replace_bus()` 自动重新挂到新 bus（GUI 合约） |
| `runtime.replace_bus(*, sink=None) -> EventBus` | 切换 bus 用于 per-unit 隔离；持久 listener 自动迁移；返回旧 bus |
| `runtime.spawn(command, **opts) -> TerminalResult` | 跨平台 PTY spawn + 自动注册到 `runtime.sessions` |
| `runtime.start(command, **opts) -> TerminalSession` | 非阻塞启动实时会话，可 wait/write/terminate |
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
└── JSONLFileSink  (CLI --log / GUI 日志保存开关，自动写入 {output_dir}/{timestamp}_{slug}.jsonl)
```

LogSink 为 Protocol，可扩展新的 sink 实现。EventBus 在分发事件时同时写入当前绑定的 sink。

### 3.6 WorkflowDefinition

**`core/workflow_loader.py`** — frozen dataclass。

```python
@dataclass(frozen=True)
class WorkflowDefinition:
    meta: WorkflowMeta
    scope: int
    steps: tuple[WorkflowStep, ...]
    atom: str | None = None      # 可选 GUI 元数据，内核不读
    recurse: bool = False
    source_path: Path | None = None
```

**YAML 顶层字段**：`meta / scope / steps`（必填）、`atom / recurse`（可选）。`atom` 仅作 GUI 输入面板选择与编辑器模块过滤的元数据，内核按实际输入推导执行形状。

### 3.7 ModuleDefinition

**`core/module_manager.py`** — frozen slots dataclass。

```python
@dataclass(frozen=True, slots=True)
class ModuleDefinition:
    slug: str
    module_meta: dict
    config_schema: dict
    run: Callable
    path: Path
    module: types.ModuleType
    core_version: str = "2.0.0"
    tags: tuple[str, ...] = ()    # 当前仅作 GUI 提示，预留为筛选标记
    is_file_module: bool = True   # 区分 path 处理模块 / 非 path 模块（仅 GUI 过滤用）
    scope: int = 1                # GUI 提示用，内核不校验
    parent: str | None = None
```

`MODULE_META` 中 `is_file_module`（布尔，必填）区分该模块是否为 path 处理模块（`True`）或非 path 模块（`False`，如 line/none/报告型）；`scope`（可选 int，默认 1）仅作 GUI 提示，内核不校验。

### 3.8 WorkflowScheduler

**`core/scheduler.py`** — 调度壳，在 `PipelineExecutor` 之上叠加三个正交能力：

| 参数 | CLI flag | 含义 |
|------|----------|------|
| `concurrency` | `--concurrency N` / `-j N` | 并发 worker 数，1=串行（默认） |
| `watch` | `--watch` | 启用文件监听模式（仅 path 输入），通过 watchdog 检测文件变更并触发重执行 |
| `cron` | `--cron "*/5 * * * *"` | 标准 5 字段 cron 表达式，通过 croniter 驱动定时循环执行 |

三者可任意组合。当三个参数均为默认值时，调度器回退到直接的 `PipelineExecutor` 路径（零开销）。

**并发模型**：并发能力由 `PipelineExecutor` 统一实现。每个 worker 使用独立 EventBus、共享取消信号和终端会话注册表；结果按输入索引发布，`scope=0` 仅有一个 unit。

**监听模型**：监听启动后不处理已有文件，只收集新增、修改和移动目标。事件经 0.5 秒防抖及 size/mtime 稳定检测后组成变化批次，删除事件忽略。监听目录与输出目录必须完全不重叠。监听时拷贝开关开启为 COPY，关闭（CLI `--direct`）为 MOVE；移动批次失败时剩余文件仍发布到输出，避免丢失。

**定时模型**：`_run_cron_loop()` 使用 `croniter.croniter` 计算下次触发时间，`sleep` 等待后执行。

**日志路径**：调度器内部创建 `PipelineRuntime` 时若 `enable_log=True`，日志自动写入 `{output_dir}/{yyyyMMdd_HHmmss}_{slug}.jsonl`。并发模式下 worker 日志加 `_w{idx:04d}` 后缀区分。

---

## 4. 模块编写规范

### 4.1 模块三要素

每个 `modules/*.py` 必须导出：

```python
MODULE_META: dict = { "slug", "name", "core_version", "tags", "is_file_module", "scope", ... }
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
    "is_file_module": True,       # bool，必填；True=path 处理模块，False=line/none/报告型
    "scope": 1,                   # int，可选，默认 1，合法值 >= 0，仅 GUI 提示
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

**失败约束**：模块无法继续时直接抛异常；内核包装为带 module/step 信息的 `ModuleExecutionError`。记录 `error` 事件后正常返回不会改变执行结果。

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

line 输入模式（`InputPlan.kind == "line"`）下，executor 自动把当前行注入 `ctx.shared["input_line"]`，模块直接读取。

### 4.5 调用外部程序

外部程序可通过阻塞式 `runtime.spawn(cmd)`，或通过 `runtime.start(cmd)` 获取实时 `TerminalSession`。

```python
def run(ctx, cfg, runtime):
    exe = cfg.get("exiftool_path") or ""
    tool = Path(exe) if exe else _default_path()
    if not tool.exists():
        runtime.log("my-slug", "error", f"未找到 {tool}")
        raise FileNotFoundError(str(tool))
    result = runtime.spawn([str(tool), str(ctx.current.path)])
    if not result.is_success:
        raise RuntimeError(f"exit={result.exit_code}")
    return ctx
```

跨平台实现：
- Windows → 优先 `pywinpty.PtyProcess`；不可用时 fallback subprocess。
- POSIX → `pty.openpty()` + `subprocess.Popen(start_new_session=True)`。
- `TerminalSession` 统一提供 `start/wait/write/terminate`；缺失命令抛 `TerminalSpawnError`。
- `shell=True` 时 Windows 使用 `cmd.exe`，POSIX 使用 `/bin/sh -lc`；默认参数列表直接执行。
- 全部平台的 stdout 经 EventBus 推送 `terminal:output` 事件，GUI / CLI log sink 自动接收。

### 4.6 模块示例参考

当前 WorkspaceFile 契约由 `verify_*` 与 `cycle_counter` 示例模块覆盖。图集、FFmpeg、压缩、解档等业务模块仍使用旧路径字段，运行前需要按本节接口迁移。

| 模式 | 文件 | 要点 |
|---|---|---|
| 无输入 | `verify_create_text_file.py` | 最小 run()、`is_file_module=False`、`ctx.create_file()` |
| path + per-unit | `verify_rename_path.py` | `ctx.current.rename()`、写 `ctx.shared["renames"]` |
| 读跨步骤合约 | `verify_write_summary.py` | 读 `ctx.shared["renames"]`、通过工作区写摘要 |
| path + scope=0 | `cycle_counter.py` | `ctx.files()` 遍历工作区清单 |
| line + per-unit | `verify_line_echo.py` | 读 `ctx.shared["input_line"]` |
| 调用外部工具 | `verify_run_external_tool.py` | `runtime.spawn`；事件流；exit_code 决策 |

---

## 5. 工作流 YAML 语法

工作流 YAML 文件放在 `workflows/` 目录下，顶层字段固定：

```yaml
meta:
  slug: my-workflow          # 唯一标识
  name: My Workflow          # 显示名称
  description: ...          # 可选
atom: file                   # 可选 GUI 元数据：file | folder | line | none
scope: 1                     # 0 | 1 | N
recurse: false               # 可选，目录输入递归展开
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
| `atom` | str | 否 | 可选 GUI 元数据：file/folder/line/none。内核不读，仅用于 GUI 输入面板选择与编辑器模块过滤 |
| `scope` | int | 是 | 批处理模式 0/1/N；内核用 scope 切分单元 |
| `recurse` | bool | 否 | 默认 false，目录输入递归展开为内部文件单元 |
| `steps[].module` | str | 是 | 模块 slug，须已注册 |
| `steps[].params` | dict | 是 | 模块参数，须符合该模块 CONFIG_SCHEMA |

步骤按数组顺序**严格依次执行**，无跳转、无循环、无条件。每个步骤的配置参数在 YAML 加载时即通过 CONFIG_SCHEMA 校验。内核**不**用 `atom`/`scope` 校验模块兼容性。

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
- 跨平台 PTY 测试：Linux 覆盖 openpty/Popen，Windows 覆盖 winpty 与 subprocess fallback，共享同一会话契约。

### 6.2 测试文件覆盖

| 文件 | 覆盖 |
|---|---|
| `test_runtime.py` | EventBus 全 API + listener 异常隔离 + JSONL sink + Runtime 生命周期 + bus replace 时持久 listener 自动迁移 |
| `test_executor.py` | per-unit bus 隔离；shared 合并树；shared+direct 拒收；cancel 在 step 边界；返回值合约非法拒绝；shared 跨步骤但不跨 unit |
| `test_terminal.py` | spawn 事件流、session 注册、跨平台 mock_tool 调用 |
| `test_input.py` | InputPlan.kind 解析、files/lines 优先级、混合 file/dir 接受 |
| `test_files.py` | ExecutionWorkspace/UnitWorkspace/WorkspaceFile、清单与冲突命名 |
| `test_module_manager.py` | is_file_module/scope 校验；MODULE_META 缺字段；重复 slug；parent；rescan |
| `test_workflow_loader.py` | YAML 拒绝旧字段 mode/batch；atom 可选；recurse 独立于 atom；保存往返 |
| `test_config_schema.py` | 8 种参数类型校验 |
| `test_cli.py` | argparse、退出码、`--list-*`、子进程运行示例、--lines 文本输入 |
| `test_migration_repairs.py` | 异常失败、干净工作区、glob、监听批次、移动失败保护、异步 PTY |

---

## 7. 常用开发路径

| 需求 | 改哪里 |
|---|---|
| 新增输入模式 | `input.py:InputPlan.kind`、`files.py:units_from_plan/build_*_units` 加分支；`executor._build_units/_prepare_context` 路由 |
| 新增 scope 类型 | `module_manager.py:VALID_SCOPES`、`workflow_loader.py:VALID_SCOPES`、`executor._build_units` 加分支 |
| 新增参数类型 | `config_schema.py` 校验 + `gui/widgets/dynamic_form.py` 渲染 + `gui/editor/state.py:iter_schema_fields/_normalize_field_type` |
| 新增模块 | `modules/<name>.py` 按第 4 节规范 |
| 新增工作流 | `workflows/<name>.yaml` 按第 5 节规范 |
| 改 GUI 输入 UI 推导 | `gui/main_window.py:_update_input_controls` |
| 改 log 持久化 | `events.py:JSONLFileSink` 或新增实现 `LogSink` Protocol |
| 改跨平台 PTY | `terminal.py:TerminalSession` 与 `runtime.py:PipelineRuntime.start/spawn` |
