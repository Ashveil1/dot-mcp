#!/bin/bash
# expose.sh — one shot: server + tunnel, print URL for ChatGPT connector
# usage: ./expose.sh                       (no auth)
#        MCP_AUTH_TOKEN=xxx ./expose.sh    (with Bearer auth)
set -u
cd "$(dirname "$0")"
PORT="${MCP_PORT:-8088}"

# 1. auth: ใช้ token ก็ต่อเมื่อส่ง MCP_AUTH_TOKEN มาทาง env เท่านั้น
#    ไม่ส่ง = ไม่มี auth (สะดวก แต่ใครได้ URL ก็สั่งเครื่องได้)
if [ -n "${MCP_AUTH_TOKEN:-}" ]; then
  AUTH_HDR=(-H "Authorization: Bearer $MCP_AUTH_TOKEN")
  AUTH_LINE="Bearer $MCP_AUTH_TOKEN"
else
  AUTH_HDR=()
  AUTH_LINE="none (เปิดโล่ง ใครมี URL ใช้ได้)"
fi

# 2. server (เริ่มใหม่แบบไม่มี token ยกเว้นส่ง MCP_AUTH_TOKEN มาทาง env)
ok=$(curl -s --max-time 3 http://127.0.0.1:$PORT/health || true)
if ! echo "$ok" | grep -q '"tools"'; then
  pkill -f "[d]ot_mcp\.py" 2>/dev/null; sleep 2
  if [ -n "${MCP_AUTH_TOKEN:-}" ]; then
    MCP_PORT=$PORT nohup .venv/bin/python dot_mcp.py > mcp.log 2>&1 &
  else
    MCP_PORT=$PORT env -u MCP_AUTH_TOKEN nohup .venv/bin/python dot_mcp.py > mcp.log 2>&1 &
  fi
  sleep 6
fi
curl -s --max-time 5 http://127.0.0.1:$PORT/health || { echo "server failed"; tail -10 mcp.log; exit 1; }
echo

# 3. cloudflared binary
CF=./bin/cloudflared
if [ ! -x "$CF" ]; then
  mkdir -p bin
  curl -fsSL -o "$CF" https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64
  chmod +x "$CF"
fi

# 4. tunnel (restart fresh)
pkill -f "[c]loudflared tunnel" 2>/dev/null; sleep 1
rm -f tunnel.log
nohup "$CF" tunnel --url http://127.0.0.1:$PORT > tunnel.log 2>&1 &
URL=""
for i in $(seq 1 45); do
  URL=$(grep -oE "https://[A-Za-z0-9-]+\.trycloudflare\.com" tunnel.log 2>/dev/null | head -1)
  [ -n "$URL" ] && break
  sleep 2
done
[ -z "$URL" ] && { echo "tunnel failed"; tail -20 tunnel.log; exit 1; }
HOST_ONLY=$(echo "$URL" | sed 's#https://##')

# 5. restart server whitelisting tunnel host (MCP DNS rebinding protection)
export MCP_PUBLIC_HOST="$HOST_ONLY"
pkill -f "[d]ot_mcp\.py" 2>/dev/null; sleep 2
if [ -n "${MCP_AUTH_TOKEN:-}" ]; then
  MCP_PORT=$PORT nohup .venv/bin/python dot_mcp.py > mcp.log 2>&1 &
else
  MCP_PORT=$PORT env -u MCP_AUTH_TOKEN nohup .venv/bin/python dot_mcp.py > mcp.log 2>&1 &
fi
sleep 6
CODE=$(curl -s --max-time 15 -o /dev/null -w "%{http_code}" -X POST "$URL/mcp" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  "${AUTH_HDR[@]}" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}')
[ "$CODE" != "200" ] && { echo "public MCP check failed (http $CODE)"; tail -5 mcp.log; exit 1; }

echo "================ COPY TO CHATGPT ================"
echo "URL:    $URL/mcp"
echo "Auth:   $AUTH_LINE"
echo "Health: $URL/health"
echo "================================================="
echo "ChatGPT web > Settings > Apps > Advanced > Developer Mode ON"
if [ -n "${MCP_AUTH_TOKEN:-}" ]; then
  echo "> Add custom connector > paste URL + Bearer token"
else
  echo "> Add custom connector > paste URL + auth none"
  echo "!! ปิด tunnel ทันทีเมื่อเลิกใช้: pkill -f '[c]loudflared tunnel'"
fi
