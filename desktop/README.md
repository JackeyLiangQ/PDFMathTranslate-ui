# Windows 桌面端

`desktop/app` 是 Pdf2zh Studio 的 Windows 界面和本地模型适配层。打包后的程序不需要系统 Python，双击 `Pdf2zhStudio.exe` 即可运行。

## 运行

1. 启动程序。
2. 在“本地模型”中导入 GGUF 文件。
3. 选择上下文长度并加载模型。
4. 返回“文档翻译”，导入 PDF 并开始任务。

模型文件会复制到：

```text
%LOCALAPPDATA%/Pdf2zhStudio/models
```

日志文件保存在：

```text
%LOCALAPPDATA%/Pdf2zhStudio/cuda-server.log
%LOCALAPPDATA%/Pdf2zhStudio/model-loader-error.log
```

## 模型加载

模型服务使用本机 `llama.cpp`：

1. 调用 `llama-server --list-devices` 检测设备。
2. 有 CUDA 设备时优先使用 `CUDA0` 和全部 GPU 层。
3. CUDA 启动失败时，自动使用同一后端切换到 CPU。
4. 加载完成后通过一次实际推理请求验证服务。

界面会显示实际运行设备。CPU 模式可以正常使用，但速度明显低于 CUDA。

## 翻译流程

1. 读取 PDF 并校验页面范围。
2. 对没有文字层的页面执行本地 OCR。
3. 把本地 GGUF 模型适配为 BabelDOC 翻译器。
4. 按用户设置调用 BabelDOC 的页数分块策略。
5. 串行处理每个分块，并由上游 `ResultMerger` 合并。
6. 输出仅译文、双语对照或左右并排 PDF。

文档分块只控制 PDF 解析和处理单元，不是请求并发数。界面里的“并行任务”控制模型请求池大小。

## 目录

```text
desktop/app/
  main.py                 pywebview 入口
  studio.py               窗口桥接、模型服务、任务管理
  translator.py           BabelDOC 与本地 GGUF 模型适配
  translation_worker.py   独立翻译进程
  _bridge_test.py         隐藏界面和模型加载回归测试
  assets/                 HTML、CSS、JavaScript
desktop/resources/        字体、图标和桌面运行资源
```

## 打包环境

- Windows 10/11 x64
- Python 3.12 x64
- PyInstaller
- pywebview、PyMuPDF、BabelDOC、ONNX Runtime、llama.cpp 后端

桌面发布包还需要准备以下本地资源：

- `resources/doclayout.onnx`
- `resources/cuda-backend/bin/llama-server.exe` 及其运行库
- `resources/cuda-backend/vendor` 中的 CUDA 运行库
- 字体文件和应用图标

打包时必须把 `app/assets`、`resources`、模型运行库和隐藏导入模块一起收集。发布前建议执行：

```text
Pdf2zhStudio.exe --self-test
Pdf2zhStudio.exe --self-test-bridge <report.json>
Pdf2zhStudio.exe --self-test-bridge <report.json> --model <model.gguf>
```

GitHub Actions 使用 `.github/workflows/desktop-installer.yml` 自动完成上述步骤，并通过 `desktop/installer/Pdf2zhStudio.iss` 生成安装包。


