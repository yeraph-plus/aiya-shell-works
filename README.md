# Shell Worker Platform

模块化工作流平台，通过 YAML 编排步骤模块，对文件或文本输入执行批量处理、自动化任务。

---

## 项目设计

### 核心理念

Shell Worker Platform 按**单向管道**模型设计：输入 → 复制到安全目录 → 逐模块处理 → 输出。GUI 与内核完全解耦，内核可脱离桌面环境独立调用。

```
YAML 文件 ──► WorkflowLoader.load() ──► WorkflowDefinition ──► PipelineExecutor.execute()
                                                                        │
File Paths ──► FileHandler.prepare_context() ──► ──────────────────────┤
Text Lines ──► InputHandler.prepare_context()                           │
                                                                        ▼
                                                                 _build_units()
                                                                 _prepare_context()
                                                                        │
                                                                        ▼
                                                                  PipelineContext
                                                                        │
                                                                        ▼
                                                                  Module.run(ctx, config)
                                                                        │
                                                           ┌────────────┼────────────┐
                                                           ▼            ▼            ▼
                                                     ctx.clone()  ctx.events.log  ctx.run_command()
                                                           │            │            │
                                                           ▼            ▼            ▼
                                                      [下一步骤]   事件总线/GUI    TerminalWindow
                                                                                   (PTY 子进程)
```

### 五种工作流模式

| 模式 | 输入 | 单元 = ? | 典型场景 |
|------|------|----------|----------|
| `file` | 文件或文件夹 | 每个文件 | 批量重命名、格式转换、元数据清理 |
| `folder` | 单个文件夹 | 整个文件夹 | 结构重组、Gallery 编排 |
| `none` | 无输入 | 1 个空单元 | 报告/配置生成 |
| `cycle` | 文件或文件夹 | 每个文件（共享状态） | 跨文件统计、计数聚合 |
| `input` | 多行文本 | 每行 | 逐行 API 调用、批量 URL |

---

## 内核设计

### 分层架构

```
用户 ──► GUI 层 ──► Core 内核层 ──► Module 模块层 ──► External Tools 外部工具

         MainWindow           executor               modules/*            resources/
         WorkflowEditor       pipeline               MODULE_META          exiftool.exe
         DynamicForm          handler_file           CONFIG_SCHEMA        ffmpeg.exe
         TerminalWindow       handler_input          run(ctx, config)     VapourSynth
         DropZone             terminal               (21 个模块)           WinRAR
         ExecutionWorker      module_manager
                              workflow_loader
                              config_schema
                              input_inspector
```

### 关键类型

| 类型 | 文件 | 说明 |
|------|------|------|
| `PipelineContext` | `core/pipeline.py` | 每单元的处理上下文，步骤间流转 |
| `PipelineEventBus` | `core/pipeline.py` | 每单元的事件总线，结构化日志 |
| `FileHandler` | `core/handler_file.py` | 安全文件拷贝/直写 |
| `InputHandler` | `core/handler_input.py` | 文本输入构建 |
| `InputInspector` | `core/input_inspector.py` | 输入路径校验 |
| `TerminalSession` | `core/terminal.py` | PTY 子进程封装 |
| `ModuleDefinition` | `core/module_manager.py` | 已验证模块 |
| `WorkflowDefinition` | `core/workflow_loader.py` | 已验证工作流 |
| `WorkflowValidationResult` | `core/workflow_loader.py` | 工作流校验结果（含 errors 与 parsed workflow） |

---

## 使用方法

### 环境要求

- Python 3.11+
- Windows (PySide6 + pywinpty 支持)

### 安装

```bash
pip install -r requirements.txt
```

### 启动桌面应用

```bash
python main.py
```

主界面支持拖拽或浏览添加输入文件/文件夹，选择工作流后点击执行。

### 创建工作流

通过内置编辑器新建或直接编写 YAML：

```yaml
meta:
  name: "文件批处理"
  description: "复制文件、标准化后缀、生成摘要"
mode: file
steps:
  - module: normalize-extensions
    name: 标准化后缀
    params:
      lowercase: true
  - module: example-write-summary
    name: 生成摘要
    params:
      filename: "summary.txt"
```

---

## 纯内核模式

内核不依赖 GUI，可通过 Python 脚本直接调用 `execute_workflow()`：

```python
from core import execute_workflow, WorkflowDefinition, WorkflowMeta, WorkflowStep

# 方式一：构造 WorkflowDefinition
workflow = WorkflowDefinition(
    meta=WorkflowMeta(name="My Workflow"),
    mode="file",
    steps=(
        WorkflowStep(
            module="normalize-extensions",
            name="标准化后缀",
            params={"lowercase": True},
        ),
        WorkflowStep(
            module="example-write-summary",
            name="生成摘要",
            params={"filename": "summary.txt"},
        ),
    ),
)

result = execute_workflow(
    workflow,
    output_dir="./output",
    input_path="./input/myfile.jpg",       # file 模式：文件或文件夹路径
    # input_text="line1\nline2",            # input 模式：多行文本
    # direct_mode=True,                      # 直写模式，不拷贝
    # event_callback=lambda event: print(f"[{event.type}] {event.text}"),
    # progress_callback=lambda p: print(f"进度: {p['percent']}%"),
)

print(f"成功: {result['success']}")
print(f"处理 {result['processed_units']} 个单元, 成功 {result['successful_units']}, 失败 {result['failed_units']}")
print(f"产物目录: {result['output_dir']}")
```

