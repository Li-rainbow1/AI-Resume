# 固定性能测试数据 v2

本目录只保存测试输入和素材清单，与 `testdata/quality/` 的问答评测数据分开。当前仅完成本地准备，不包含压测结果、模型质量分数或性能提升结论。

## 图片解析

- 人工查看文件：[`image_parser/five-images.pdf`](image_parser/five-images.pdf)。共 5 页，每页 1 张来自源 PDF 的图片，约 6.67 MiB，低于后端 10 MiB 限制。
- 固定图片与顺序：`image_parser/assets/`。
- 来源页、尺寸、文件大小和 SHA-256：[`image_parser/manifest.json`](image_parser/manifest.json)。
- 源文件只记录文件名、SHA-256 和选取页码，不复制到仓库；当前使用第 4、5、6、9 页的前 5 个 PDF 内嵌图片对象。原六页 PDF 与第六张源图片保留作历史素材，不参与当前测试。

| 顺序 | 来源页 | 内容形态 | 用途 |
| --- | ---: | --- | --- |
| 1 | 4 | 网络结构图 | 图示描述与文字识别耗时 |
| 2 | 5 | 模块结构图 | 图示描述与文字识别耗时 |
| 3 | 5 | 模块结构图 | 图示描述与文字识别耗时 |
| 4 | 6 | 卷积流程图 | 图示描述与文字识别耗时 |
| 5 | 9 | 图像修复对比图 | 视觉解析耗时 |

运行时 `PerformanceDataFactory.document("pdf", image_count=5)` 从这组固定素材复制图片，逐张校验 SHA-256，再生成本轮隔离文件名的 PDF。两组使用同一组图片、正文结构和顺序，仅替换隔离标识。启动容器前生成样本并检查 10 MiB 限制；生成的固定预览 PDF 会添加测试页眉，图片内容保持来源素材不变。

默认按旧同步串行、新异步并发 3 顺序执行；每组 2 次预热、10 次正式测量。已完成旧版基线时可只运行 `async_c3`，避免重复采集。真实模型的限流、网络和输出长度需要在报告中单独记录，不能与 Mock 结果混算。历史小样本不能与本套素材结果混算。

## 生成、复用与验证

运行以下命令可从本地论文 PDF 提取固定图片并生成素材清单；不会启动服务、上传文件或调用模型：

```powershell
.\.venv\Scripts\python.exe scripts\prepare_performance_data.py `
  --source-pdf "C:\Users\Administrator\Desktop\Omni_Contextual_Aggregation_Networks_for_High-Fidelity_Image_Inpainting.pdf" `
  --replace
```

固定素材已落盘，日常压测直接读取，无须重新生成；入口默认拒绝覆盖已有目录。使用 `--reuse-assets` 可复用清单前五张图片生成五页 PDF，不删除源图片与旧 PDF。准备脚本使用 Poppler `pdfimages` 提取真实内嵌图片，使用 Pillow 校验图片可读性与唯一 SHA-256。

离线检查命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\performance\test_fixed_data.py -q -o addopts=''
```

离线检查不会运行 Locust 用户，也不会上传资料。正式性能基线已于 2026-09-12 执行（`reports/performance/image-parser/formal-ab-20260912a/`）；真实模型结果与量化数字见该轮报告。

历史素材曾包含 6 张图片、6 页，约 12.81 MiB，旧版上传在读取阶段触发文件大小限制。当前移除最后一页，保留前五张原始图片，页数为 5，大小 6,996,332 字节。本次调整未启动压测、未调用模型。
