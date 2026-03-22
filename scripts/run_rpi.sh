#!/bin/bash
# Auto Opener Miner - Raspberry Pi Lean Bootstrapper

# 1. 自动进入项目目录
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$SCRIPT_DIR/.."

# 2. 环境优化设置
export PYTHONUNBUFFERED=1
# 树莓派通常不需要 24位真彩色，使用 256 色可显著降低 Textual 渲染压力
export COLORTERM=256color

echo "--- AOM 树莓派精简模式启动 ---"
echo "[Info] 已禁用 Web 服务以节省内存 (128MB+ RAM free up)"
echo "[Info] 渲染模式: 256 Colors (CPU Friendly)"

# 3. 运行 TUI
# 使用 -O 优化模式运行 python 进一步提升性能
python3 -O -m aom tui
