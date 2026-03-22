# Auto Opener Miner (AOM) - 项目核心记忆

## 1. 项目概览
AOM 是专为 WorldQuant Brain 平台设计的一站式工作流工具，旨在实现因子的批量生成、高速回测、库管理和结果回填。

### 核心模块
- **Template Filler**: 基于占位符 `<name/>` 的因子表达式批量展开引擎。
- **Submitter (Concurrent)**: 高性能并行回测提交器，支持 Multiple (Batch) 模式。
- **Factor Library**: 基于 SQLite 的本地因子库，支持指纹（Fingerprint）去重。
- **Metadata Downloader**: 自动同步 Brain 平台的算子、设置和数据集缓存。

---

## 2. 关键技术方案与修复 (已填坑)

### 2.1 并发与锁优化
- **UI 假死修复**: 缩小了 `_state_lock` 的粒度。数据库写入和 UI 进度更新已移出锁范围，确保高并发时 TUI/CLI 不会卡住。
- **并行结果拉取**: 在回测完成后，使用 `ThreadPoolExecutor` 并行抓取子任务 ID 和 Alpha 指标，极大缩短了批次切换的等待时间。

### 2.2 SQLite 多线程安全
- **连接策略**: 弃用了长连接，改为“随用随开”。每个 Batch 任务在独立线程中创建自己的连接。
- **配置优化**: 开启了 `check_same_thread=False` 和 `timeout=30.0`，彻底解决了 `database is locked` 和 `SQLite objects created in a thread can only be used in that same thread` 的报错。

### 2.3 状态管理
- **流式处理**: 针对超大因子文件（>2MB），系统会自动切换为 `ordered stream` 模式，避免一次性加载导致的内存溢出。
- **状态回传**: 增加了对 `CANCELLED` 状态的识别，并在 CLI/TUI 中实时回传 Brain 官网的模拟详情直达链接。

---

## 3. 运行环境配置 (Current Context)
- **默认地区 (Region)**: `ASI`
- **默认宇宙 (Universe)**: `MINVOL1M`
- **回测并发策略**: 建议 `concurrency=2`, `batch-size=10`（单次提交 20 个回测）。

---

## 4. 常用操作指令

### 交互式脚本 (推荐)
```bash
./scripts/submit_factors.sh
```
*特点：自动识别最新生成的 JSON 因子文件，交互式确认 Region/Universe，支持断点续传。*

### CLI 直接运行
```bash
python3 -m aom submit run --file <FILE> --concurrency 2 --batch-size 10 --region ASI --universe MINVOL1M
```

---

## 5. 注意事项与常见报错处理
- **变量名错误**: 若提示 `Attempted to use unknown variable`，通常是因为变量名带了数字后缀（如 `_6554`），请检查模板生成逻辑。
- **算子不支持**: `ts_mean` 等时序算子仅支持“连续性数值”输入。若在 `_anntime` 或 `_date` 字段上使用会报错 `Operator does not support event inputs`。
- **UI 布局**: Web UI 的字段浏览器已修复“缩成一团”的问题，使用了稳固的 Flexbox 布局。

---
*Last Updated: 2026-03-22*
