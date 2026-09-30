#!/bin/bash
set -e

echo "========================================"
echo "  KnowledgeDiver - 停止服务"
echo "========================================"
echo

# 1. Stop backend
echo "[1/3] 停止后端..."
if systemctl is-active --quiet knowledgediver; then
    systemctl stop knowledgediver
    echo "  后端已停止"
else
    echo "  后端未在运行"
fi

# 2. Stop frontend
echo "[2/3] 停止前端..."
if systemctl is-active --quiet knowledgediver-frontend; then
    systemctl stop knowledgediver-frontend
    echo "  前端已停止"
else
    echo "  前端未在运行"
fi

# 3. Stop nginx
echo "[3/3] 停止 Nginx..."
if systemctl is-active --quiet nginx; then
    systemctl stop nginx
    echo "  Nginx 已停止"
else
    echo "  Nginx 未在运行"
fi

echo
echo "========================================"
echo "  服务已停止"
echo "========================================"
