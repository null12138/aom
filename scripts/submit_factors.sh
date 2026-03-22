#!/bin/bash

# Auto Opener Miner - 批量回测提交脚本 (自动识别最新文件)
# 用法: ./scripts/submit_factors.sh [因子文件] [并发数] [批次大小]

# --- 自动寻找最新生成的因子文件 ---
NEWEST_FILE=$(ls -t generated/*.json 2>/dev/null | head -n 1)
DEFAULT_FILE=${NEWEST_FILE:-"generated/factors.json"}

DEFAULT_CONC=2
DEFAULT_BATCH=10

# 获取参数
FACTOR_FILE=${1:-$DEFAULT_FILE}
CONCURRENCY=${2:-$DEFAULT_CONC}
BATCH_SIZE=${3:-$DEFAULT_BATCH}

# 基础检查
if [ ! -f "$FACTOR_FILE" ]; then
    echo "❌ 错误: 找不到因子文件 $FACTOR_FILE"
    echo "用法: $0 [因子文件] [并发数] [批次大小]"
    exit 1
fi

# --- 交互确认 Region 和 Universe ---
echo "--- 运行环境确认 ---"
echo "当前默认因子文件: $FACTOR_FILE"
read -p "请输入 Region (直接回车保持默认 ASI): " REGION
REGION=${REGION:-ASI}

read -p "请输入 Universe (直接回车保持默认 MINVOL1M): " UNIVERSE
UNIVERSE=${UNIVERSE:-MINVOL1M}

# 获取文件名作为 state 基础名
BASE_NAME=$(basename "$FACTOR_FILE" .json)
STATE_FILE="runs/state_${BASE_NAME}.json"

echo ""
echo "🚀 准备启动回测任务..."
echo "--------------------------------------"
echo "文件: $FACTOR_FILE"
echo "状态: $STATE_FILE"
echo "并发: $CONCURRENCY"
echo "批次: $BATCH_SIZE (Multiple 模式)"
echo "地区: $REGION"
echo "宇宙: $UNIVERSE"
echo "--------------------------------------"
read -p "确认启动? (y/n): " CONFIRM
if [ "$CONFIRM" != "y" ]; then
    echo "已取消。"
    exit 0
fi

# 运行命令
python3 -m aom submit run \
    --file "$FACTOR_FILE" \
    --state "$STATE_FILE" \
    --concurrency "$CONCURRENCY" \
    --batch-size "$BATCH_SIZE" \
    --region "$REGION" \
    --universe "$UNIVERSE" \
    --library "db/factor_library.db"

# 检查结果
if [ $? -eq 0 ]; then
    echo "--------------------------------------"
    echo "✅ 任务执行完毕！"
    echo "进度已保存至: $STATE_FILE"
else
    echo "--------------------------------------"
    echo "❌ 任务运行过程中出错，请检查日志。"
fi
