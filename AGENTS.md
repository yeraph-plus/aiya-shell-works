# AGENTS.md — Shell Worker Platform 开发者文档

> **面向对象**：AI 编码代理（opencode）、项目贡献者。
> **定位**：自包含的架构与约定参考。阅读本文档即可在不探索项目的前提下理解代码结构并开始修改。

---

## 1. 项目概述

| 项 | 值 |
|---|---|
| 项目名称 | Shell Worker Platform |
| 定位 | 用模块化工作流批量处理文件或执行爬虫/自动化任务的桌面平台 |
| 入口文件 | `main.py`（带 console 隐藏兼容） |
| 核心版本 | `CORE_VERSION = "1.0.0"`（定义于 `core/__init__.py:3`） |
| Python 要求 | 3.11+ |
| GUI 框架 | PySide6 ≥ 6.9 |
| 运行时依赖 | PySide6、PyYAML、pywinpty |
| 测试依赖 | pytest ≥ 8.0、pytest-cov ≥ 5.0 |

内核 (`core/`) 与 GUI (`gui/`) **完全解耦**。`core/` 不含任何 PySide6 引用，`execute_workflow()` 纯函数可在无 GUI 环境下调用。

---

## 2. 目录结构与文件职责

```
.
├── main.py                       # 应用入口：创建 QApplication，实例化 MainWindow
├── requirements.txt             # 运行与测试依赖
├── README.md                    # 面向用户的使用文档
├── AGENTS.md                    # 本文件：面向代理/开发者的架构文档
│
├── core/                        # 内核：无 GUI 依赖，纯 Python
│   ├── __init__.py              # 包导出 + CORE_VERSION
│   ├── pipeline.py              # PipelineContext, PipelineEventBus, PipelineEvent, PipelineMode/EventType
│   ├── handler_file.py          # FileHandler + FileHandlingError（文件/文件夹拷贝准备）
│   ├── handler_input.py         # InputHandler（input 模式单元与上下文构建）
│   ├── input_inspector.py       # InputInspector + ValidationResult（输入路径校验）
│   ├── terminal.py              # TerminalSession + TerminalResult（PTY 子进程封装）
│   ├── config_schema.py         # CONFIG_SCHEMA 校验 + 步骤参数标准化
│   ├── module_manager.py        # 模块扫描、校验、缓存
│   ├── workflow_loader.py       # YAML 工作流加载、保存、校验
│   └── executor.py              # PipelineExecutor + execute_workflow() 独立接口
│
├── gui/                         # 图形界面层
│   ├── __init__.py              # 导出 MainWindow, WorkflowEditor
│   ├── main_window.py           # 主窗口：工作流选择、输入、日志、进度、后台线程执行
│   ├── workflow_editor.py       # 工作流编辑器窗口：新建/打开/保存/另存为
│   ├── workflow_editor_state.py # 编辑器状态管理：WorkflowDraft, SchemaField, filter_modules
│   └── widgets/
│       ├── __init__.py          # 导出 DynamicParameterForm, DropZoneWidget
│       ├── dynamic_form.py      # 按 CONFIG_SCHEMA 动态生成参数表单控件
│       ├── drop_zone.py         # 拖拽输入区组件
│       └── terminal_window.py   # PTY 终端交互窗口（非模态对话框）
│
├── modules/                     # 可扫描的处理模块（每个 .py 文件为一个模块）
│   ├── rename_path.py           # 模块：文件/文件夹重命名（file, folder）
│   ├── write_summary.py         # 模块：写入工作流摘要（file, folder, none）
│   ├── create_text_file.py      # 模块：无输入创建文本文件（none）
│   ├── input_echo.py            # 模块：逐行回显文本（input）
│   ├── cycle_counter.py         # 模块：循环计数（cycle）
│   ├── delete_files.py          # 模块：按 glob 模式硬删除文件（file, folder）
│   ├── exiftool_clean.py        # 模块：ExifTool 清除 EXIF 元数据（file, folder）
│   ├── flatten_folder.py        # 模块：递归提取子文件夹文件到根目录（folder）
│   ├── gallery_count.py         # 模块：Gallery 风格统计计数标签（folder）
│   ├── gallery_rename.py        # 模块：Gallery 风格按类型分组建模重命名（folder）
│   ├── normalize_extensions.py  # 模块：标准化文件后缀大小写与变体（file, folder）
│   ├── strip_attributes.py      # 模块：清除只读/隐藏属性（file, folder）
│   └── unlock_files.py          # 模块：解除文件占用锁定（file, folder）
│
├── workflows/                   # YAML 工作流定义
│   ├── example-file-rename.yaml
│   ├── example-folder-rename.yaml
│   ├── example-none-generate.yaml
│   ├── example-input-echo.yaml
│   └── example-cycle-count.yaml
│
├── resources/                   # 外部工具目录（通过 install_*.ps1 脚本下载）
│   ├── install_exiftool.ps1      # 下载 ExifTool
│   ├── install_ffmpeg.ps1        # 下载 FFmpeg
│   ├── install_aria2.ps1         # 下载 aria2
│   ├── exiftool/.gitkeep
│   ├── ffmpeg/.gitkeep
│   └── aria2/.gitkeep
│
├── tests/                       # 测试套件
│   ├── test_executor.py         # PipelineExecutor 各模式测试
│   ├── test_module_manager.py   # ModuleManager 扫描/校验测试
│   ├── test_workflow_loader.py  # WorkflowLoader 加载/保存/校验测试
│   ├── test_file_handler.py     # FileHandler 和 PipelineEventBus 测试
│   ├── test_workflow_editor_state.py  # Editor state 逻辑测试
│   ├── test_flatten_folder.py   # FlattenFolder 模块集成测试
│   └── test_example_assets.py   # 端到端示例模块/工作流集成测试
│
└── ShellWorkerPlatform.spec      # PyInstaller 打包规格
```

