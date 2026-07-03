#!/bin/bash
# WhatsApp Hermes Integration - Unified Startup Script
set -e
LOG_DIR=/opt/data/whatsapp-messages
BRIDGE_DIR=/opt/hermes/scripts/whatsapp-bridge
echo "[start] WhatsApp Hermes Integration starting..."
pkill -f "bridge.js.*whatsapp" 2>/dev/null || true
pkill -f "batcher.py" 2>/dev/null || true
pkill -f "mcp_server.py" 2>/dev/null || true
pkill -f "triage_agent.py" 2>/dev/null || true
pkill -f "escalation_pusher.py" 2>/dev/null || true
pkill -f "digest_cron.py" 2>/dev/null || true
sleep 3
cd $BRIDGE_DIR
WHATSAPP_ALLOWED_USERS="*" WHATSAPP_MODE="bot" node bridge.js --port 3000 --session /opt/data/platforms/whatsapp/session-phone1 > $LOG_DIR/bridge-phone1.log 2>&1 &
WHATSAPP_ALLOWED_USERS="*" WHATSAPP_MODE="bot" node bridge.js --port 3001 --session /opt/data/platforms/whatsapp/session-phone2 > $LOG_DIR/bridge-phone2.log 2>&1 &
sleep 5
cd $LOG_DIR
python3 batcher.py > $LOG_DIR/batcher.log 2>&1 &
python3 mcp_server.py > $LOG_DIR/mcp.log 2>&1 &
python3 triage_agent.py > $LOG_DIR/triage.log 2>&1 &
python3 escalation_pusher.py > $LOG_DIR/escalation.log 2>&1 &
python3 digest_cron.py > $LOG_DIR/digest.log 2>&1 &
sleep 3
echo "[start] All services started"
