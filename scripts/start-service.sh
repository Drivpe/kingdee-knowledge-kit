#!/usr/bin/env bash
# start-service.sh — 参数化启动金蝶知识检索服务(匿名)
# 用法: ./start-service.sh [port] [install_root] [--restart]
PORT="${1:-4097}"
ROOT="${2:-$HOME/.kingdee-kit}"
[ "$3" = "--restart" ] && RESTART=1
SVC="$ROOT/service/kingdee-ksearch-service.py"
[ -f "$SVC" ] || { echo "service not found: $SVC"; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 not found"; exit 1; }
if [ -n "$RESTART" ]; then
  if command -v fuser >/dev/null 2>&1; then fuser -k "$PORT/tcp" 2>/dev/null || true; fi
  sleep 1
fi
nohup python3 "$SVC" "$PORT" >/dev/null 2>&1 &
for _ in $(seq 1 24); do
  sleep 0.5
  if command -v curl >/dev/null 2>&1; then
    curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && { echo "service ready on :$PORT"; exit 0; }
  else
    python3 -c "import urllib.request,sys;urllib.request.urlopen('http://127.0.0.1:$PORT/health',timeout=2)" 2>/dev/null \
      && { echo "service ready on :$PORT"; exit 0; }
  fi
done
echo "service not ready on :$PORT"
exit 1
