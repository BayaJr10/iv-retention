#!/bin/sh
# Starts Streamlit (internal, port 8501, under /app) and nginx (public, $PORT).
set -e
ROOT="${APP_ROOT:-/srv}"
export PORT="${PORT:-10000}"
export APP_ROOT="$ROOT"
envsubst '${PORT} ${APP_ROOT}' < "$ROOT/deploy/nginx.conf.template" > /tmp/nginx.conf

streamlit run "$ROOT/app.py" \
  --server.port 8501 --server.address 127.0.0.1 \
  --server.baseUrlPath app --server.headless true \
  --browser.gatherUsageStats false &
ST_PID=$!

nginx -c /tmp/nginx.conf -g 'daemon off;' &
NG_PID=$!

# If either process stops, stop the container so Render restarts it
while kill -0 "$ST_PID" 2>/dev/null && kill -0 "$NG_PID" 2>/dev/null; do sleep 5; done
echo "A process exited; shutting down." >&2
kill "$ST_PID" "$NG_PID" 2>/dev/null || true
exit 1
