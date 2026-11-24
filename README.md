# Pdf2zh Studio

Pdf2zh Studio 是一个面向 Windows 的本地 PDF 学术翻译桌面应用。它把 PDFMathTranslate / BabelDOC 的版式保留处理链与本地 GGUF 模型连接起来，文档和模型数据都留在本机。

[下载 Windows 版本](https://github.com/JackeyLiangQ/PDFMathTranslate-ui/releases) · [桌面端说明](desktop/README.md) · [项目说明](ABOUT.md)

## 核心能力

| 能力 | 说明 |
| --- | --- |
| 本地 GGUF 模型 | 直接导入 `.gguf` 指令模型，不依赖云端 API |
| 版式保留 | 保留论文的段落、公式、图表、页眉页脚和原始页面结构 |
| 表格处理 | 识别表格区域，保护表格线、列宽和单元格布局，并按短文本方式翻译内容 |
| 扫描件 OCR | 为没有文字层的图片页添加本地文字层，再进入正常翻译流程 |
| CUDA / CPU 自动切换 | 优先使用 CUDA；CUDA 不可用或显存不足时自动回退到 CPU |
| 页面范围 | 只翻译指定页，并只输出裁切后的目标页面 |
| 大文档分块 | 默认每 50 页作为一个 BabelDOC 处理单元，完成后自动合并 |
| 多种输出 | 仅译文、双语对照、左右并排 |
| 后台任务 | 模型加载和文档翻译在独立进程中执行，界面保持响应 |

## 快速开始

1. 启动 `Pdf2zhStudio.exe`，不需要安装 Python。
2. 打开“本地模型”，导入一个 GGUF 指令模型。
3. 选择上下文长度并加载模型。没有可用 CUDA 设备时会自动使用 CPU。
4. 回到“文档翻译”，选择 PDF、语言和输出格式。
5. 根据需要设置页面范围、OCR、表格结构保护和文档分块页数。
6. 点击“开始翻译”，完成后从翻译记录打开结果。

## 翻译设置

### 页面范围

填写 `1-5, 8` 这样的页码范围时，只处理指定页面，最终 PDF 也只保留这些页面。

### 文档分块页数

- 默认 `50`，适合几十页到数百页的论文或报告。
- 分块由 BabelDOC 的 `PageCountStrategy` 执行，每一块处理完成后自动合并。
- 设置为 `0` 可关闭文档分块。
- 非零值不能小于 50 页。

### OCR

扫描件或没有文字层的页面需要启用 OCR。OCR 在 CPU 上运行，扫描页越多，识别时间越长。

### 表格结构保护

用于保护表格线、列宽和单元格位置，并减少表格短文本被模型合并、扩写或重排的情况。

## 模型建议

| 模型规模 | 建议场景 | 说明 |
| --- | --- | --- |
| 7B / 8B | 日常论文翻译 | 速度和资源占用较均衡 |
| 14B | 更重视译文质量 | 需要更多显存或内存 |
| 30B 以上 | 高配置设备 | 建议量化模型并准备充足内存 |

优先选择面向翻译或多语言理解的 GGUF 指令模型，推荐 Q4_K_M 量化。模型能否加载取决于可用内存、显存和上下文长度。

## 输出位置

译文默认保存在：

```text
用户文档/Pdf2zhStudio
```

导入的模型会复制到：

```text
%LOCALAPPDATA%/Pdf2zhStudio/models
```

## 隐私

- PDF 只在当前电脑处理。
- GGUF 模型通过本机 `llama.cpp` 服务运行。
- 本应用不上传文档、译文、模型文件或翻译记录。

## 项目结构

```text
pdf2zh/              PDFMathTranslate 核心处理链
desktop/app/         Windows 桌面界面、模型服务和翻译适配层
desktop/installer/   Inno Setup 安装程序
desktop/resources/   字体、图标和桌面运行资源
.github/workflows/   Windows 安装包构建与 Release 发布
```

## 上游与许可

Pdf2zh Studio 建立在以下开源项目之上：

- [PDFMathTranslate](https://github.com/PDFMathTranslate/PDFMathTranslate)
- [PDFMathTranslate-next](https://github.com/PDFMathTranslate-next/PDFMathTranslate-next)
- [BabelDOC](https://github.com/funstory-ai/BabelDOC)
- [llama.cpp](https://github.com/ggml-org/llama.cpp)

本仓库继续遵循上游许可证。第三方组件和模型的许可证以各自项目为准。