```python
# 方式二：加载已有 YAML 工作流
result = execute_workflow(
    "图集批处理.yaml",            # workflows/ 下的文件名
    output_dir="./output",
    input_path="./input/my_gallery",
)
```

```python
# 方式三：直接传入 dict
result = execute_workflow(
    {
        "meta": {"name": "Quick Test"},
        "mode": "none",
        "steps": [
            {"module": "example-create-text-file", "params": {"filename": "hello.txt", "content": "Hello!"}},
        ],
    },
    output_dir="./output",
)
```

### 在模块中使用 context.run_command() 调用外部程序

```python
def run(context, config):
    result = context.run_command(["echo", "hello"])
    # 输出实时写入 context.events (terminal:output)
    # GUI 自动弹出 TerminalWindow 显示实时终端
    if not result.is_success:
        raise RuntimeError(f"命令返回 {result.exit_code}")
    return context
```

---

## 步骤模块列表

| 模块 | slug | 模式 | 说明 |
|------|------|------|------|
| 递归提取文件 | `flatten-folder` | folder | 递归移动子文件夹文件到根目录，按深度层级添加数字前缀 |
| 清除文件属性 | `strip-attributes` | file, folder | 清除文件的只读/隐藏属性 |
| 标准化文件后缀 | `normalize-extensions` | file, folder | 统一扩展名为小写标准后缀（jpeg→jpg, tiff→tif 等） |
| 删除无用文件 | `delete-files` | file, folder | 按 glob 模式硬删除 .txt/.url/.html/Thumbs.db 等无用文件 |
| 清除 EXIF 元数据 | `exiftool-clean` | file, folder | 通过 ExifTool 批量清除图片/视频文件元数据 |
| 解除文件占用 | `unlock-files` | file, folder | 检测并终止 dllhost/资源管理器等进程对文件的占用锁定 |
| Gallery 统计计数 | `gallery-count` | folder | 统计视频/其他文件数量，在文件夹名后追加计数标签 |
| Gallery 重命名 | `gallery-rename` | folder | 按文件类型分组建模重命名（图片无前缀，视频 VIDEO_ 队列） |
| RAR 打包 | `pack-rar` | folder | 调用 WinRAR 将文件夹打包为 .rar 压缩包 |
| 提取图集 | `extract-archive` | file | 从 ZIP 压缩包提取图片并生成 info.json 信息文件 |
| FFmpeg 转码 | `ffmpeg-convert` | file, folder | 批量转换媒体文件格式，支持硬件加速 |
| FFmpeg 合成编码 | `ffmpeg-compose` | file, folder | 将 Y4M 流或帧序列编码为最终视频 |
| FFmpeg 合并 m3u8 | `ffmpeg-merge` | file, input | 下载并合并 HLS 播放列表为单个文件 |
| VapourSynth 去隔行 | `vs-deinterlace` | file | BWDIF/VIVTC 去隔行处理 |
| VapourSynth 补帧 | `vs-frame-interpolate` | file | RIFE 模型 AI 智能补帧（2x/4x/8x） |
| VapourSynth 超分 | `vs-super-resolution` | file | RealESRGAN/SwinIR AI 超分辨率处理 |
| 路径重命名 | `example-rename-path` | file, folder | 为文件/文件夹添加前缀后缀重命名 |
| 写入摘要 | `example-write-summary` | file, folder, none | 将 PipelineContext 状态输出为摘要文本 |
| 创建文本文件 | `example-create-text-file` | none | 在产物目录创建文本文件 |
| 循环计数 | `example-cycle-count-counter` | cycle | 跨文件累计计数，生成处理清单报告 |
| 输入回显 | `example-input-echo` | input | 将每行文本输入写入独立文件 |

### 引入外部工具

部分模块依赖外部程序。`resources/` 目录提供安装脚本：

```powershell
powershell -File resources/install_exiftool.ps1    # ExifTool
powershell -File resources/install_ffmpeg.ps1      # FFmpeg
powershell -File resources/install_aria2.ps1       # aria2
```

---

## 测试

### 运行命令

```bash
python -m pytest
python -m pytest --cov=core --cov=gui --cov=modules --cov-report=term-missing
```

### 测试文件覆盖点

| 文件 | 覆盖内容 |
|------|---------|
| `test_executor.py` | file/folder/none/cycle/input 模式执行、错误隔离、取消、参数校验 |
| `test_module_manager.py` | 合法模块加载、无效模块忽略、缓存和重新扫描、重复 slug |
| `test_workflow_loader.py` | YAML 加载、校验错误捕捉、保存往返、new_workflow 模板 |
| `test_file_handler.py` | none 上下文、文件/文件夹复制、finalize_context、PipelineEventBus |
| `test_workflow_editor_state.py` | iter_schema_fields、normalize_params、filter_modules、WorkflowDraft |
| `test_flatten_folder.py` | FlattenFolder 模块集成测试 |
| `test_example_assets.py` | 示例模块可发现、工作流可加载、端到端执行 |
| `test_config_schema.py` | validate_config_schema、normalize_config_params 参数校验 |
| `test_input_handler.py` | InputHandler.build_units、prepare_context |
| `test_input_inspector.py` | InputInspector 路径校验、文本输入拆分 |
| `test_unlock_files.py` | 文件锁定检测 CreateFileW 原子操作验证 |
