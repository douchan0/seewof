#!/usr/bin/env bash
# MacBook 端到端冒烟测试
set -e
BASE="http://127.0.0.1:8000"
BOOT="dev-bootstrap-token-please-change-in-prod"

echo "[1] health"
curl -s $BASE/api/v1/health | python3 -m json.tool

echo ""
echo "[2] bootstrap admin"
curl -s -X POST $BASE/api/v1/auth/bootstrap_admin \
  -H "Content-Type: application/json" \
  -d "{\"token\":\"$BOOT\",\"username\":\"admin\",\"password\":\"admin123\"}" \
  | python3 -m json.tool

echo ""
echo "[3] login"
TOK=$(curl -s -X POST $BASE/api/v1/auth/login \
  -d "username=admin&password=admin123" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
echo "  token: ${TOK:0:30}..."

echo ""
echo "[4] add classroom"
curl -s -X POST $BASE/api/v1/classrooms \
  -H "Authorization: Bearer $TOK" \
  -H "Content-Type: application/json" \
  -d '{"id":"ROOM-MAC-01","name":"MacBook测试教室","ip":"127.0.0.1","psk":"'"$(head -c 48 /dev/urandom | base64)"'"}' \
  | python3 -m json.tool

echo ""
echo "[5] list classrooms"
curl -s $BASE/api/v1/classrooms \
  -H "Authorization: Bearer $TOK" | python3 -m json.tool

echo ""
echo "[6] add USB key"
curl -s -X POST $BASE/api/v1/usb \
  -H "Authorization: Bearer $TOK" \
  -H "Content-Type: application/json" \
  -d '{"serial":"USBSTOR-DISK-MAC-FAKE-SN-12345","teacher_id":"T001","teacher_name":"张老师"}' \
  | python3 -m json.tool

echo ""
echo "=== ALL OK ==="
