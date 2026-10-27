#!/bin/bash
set -e

# Ensure data directories exist
mkdir -p data/qdrant data/history data/eval data/models data/hot_words data/meetings

# 远程模式（瘦壳）：SINKDUCE_REMOTE_MODE=1 时不启动完整后端，只跑
# src/remote_shell —— 前端 SPA + 录音/桌面代理 + MCP Hub + 壳级设置，
# 不连 Qdrant、不加载模型。开关在 .env（改完 docker compose up -d 重建）。
if [ "${SINKDUCE_REMOTE_MODE:-0}" = "1" ]; then
  export SINKDUCE_REMOTE_SHELL=1
  exec python -m src.remote_shell
fi

# Start FastAPI (serves both API and frontend)
exec python -m src.main