---

## 3. 架构与数据流

### 3.1 执行主链路

```
YAML文件 ──→ WorkflowLoader.load() ──→ WorkflowDefinition（不可变）
                                            │
                                            ▼
                                    PipelineExecutor.execute()
                                            │
                    ┌───────────────────────┼──────────────────────┐
                    │                       │                       │
                    ▼                       ▼                       ▼
             ModuleManager            FileHandler            取消标志检查
           .get_module(slug)     prepare_context()          (threading.Event)
                    │            build_*_units()
                    ▼                       │
             ModuleDefinition                ▼
            (已验证的 run())           PipelineContext
                    │                 (每个单元一个)
                    ▼                       │
                run(ctx, config)  ◄──────────┘
                    │
          ┌────────┼────────┐
          ▼                  ▼
  ctx.clone()      ctx.run_command()
  (更新上下文)      (调用外部程序)
                         │
                         ▼
                  TerminalSession
                         │
                         ▼
                   PTY 子进程
                         │
                         ▼
                  TerminalWindow
                  (GUI 实时输出)
```
```

### 3.2 模块发现链路

```
modules/*.py ──→ ModuleManager.scan_modules()
                      │
                      ▼
                _load_module(Path)         # exec() 动态创建 ModuleType
                      │
                      ▼
                _validate_module(module)   # 校验 MODULE_META / CONFIG_SCHEMA / run()
                      │
                      ▼
                ModuleDefinition（frozen, slots）  # 写入 _modules_cache
```

### 3.3 GUI 线程模型

```
MainWindow（主线程）
    │
    ├── 信号/槽绑定
    ├── 点击「执行」
    │       │
    │       ▼
    │   ExecutionWorker(QObject).moveToThread(QThread)
    │       │
    │       ├── thread.started ──→ worker.run()
    │       │                         │
    │       │                    PipelineExecutor.execute()
    │       │                         │
    │       │                    event_callback ──→ Signal ──→ MainWindow._append_log()
    │       │                    progress_callback ──→ Signal ──→ MainWindow._handle_progress()
    │       │                         │
    │       │                    finished.emit(summary) ──→ MainWindow._handle_finished()
    │       │
    │       └── thread.finished ──→ _cleanup_worker()
    │
    └── 点击「停止」──→ worker.request_stop() ──→ _cancel_event.set()
```

---

## 4. 核心数据类型速查

### 4.1 PipelineContext（`core/pipeline.py:77`）

**Mutuable dataclass (slots)**，每个处理单元一个实例。在步骤间流转。

| 字段 | 类型 | 说明 |
|---|---|---|
| `original_input` | `Path \| None` | 用户提供的原始输入路径 |
| `working_path` | `Path` | 当前工作副本路径（模块在此操作） |
| `output_dir` | `Path` | 产物目录 |
| `mode` | `PipelineMode` | 工作流模式：`"file" \| "folder" \| "none" \| "cycle" \| "input"` |
| `shared` | `dict[str, Any]` | 跨模块/跨单元共享数据字典 |
| `extra_files` | `list[Path]` | 已追踪的额外产出文件列表 |
| `source_root` | `Path \| None` | 文件模式下用于保持相对路径的源根目录 |
| `events` | `PipelineEventBus` | 本单元事件总线 |
| `is_file` | `bool` | working_path 是否为文件（构造时自动计算） |
| `is_dir` | `bool` | working_path 是否为目录（构造时自动计算） |

关键方法：
- `clone(**changes)` — 浅拷贝并替换指定字段（模块更新上下文的标准方式）
- `track_extra_file(path)` — 追踪一个额外产出文件
- `run_command(command, *, cwd?, env?)` — 通过 PTY 子进程执行外部命令并返回 `TerminalResult`

### 4.2 PipelineEventBus（`core/pipeline.py:28`）

**每单元一个，生命周期跟随 PipelineContext。** 作为模块间信令和结构化日志的总线。

| 方法 | 说明 |
|---|---|---|
| `log(slug, event_type, text, data?)` | 记录一条事件，存入 `_events` 并**实时通知所有订阅监听器** |
| `subscribe(listener)` | 注册实时监听器，之后每次 `log()` 调用都会立即通知 |
| `unsubscribe(listener)` | 移除已注册的监听器 |
| `query(*, slug?, event_type?)` | 按 slug/类型过滤查询已有事件 |
| `has_errors()` | 检查是否存在 error 类型事件 |
| `reset()` | 清空所有事件 |

**支持迭代**：`for event in bus` / `len(bus)`

### 4.3 PipelineEvent（`core/pipeline.py:18`）

单条事件的不可变记录（slots dataclass）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `slug` | `str` | 发送事件的模块标识 |
| `type` | `PipelineEventType` | `"success" \| "message" \| "hint" \| "warning" \| "error"` |
| `text` | `str` | 事件描述文本 |
| `data` | `dict[str, Any]` | 附加结构化数据 |

### 4.4 ModuleDefinition（`core/module_manager.py:19`）

**Frozen slots dataclass**，一个已验证的模块。

| 字段 | 类型 | 说明 |
|---|---|---|
| `slug` | `str` | 模块唯一标识，如 `"rename-path"` |
| `module_meta` | `dict[str, Any]` | 原始 MODULE_META 字典副本 |
| `config_schema` | `dict[str, Any]` | 原始 CONFIG_SCHEMA 字典副本 |
| `run` | `Callable` | 模块入口函数 `run(ctx, config)` |
| `path` | `Path` | 模块源文件路径 |
| `module` | `ModuleType` | 动态创建的模块对象 |
| `core_version` | `str` | 兼容的核心版本号 |
| `tags` | `tuple[str, ...]` | 模块标签（用于编辑器中筛选） |
| `mode` | `tuple[str, ...]` | 模块适配的工作流模式列表 |
| `parent` | `str \| None` | 父模块 slug（自动排序依赖） |

### 4.5 WorkflowDefinition（`core/workflow_loader.py:65`）

**Frozen dataclass**，一份已通过校验的工作流。

| 字段 | 类型 | 说明 |
|---|---|---|
| `meta` | `WorkflowMeta` | 工作流元信息 |
| `mode` | `str` | 工作流模式 |
| `steps` | `tuple[WorkflowStep, ...]` | 步骤列表 |
| `source_path` | `Path \| None` | YAML 源文件路径 |

### 4.6 WorkflowMeta（`core/workflow_loader.py:25`）

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | `str` | 工作流名称 |
| `description` | `str` | 描述（默认空字符串） |
| `version` | `str` | 版本号（默认 `"1.0.0"`） |
| `slug` | `str` | 短标识（默认空字符串） |

### 4.7 WorkflowStep（`core/workflow_loader.py:46`）

| 字段 | 类型 | 说明 |
|---|---|---|
| `module` | `str` | 引用的模块 slug |
| `params` | `dict[str, Any]` | 步骤参数（映射到模块 CONFIG_SCHEMA） |
| `name` | `str` | 步骤显示名称 |

### 4.8 PreparedWorkflowStep（`core/executor.py:25`）

**Frozen slots dataclass**，运行时就绪的步骤，不同于 WorkflowStep 之处在于已解析 ModuleDefinition 和标准化参数。

| 字段 | 类型 |
|---|---|
| `index` | `int` |
| `name` | `str` |
| `module_slug` | `str` |
| `module_definition` | `ModuleDefinition` |
| `params` | `dict[str, Any]` |

### 4.9 WorkflowSummary（`core/workflow_loader.py:83`）

供 GUI 列表使用的轻量摘要。

| 字段 | 类型 | 说明 |
|---|---|---|
| `filename` | `str` | 文件名 |
| `name` | `str` | 工作流名称 |
| `mode` | `str` | 模式 |
| `step_count` | `int` | 步骤数 |
| `path` | `Path` | 文件路径 |
| `description` | `str` | 描述 |
| `is_valid` | `bool` | 是否通过校验 |
| `errors` | `tuple[str, ...]` | 校验错误 |

### 4.10 FileHandler（`core/handler_file.py:15`）

安全文件操作服务。构造时传入产物目录，支持两种模式。

| 构造参数 | 说明 |
|---|---|
| `output_dir` | 产物目录，copy 模式下自动创建 |
| `direct_mode=False` | **copy 模式**：拷贝到产物目录再操作 |
| `direct_mode=True` | **直写模式**：working_path 指向原始路径，不拷贝 |

| 方法 | 说明 |
|---|---|
| `build_file_units(paths)` | file 模式单元构建：文件直接列出，文件夹递归枚举并保持 source_root |
| `build_cycle_units(paths)` | cycle 模式单元构建：同 file 但不设 source_root |
| `build_folder_unit(path)` | folder 模式单元构建：文件夹本身作为一个单元 |
| `prepare_context(unit, mode, shared?, base_context?)` | 统一入口：按 mode 路由到正确的准备方法 |
| `prepare_none_context(shared?)` | 为 none 模式创建上下文 |
| `finalize_context(ctx, success)` | no-op，始终返回 False（平台不删除原始文件） |

### 4.11 异常体系

| 异常类 | 父类 | 文件位置 | 触发场景 |
|---|---|---|---|
| `FileHandlingError` | `RuntimeError` | `handler_file.py:12` | 文件复制/删除/路径校验失败 |
| `PipelineExecutionError` | `RuntimeError` | `executor.py:36` | 工作流设置无效、模块缺失、参数校验失败 |
| `PipelineCancelledError` | `RuntimeError` | `executor.py:40` | 用户取消执行 |
| `ConfigValidationError` | `_ConfigError(ValueError)` | `config_schema.py:30` | 步骤参数不符合 schema |
| `ConfigSchemaValidationError` | `_ConfigError(ValueError)` | `config_schema.py:23` | 模块 CONFIG_SCHEMA 本身不合法 |
| `WorkflowValidationError` | `ValueError` | `workflow_loader.py:15` | YAML 文档不符合工作流规范 |

### 4.12 InputHandler（`core/handler_input.py:11`）

**纯静态服务类**，为 input 模式构建处理单元和上下文。

| 方法 | 说明 |
|---|---|
| `build_units(lines)` | 将文本行列表转为 unit dict 列表（每行 `{"line": ...}`） |
| `prepare_context(line, output_dir, *, shared?)` | 为单行创建 PipelineContext，`shared["input_line"]` 包含该行文本 |

### 4.13 InputInspector（`core/input_inspector.py:18`）

**纯静态校验工具**，供 GUI 和执行器在正式处理前验证输入路径。

| 方法 | 说明 |
|---|---|
| `validate_file(path)` | 检查路径是否为存在的文件，返回 `ValidationResult` |
| `validate_directory(path)` | 检查路径是否为存在的目录，返回 `ValidationResult` |
| `validate_file_input(paths)` | 批量校验文件/文件夹路径，递归枚举文件夹内文件，返回 `(valid_paths, invalid_results)` |
| `validate_folder_input(path)` | folder 模式专用校验，等同于 `validate_directory` |
| `validate_text_input(text)` | 将多行文本按行拆分，去空行，返回行列表 |

**`ValidationResult`**（`input_inspector.py:9`）：frozen slots dataclass，字段 `path: Path`、`is_valid: bool`、`error: str`。

### 4.14 TerminalSession（`core/terminal.py:47`）

**PTY 子进程封装**，模块通过 `context.run_command()` 间接使用。

| 方法 | 说明 |
|---|---|
| `__init__(command, *, cwd?, env?, event_bus)` | 构造 session，`event_bus` 接收 stdout 流 |
| `run(timeout?)` | 阻塞执行命令，通过 `winpty.PtyProcess` 实时读取输出并写入 event_bus |
| `write(data)` | 向子进程 stdin 写入数据（TerminalWindow 调用） |
| `terminate()` | 终止子进程 |
| `exit_code` | 属性：进程退出码，运行中为 None |

**`TerminalResult`**（`terminal.py:29`）：frozen slots dataclass，字段 `exit_code: int`，属性 `is_success: bool`。

**`get_session(session_id)`**（`terminal.py:35`）：模块级函数，按 session ID 查找活跃 session（GUI 层调用）。

模块使用 `context.run_command()` 调用外部程序时，core 自动：
1. 创建 `TerminalSession`，cwd 默认 `context.output_dir`
2. 将输出流写入 `context.events`（事件类型为 `terminal:output`）
3. 阻塞等待子进程退出
4. 返回 `TerminalResult`

---

## 5. 五种工作流模式详解

| 模式 | 输入要求 | 单元构建 | 上下文特性 | 错误隔离 | 适用任务 |
|---|---|---|---|---|---|
| **file** | 文件或文件夹 | 每文件=1单元，目录展开保持 source_root | 独立上下文，relative path 复制 | 单单元失败继续 | 重命名、格式转换、元数据注入 |
| **folder** | 仅单个文件夹 | 整个文件夹=1单元 | 副本操作 | 整体失败 | 结构重组、批量缩略图、归档 |
| **none** | 无输入 | 1个单元，working_path=output_dir | 从零创建 | — | 报告生成、脚手架、配置写入 |
| **cycle** | 文件或文件夹 | 每文件=1单元，不设 source_root | 共享 shared + events 跨单元累积 | 单单元失败继续 | 计数统计、数据聚合、索引构建 |
| **input** | 多行文本 | 每非空行=1单元 | 每行独立，`shared["input_line"]` | 单行失败继续 | API 调用、URL 下载、逐行命令 |

**单元构建入口**：`executor.py:274-298`（`_build_units`）按模式路由到 `FileHandler.build_*_units()` 或 `InputHandler.build_units()`。
**上下文准备**：`executor.py:350-372`（`_prepare_context`）按 mode 路由到对应 handler。

---

## 6. 模块编写规范

### 6.1 模块三要素

每个 `modules/*.py` 文件是一个可被 `ModuleManager` 扫描的模块，**必须**导出以下三个对象：

```python
# 1. 模块元信息字典
MODULE_META: dict = { ... }

# 2. 参数配置 schema 字典
CONFIG_SCHEMA: dict = { ... }

# 3. 执行入口函数
def run(context, config):
    ...
    return context  # 或 None 或 {"context": context}
```

### 6.2 MODULE_META 字段详解

```python
MODULE_META = {
    "slug": "my-module",          # str, 必填。唯一标识，全英文小写 + 连字符
    "name": "My Module",          # str, 必填。显示名称
    "core_version": "1.0.0",      # str, 必填。兼容的内核版本号
    "tags": ["tag1", "tag2"],     # list[str], 必填。非空字符串标签，用于编辑器中筛选
    "mode": ["file", "folder"],   # list[str], 必填。适用工作流模式列表
    "parent": "other-module",     # str, 可选。父模块 slug，编辑器中自动插入到父模块之后
    "description": "...",         # str, 可选。模块功能描述
}
```

**mode 字段取值**：`"file"`、`"folder"`、`"none"`、`"cycle"`、`"input"` 中的任意组合。

### 6.3 CONFIG_SCHEMA 规范

```python
CONFIG_SCHEMA = {
    "type": "object",             # 固定值
    "properties": {
        "param_name": {
            "type": "str",        # 必填。int / float / str / bool / select / file_path / folder_path
            "title": "参数名称",   # 可选。GUI 标签文本
            "description": "...",  # 可选。说明文字（显示为 tooltip）
            "default": "value",    # 可选。默认值
            "required": True,      # 可选。是否必填
            "min": 0,              # 可选。int/float 最小值
            "max": 100,            # 可选。int/float 最大值
            "options": ["a", "b"], # select 类型必填。可选项列表
        },
        # ...更多参数
    },
    "required": ["param_name"],    # 可选。顶层必填参数名列表
}
```

**7 种参数类型**：

| 类型 | GUI 控件 | 值格式 |
|---|---|---|
| `int` | QSpinBox | 整数 |
| `float` | QDoubleSpinBox | 浮点数 |
| `str` | QLineEdit | 字符串 |
| `bool` | QCheckBox | 布尔值 |
| `radio` | QComboBox | options 中的值 |
| `select` | QComboBox | options 中的值 |
| `file_path` | QLineEdit + 浏览按钮 | 文件路径字符串 |
| `folder_path` | QLineEdit + 浏览按钮 | 文件夹路径字符串 |

**schema 校验位置**：`core/config_schema.py:37 validate_config_schema()` 在模块扫描时调用；`normalize_config_params()` 在执行时校验步骤参数并填充默认值。

### 6.4 run() 签名与返回值约定

```python
def run(context: PipelineContext, config: dict[str, Any]) -> PipelineContext | None | dict[str, Any]:
```

| 参数 | 说明 |
|---|---|
| `context` | 当前 PipelineContext 实例 |
| `config` | 已标准化并填充默认值的步骤参数字典 |

**返回值约束**（优先级从高到低）：

| 返回值 | 行为 |
|---|---|
| `PipelineContext` | 使用该新上下文继续后续步骤 |
| `None` | 保留原上下文继续（原位修改） |
| `dict` with `"context"` key | 提取 `result["context"]` 作为新上下文 |
| 其他 | 抛出 `PipelineExecutionError` |

**推荐方式**：使用 `context.clone(**changes)` 返回新上下文，不改动原对象。通过 `context.events.log(...)` 写入事件，通过 `context.track_extra_file(...)` 追踪产物。

### 6.5 模块类型与工作流模式的匹配规则

**规则**：执行时，工作流的 `mode` 必须属于模块 `MODULE_META["mode"]` 列表。

**校验位置**：
1. **模块扫描阶段**（`module_manager.py:180-188`）：mode 字段必须是非空列表且值合法
2. **编辑器可用模块筛选**（`workflow_editor_state.py:61`）：`active_mode not in definition.mode` 则排除
3. **执行时参数校验**（`executor.py:205-241`）：步骤的模块 slug 通过 ModuleManager 解析，能加载即表示模块已通过扫描校验

### 6.6 各类型模块适配的任务场景

| 模式 | 说明 | 典型任务 |
|---|---|---|
| file | 处理单个文件副本，从 `working_path` 读取并操作 | 文件重命名、格式转换、图片缩放、元数据注入、水印、哈希计算 |
| folder | 处理整个文件夹副本，可修改结构或生成子文件 | 文件夹重命名、批量缩略图、归档打包、结构重组 |
| none | 无需输入，从零创建内容到产物目录 | 文本文件生成、报告/摘要、项目脚手架、配置生成 |
| cycle | 多文件共享累计状态，通过 `shared` 跨文件传递 | 文件计数、数据聚合、特征索引、目录清单、配对检查 |
| input | 逐行文本输入，每行独立上下文 | 逐行 API 调用、批量 URL 下载、逐行命令执行、CSV 行处理 |

### 6.7 完整模块编写示例

参照内置模块源码（均为独立可运行的完整示例）：

| 模式 | 参考文件 | 要点 |
|---|---|---|
| file | `modules/rename_path.py` | `working_path.rename()` 后 `clone(working_path=...)` 更新上下文 |
| none | `modules/create_text_file.py` | 从 `output_dir` 创建文件，用 `track_extra_file()` 追踪产物 |
| cycle | `modules/cycle_counter.py` | 通过 `shared["cycle_count"]` 跨单元累加，`clone(shared={...})` 传递 |
| input | `modules/input_echo.py` | 从 `shared["input_line"]` 读取当前行，写入独立文件 |

核心模式：`clone(**changes)` 返回新上下文 → `events.log(...)` 写事件 → `track_extra_file(...)` 追踪产物。

---

## 7. 事件总线与日志系统

### 7.1 架构

```
模块 run() ──→ ctx.events.log(slug, type, text, data)
                      │
                      ├─► 存入 PipelineEventBus._events
                      │         │
                      │         └─► 遍历 _listeners，实时通知已订阅的监听器
                      │                    │
                      │              executor.event_callback ──→ ExecutionWorker._on_executor_event()
                      │                                                 │
                      │                                           Signal.emit()
                      │                                                 │
                      │                                         MainWindow._append_log()
                      │                                                 │
                      │                                           QPlainTextEdit 显示
                      │
                      ├─► executor._run_unit() 在步骤间自动 log 开始/完成事件
                      │        （同样通过事件总线实时转发到 GUI）
                      │
                      ├─► ctx.run_command() ──→ TerminalSession ──→ event_bus.log("terminal:output", ...)
                      │                                              │
                      │                                    TerminalWindow (GUI 实时终端)
                      │
                      └─► ctx.events.query(slug, type) ◄── 下游模块可查询上游事件
```

**实时订阅模型**：执行器在每个处理单元开始时把 `event_callback` 订阅到当前 `context.events`，
并在单元结束后取消订阅。因此模块内部任何 `context.events.log(...)` 调用都会**立刻**到达 GUI，
无需等待步骤结束再批量补发。

### 7.2 事件类型与 GUI 前缀映射

| PipelineEventType | GUI 前缀 | 语义 |
|---|---|---|
| `success` | `[OK]` | 步骤/单元成功完成 |
| `message` | `[INFO]` | 普通信息 |
| `hint` | `[HINT]` | 提示信息 |
| `warning` | `[WARN]` | 警告 |
| `error` | `[ERROR]` | 错误（但不一定阻断执行） |

### 7.3 Phase 级日志

`ModuleManager` 使用标准 Python `logging` 记录扫描阶段的警告（`LOGGER.warning(...)` at `module_manager.py:235`）。这些警告也被收集进 `ModuleManager._warnings` 列表，并在执行开始时通过 `executor._emit_event("executor", "warning", ...)` 回放给用户。

### 7.4 PipelineContext 中的 events 生命周期

- **file / folder / none 模式**：每个单元一个独立 PipelineEventBus，单元间互不干扰。执行器在每个单元开始时将 `event_callback` 订阅到该总线，单元结束后取消订阅。
- **cycle 模式**：第一个单元新建 PipelineEventBus，后续所有单元共享同一个（通过 `context.clone(events=base_context.events)`）。执行器的订阅跟随上下文转移，所有文件的事件累积在该总线中并实时转发给 GUI。

---

## 8. GUI 架构

### 8.1 MainWindow（`gui/main_window.py:184`）

主窗口纵向排列三个区块：
- **执行配置区**：工作流下拉选择（仅显示名称）+ 刷新/编辑按钮；下方三行标签分别显示模式、步骤数/文件、描述文本；产物目录（QSettings 持久化，重启保留）；删除原文件复选框；执行/停止按钮
- **输入区**（中部）：拖拽区（3px 虚线边框）、文本编辑器（input 模式）、输入列表（每项前缀 `[等待]/[处理中]/[完成]/[失败]` 徽章）+ 添加/移除按钮
- **日志区**（底部，stretch=1）：QTextEdit HTML 格式日志面板；错误→红色、警告→橙色、提示→灰色；INFO/SUCCESS 级别默认隐藏
- **状态栏**：QProgressBar
- **窗口默认尺寸**：680×920（竖直长方形）

**模式感知输入控制**（`main_window.py:422-454`）：

| 模式 | 拖拽区 | 文件列表 | 添加文件 | 添加文件夹 | 文本编辑器 |
|---|---|---|---|---|---|
| file | 显示 | 显示 | 启用 | 启用 | 隐藏 |
| folder | 显示 | 显示 | 禁用 | 启用 | 隐藏 |
| none | 隐藏 | 隐藏 | 隐藏 | 隐藏 | 隐藏 |
| cycle | 显示 | 显示 | 启用 | 启用 | 隐藏 |
| input | 隐藏 | 隐藏 | 隐藏 | 隐藏 | 显示 |

### 8.2 ExecutionWorker（`gui/main_window.py:36`）

执行线程的载体 QObject：
- 使用 `threading.Event()` 作为取消标志
- `run()` slot 在 thread.start 时触发
- 通过 `event_callback` 和 `progress_callback` 将 PipelineEvent 转为 Signal 发射回主线程
- 多输入时，逐个调用 `executor.execute()` 并聚合结果
- `finished` signal 携带最终汇总字典

### 8.3 WorkflowEditor（`gui/workflow_editor.py:80`）

独立编辑窗口。布局：
- **工具栏**：新建、打开、副本（直接生成 `*-副本.yaml` 无弹窗）、保存、另存为
- **元信息区**：名称输入、模式只读标签（新建时通过 `_ModeDialog` 弹窗选定，编辑时不可更改）、描述文本框
- **模块穿梭框**：左侧可用模块列表（固定宽度 260px，标签以 `[tag]` 纯文本显示）→ 中间添加/移除/上移/下移按钮 → 右侧步骤配置区（单一边框内步骤列表 260px + 步骤详情表单左右排布）
- **步骤详情区**：步骤名称输入、模块 slug 显示、步骤描述、DynamicParameterForm
- **模式弹窗** `_ModeDialog`：新建工作流时弹出，列出 5 种模式及说明，选择后不可更改

**WorkflowDraft**（`workflow_editor_state.py:185`）是编辑器的可变状态容器，操作包括 `add_step`（自动处理 parent 属性插入）、`remove_step`、`move_step`、`update_step_name`、`update_step_params`。

### 8.4 DynamicParameterForm（`gui/widgets/dynamic_form.py:29`）

根据 `iter_schema_fields()` 输出的 `SchemaField` 列表动态创建表单行。7 个分支对应 7 种参数类型。`values_changed` signal 在任意控件值变更时发射。

### 8.5 TerminalWindow（`gui/widgets/terminal_window.py:21`）

**非模态对话框**，显示 PTY 子进程的实时输出并允许用户通过输入行与之交互。

| 功能 | 说明 |
|---|---|
| 输出流 | 监听事件总线 `terminal:output` 事件，信号驱动更新 QPlainTextEdit |
| 输入 | QLineEdit 回车或「发送」按钮将文本写入 `TerminalSession.write()` |
| 终止 | 「终止进程」按钮调用 `TerminalSession.terminate()` |
| 完成 | 进程退出后禁用输入控件，显示退出码 |
| 外观 | 暗色主题，Consolas 10pt 等宽字体 |

**创建时机**：GUI 层检测到事件总线中出现 `terminal:started` 事件（session_id、command 作为 data），自动创建并显示 TerminalWindow。

---

## 9. 代码约定

| 约定 | 说明 |
|---|---|
| `from __future__ import annotations` | **所有 Python 文件必须声明**，支持延迟求值类型标注 |
| dataclass 优先 | 数据结构用 dataclass；不可变类型用 `frozen=True, slots=True` |
| 中文业务日志 | 异常消息、事件文本、GUI 标签使用中文（面向中文用户的桌面应用） |
| Path 路径统一 | 所有文件系统操作使用 `pathlib.Path`，不接受裸字符串 |
| 类型标注 | 完整类型标注，兼容 mypy |
| 无 GUI 依赖 core | `core/` 不得导入 PySide6 任何内容 |
| 文件路径参数传递 | GUI 层使用字符串，core 层内部使用 Path 对象 |
| 错误不吞没 | 单个单元失败记录日志后继续，不因此终止整个工作流 |

---

## 10. 工作流 YAML 格式速查

### 10.1 file 模式示例（`example-file-rename.yaml`）

```yaml
meta:
  name: "File Rename Example"
  description: "Rename file copies and generate a summary."
  version: "1.0.0"
mode: file
steps:
  - module: rename-path
    name: 重命名文件
    params:
      prefix: ""
      suffix: "_renamed"
  - module: write-summary
    name: 生成摘要
    params:
      filename: "summary.txt"
      title: "File Workflow Report"
```

### 10.2 folder / none / input / cycle 模式示例

其余模式仅在 `mode` 字段和步骤配置上有差异，完整示例参考 `workflows/example-*.yaml`：

| 文件 | mode | 关键差异 |
|---|---|---|
| `example-folder-rename.yaml` | folder | 单个文件夹输入，步骤与 file 相同 |
| `example-none-generate.yaml` | none | 无输入路径，`create-text-file` 从零生成文件 |
| `example-input-echo.yaml` | input | 无文件路径，用 `input-echo` 处理文本行 |
| `example-cycle-count.yaml` | cycle | `cycle-counter` 通过 `shared` 跨文件累计统计 |

---

## 11. 外部工具与打包

### 11.1 外部工具目录

`resources/` 存放模块可能调用的外部程序（exiftool、ffmpeg、aria2 等）。每个工具一个子目录。

通过 PowerShell 脚本从官方源下载：

```powershell
powershell -File resources/install_exiftool.ps1  # 单独安装
```

下载后的 `.exe` 文件通过 `.gitignore` 排除，不提交到仓库。

### 11.2 模块调用外部工具的模式

需要外部工具的模块应在 `CONFIG_SCHEMA` 中定义工具路径参数。推荐通过 `context.run_command()` 调用（自动 PTY 子进程、输出实时写入事件总线、GUI 可开 TerminalWindow）：

```python
def run(context, config):
    exe = config.get("exiftool_path", "").strip()
    if not exe:
        return context
    result = context.run_command([exe, str(context.working_path)])
    if not result.is_success:
        raise RuntimeError(f"ExifTool 返回 {result.exit_code}")
    return context
```

使用 `subprocess.run()` 直接调用则输出不出现在 GUI 日志中。

### 11.3 PyInstaller 打包

```bash
pip install pyinstaller
pyinstaller ShellWorkerPlatform.spec  # 产物: dist/ShellWorkerPlatform/
```

`ShellWorkerPlatform.spec` 为 `--onedir` 模式，将 `resources/`、`modules/`、`workflows/` 作为数据文件分发。打包前需在 `resources/` 中预下载外部工具 `.exe`。

---

## 12. 测试

### 运行命令

```bash
# 全部测试
python -m pytest

# 带覆盖率
python -m pytest --cov=core --cov=gui --cov=modules --cov-report=term-missing
```

### 测试文件覆盖点

| 文件 | 覆盖内容 |
|---|---|---|
| `test_executor.py` | file/folder/none/cycle/input 模式执行、错误隔离、取消执行、参数校验失败、folder 仅接受文件夹 |
| `test_module_manager.py` | 合法模块加载、无效模块忽略（缺字段/非法类型/无 CONFIG_SCHEMA/无 run）、缓存和重新扫描、重复 slug、tag 排序 |
| `test_workflow_loader.py` | YAML 加载、校验错误捕捉、保存往返、无效工作流列举、new_workflow 模板、路径越界防护 |
| `test_file_handler.py` | none 上下文、文件复制（相对路径、重名处理）、文件夹复制（重名）、finalize_context（成功/失败/路径相同保护）、PipelineEventBus（log/query/reset/迭代）、PipelineContext clone 和默认值 |
| `test_workflow_editor_state.py` | iter_schema_fields、normalize_params、filter_modules（tag AND 逻辑、mode 筛选）、WorkflowDraft（添加/移除/移动/parent 感知插入/导出） |
| `test_flatten_folder.py` | FlattenFolder 模块集成测试（subfolder_first 优先级排序、prefix 编号规则） |
| `test_example_assets.py` | 内置示例模块可发现、工作流可加载、file/folder/none 模式端到端执行 |

---

## 13. 常用开发路径索引

| 需求 | 关键文件 | 参考行号 |
|---|---|---|
| 新增工作流模式 | `pipeline.py:10`, `executor.py:243-295`, `executor.py:297-343`, `workflow_loader.py:11` | 四处 mode 定义需同步 |
| 新增参数类型 | `config_schema.py:9`, `config_schema.py:197-242`, `editor_state.py:13`, `dynamic_form.py:96-163` | 校验 + GUI 渲染需同步 |
| 新增模块文件 | `modules/*.py` | 按第 6 节规范编写 |
| 修改执行逻辑 | `executor.py` | `_build_units`, `_prepare_context`, `_run_unit` |
| 修改文件处理逻辑 | `handler_file.py` | `prepare_context`, `build_*_units`, `finalize_context` |
| 修改 GUI 输入策略 | `main_window.py:422-454` | `_update_input_controls` |
| 修改工作流编辑器表单 | `dynamic_form.py:44-68`, `editor_state.py:74-121` | `set_schema`, `iter_schema_fields` |
| 修改日志/事件格式 | `pipeline.py:18-74`, `main_window.py:163` | PipelineEvent + GUI 前缀映射 |
| 调用外部程序 | `terminal.py`, `pipeline.py:124-144` | TerminalSession, PipelineContext.run_command |
| 添加终端 GUI | `terminal_window.py:21` | TerminalWindow |

---

## 14. 设计约束与防退化清单（代码审查结论）

以下约束源于近期代码审查中发现的潜在问题。每一项都是**不可退化**的设计保证，
修改相关代码时必须保持对应的行为不变。

### 14.1 GUI 输入路径校验：预展开与语义保持

**约束**：GUI 层对 file / cycle 模式的输入路径校验**不得预展开目录**。
目录展开（递归枚举内部文件）必须延迟到 `FileHandler.build_file_units()` 中执行，
否则将丢失 `source_root` 信息，破坏 file 模式相对路径复制语义。

| 组件 | 正确用法 |
|---|---|
| GUI `_add_input_paths()` | file / cycle 模式调用 `InputInspector.validate_path_input()` — 保留目录原样 |
| Executor `_build_units()` | 调用 `FileHandler.build_file_units()` — 在此展开目录并设置 `source_root` |
| `InputInspector.validate_file_input()` | 仅用于不需要 `source_root` 的场景或测试中，**GUI 严禁在 file 模式下调用此方法** |

**关键代码路径**：
- `gui/main_window.py:622-663` — `_add_input_paths()` 分支：folder 用 `validate_folder_input`，其余用 `validate_path_input`
- `core/input_inspector.py:74-96` — `validate_path_input()`：只校验存在性，不展开
- `core/input_inspector.py:46-72` — `validate_file_input()`：会递归展开目录，仅用于不需要 source_root 的场景
- `core/handler_file.py:42-58` — `build_file_units()`：目录展开 + `source_root` 设置的正确位置

### 14.2 cycle 模式多输入的共享上下文传递

**约束**：cycle 模式的核心语义是**所有输入文件共享同一个 PipelineContext**（包括 `shared` 字典和 `PipelineEventBus`，
实现跨文件计数/聚合）。GUI 层必须将**全部输入路径一次性传给** `executor.execute(input_paths=...)`，而非逐路径调用。

| 模式 | GUI 调用方式 | 理由 |
|---|---|---|
| file | 逐路径循环 `executor.execute(input_path=path)` | 每个文件独立上下文 |
| cycle | **一次性** `executor.execute(input_paths=self.input_paths)` | 共享上下文 |

Executor 内部通过以下机制实现共享：
1. `executor.py:114-161` — `shared_context` 变量在循环中累积：模式为 cycle 时 `final_context` 赋值给 `shared_context` 供下一个单元继承
2. `handler_file.py:155-169` — `_prepare_cycle_unit()`：若 `base_context` 非 None，则 `clone(shared=base_context.shared, events=base_context.events, extra_files=...)`

**退化风险**：若将来有人在 GUI 中把 cycle 也改成逐路径循环，cycle 模块（如 `cycle_counter`）的 `shared["cycle_count"]` 将永远为 1。

**关键代码路径**：
- `gui/main_window.py:118-143` — cycle 分支：单次 `executor.execute(input_paths=...)`
- `gui/main_window.py:144-168` — file 分支：逐路径循环（对比参考）

### 14.3 output_dir 创建与 direct_mode 解耦

**约束**：`output_dir`（产物目录）的创建**不受 `direct_mode` 影响**。即使 `direct_mode=True`（不拷贝文件），
模块仍可能通过 `context.track_extra_file()` 生成 sidecar 文件（摘要、日志、报告），需要产物目录作为落地位置。

`FileHandler.__init__()` 中的 `self.output_dir.mkdir(parents=True, exist_ok=True)` 必须在构造时**无条件执行**。

| `direct_mode` | 文件拷贝 | output_dir 创建 |
|---|---|---|
| `False` | 是 | 是 |
| `True` | 否 | **是**（为 extra files 保留容身之所） |

**关键代码路径**：
- `core/handler_file.py:28-36` — `__init__`：`mkdir` 在 `direct_mode` 分支之外，无条件执行
- `gui/main_window.py:691-703` — `_resolve_output_dir()`：直接模式未指定产物目录时自动取第一个输入路径的父目录

### 14.4 文件锁定探测：原子操作要求

**约束**：`unlock_files` 模块的文件锁定检测**必须使用 Windows Kernel32 `CreateFileW` API** 进行原子探测，
不得使用重命名、移动、或任何会修改文件系统状态的非原子操作。

**原问题**：重命名探测（rename → rename-back）在以下场景存在风险：
- 进程崩溃在两次重命名之间 → 文件永久留在错误的临时名
- 对只读文件会返回"锁定"误报（实际是权限不足）
- 触发文件系统监听器，引发不必要的副作用

**正确实现（`modules/unlock_files.py:54-69`）**：

```python
def _is_locked(path: Path) -> bool:
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateFileW.restype = ctypes.c_void_p
    handle = kernel32.CreateFileW(
        str(path),
        GENERIC_READ | GENERIC_WRITE,  # dwDesiredAccess
        0,                              # dwShareMode = 0 → 独占
        None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None,
    )
    if handle == INVALID_HANDLE_VALUE:
        return True                     # 文件被锁定
    kernel32.CloseHandle(ctypes.c_void_p(handle))
    return False
```

**退化检查点**：任何人修改此模块时，搜索 `_is_locked` 函数体，确保不含 `rename`、`replace`、`MoveFile` 调用。

### 14.5 事件总线监听器异常隔离

**约束**：`PipelineEventBus.log()` 中对外部监听器（如 GUI 事件回调）的调用**必须做防御性 try/except 隔离**。
监听器属于观测层，其异常不得反向传播到执行层的模块逻辑中。

**设计原理**：

```
模块 run() ──log()──→ PipelineEventBus ──listeners──→ GUI callback（观测层）
                │                                         │
                │  ←── try/except 防火墙 ←──── 异常抛出 ──┘
                │
                ▼
            正常返回（执行层不受影响）
```

**实现（`core/pipeline.py:37-51`）**：

```python
def log(self, slug, event_type, text, data=None):
    event = PipelineEvent(...)
    self._events.append(event)
    for listener in list(self._listeners):        # 快照拷贝迭代
        try:
            listener(event)
        except Exception:                          # 防御性隔离
            LOGGER.exception("Pipeline event listener failed: %r", listener)
    return event                                    # 始终正常返回
```

关键保护点：
1. `list(self._listeners)` — 快照拷贝，防止遍历期间 listener 自我取消订阅导致 RuntimeError
2. `try/except Exception` — 捕获监听器所有运行时异常，只记录日志，**永不重新抛出**

这个隔离是关键架构保障，因为 `PipelineEventBus` 的**发布-订阅机制是事件从 core 到 GUI 的唯一实时通道**。
执行器在每个处理单元期间通过 `_subscribe_live_events()`（`executor.py:493-504`）将 `event_callback` 注册
为监听器，模块 `run()` 中的所有 `log()` 调用都会立即触发回调 → GUI 实时更新。
单元结束时在 `_run_unit()` 的 `finally` 块中取消订阅（`executor.py:440-443`）。

**受保护的上游调用方包括但不限于**：
- `core/executor.py:378-443` — `_run_unit()`：单元开始订阅 → 步骤间重订阅 → finally 取消订阅的生命周期
- `core/terminal.py` — TerminalSession 的 `terminal:output` 事件推送
- `gui/main_window.py:214-226` — `ExecutionWorker._on_executor_event`：通过 Signal 转发到主线程
- `gui/widgets/terminal_window.py` — TerminalWindow 的实时输出追加

**退化风险**：如果移除 try/except 或将异常重新抛出，GUI 端任何 bug（如信号 emit 失败、Qt 对象已销毁等）都会
导致 pipeline 执行流程崩溃，这是不可接受的。
