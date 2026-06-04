#!/bin/sh
set -eu

cd /app

# Reflex 前后端分别监听 3000 / 8000；Nginx 对外统一暴露 80。
reflex run &
REFLEX_PID=$!

nginx -g 'daemon off;' &
NGINX_PID=$!

# 任一关键进程退出，都让容器退出（避免只剩 Nginx 欢迎页）。
wait -n "$REFLEX_PID" "$NGINX_PID"
exit 1