#!/usr/bin/env bash
# 启动脚本 - 自动激活 venv + 启动 uvicorn
set -e
cd "$(dirname "$0")"

if [ ! -d "venv" ]; then
    echo "venv 不存在, 请先: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

source venv/bin/activate
PYTHONUNBUFFERED=1 exec python3 -u app.py