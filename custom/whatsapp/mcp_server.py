#!/usr/bin/env python3
"""
WhatsApp MCP Server

Exposes WhatsApp data from SQLite as MCP tools accessible by the Hermes Agent.
Runs as an SSE server on port 8650.
"""

import json
import sqlite3
import os
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import threading
import uuid

DB_PATH = '/opt/data/whatsapp-messages/whatsapp_data.db'
CONFIG_PATH = '/opt/data/whatsapp-messages/config.json'
PORT = 8650

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)

# MCP Tool implementations
def whatsapp_search_messages(params):
    """Full-text search across messages."""
    query = params.get('query', '')
    sender = params.get('sender', '')
    phone = params.get('phone', '')
    date_from = params.get('date_from', '')
    date_to = params.get('date_to', '')
    is_group = params.get('is_group')
    limit = int(params.get('limit', 50))

    db = get_db()
    sql = "SELECT id, source_phone, sender_phone, sender_name, chat_id, is_group, text, media_type, timestamp FROM messages WHERE 1=1"
    args = []

    if query:
        sql += " AND text LIKE ?"
        args.append(f'%{query}%')
    if sender:
        sql += " AND (sender_phone LIKE ? OR sender_name LIKE ?)"
        args.extend([f'%{sender}%', f'%{sender}%'])
    if phone:
        sql += " AND source_phone = ?"
        args.append(phone)
    if date_from:
        sql += " AND timestamp >= ?"
        args.append(date_from)
    if date_to:
        sql += " AND timestamp <= ?"
        args.append(date_to)
    if is_group is not None:
        sql += " AND is_group = ?"
        args.append(1 if is_group else 0)

    sql += " ORDER BY timestamp DESC LIMIT ?"
    args.append(limit)

    rows = db.execute(sql, args).fetchall()
    db.close()
    return [dict(r) for r in rows]


def whatsapp_get_recent(params):
    """Get recent messages."""
    phone = params.get('phone', '')
    sender = params.get('sender', '')
    limit = int(params.get('limit', 20))

    db = get_db()
    sql = "SELECT id, source_phone, sender_phone, sender_name, chat_id, is_group, text, media_type, timestamp FROM messages WHERE 1=1"
    args = []

    if phone:
        sql += " AND source_phone = ?"
        args.append(phone)
    if sender:
        sql += " AND (sender_phone LIKE ? OR sender_name LIKE ?)"
        args.extend([f'%{sender}%', f'%{sender}%'])

    sql += " ORDER BY timestamp DESC LIMIT ?"
    args.append(limit)

    rows = db.execute(sql, args).fetchall()
    db.close()
    return [dict(r) for r in rows]


def whatsapp_list_tasks(params):
    """List extracted tasks."""
    status = params.get('status', '')
    priority = params.get('priority', '')
    date_from = params.get('date_from', '')

    db = get_db()
    sql = "SELECT * FROM wa_tasks WHERE 1=1"
    args = []

    if status:
        sql += " AND status = ?"
        args.append(status)
    if priority:
        sql += " AND priority = ?"
        args.append(priority)
    if date_from:
        sql += " AND created_at >= ?"
        args.append(date_from)

    sql += " ORDER BY created_at DESC"
    rows = db.execute(sql, args).fetchall()
    db.close()
    return [dict(r) for r in rows]


def whatsapp_list_notes(params):
    """List extracted notes."""
    date_from = params.get('date_from', '')
    keyword = params.get('keyword', '')

    db = get_db()
    sql = "SELECT * FROM wa_notes WHERE 1=1"
    args = []

    if date_from:
        sql += " AND created_at >= ?"
        args.append(date_from)
    if keyword:
        sql += " AND content LIKE ?"
        args.append(f'%{keyword}%')

    sql += " ORDER BY created_at DESC"
    rows = db.execute(sql, args).fetchall()
    db.close()
    return [dict(r) for r in rows]


def whatsapp_list_contacts(params):
    """List contacts."""
    sort_by = params.get('sort_by', 'last_seen')
    is_family = params.get('is_family')

    db = get_db()
    sql = "SELECT c.*, GROUP_CONCAT(h.handle_type || ':' || h.handle_value, ', ') as handles FROM unified_contacts c LEFT JOIN contact_handles h ON c.id = h.contact_id WHERE 1=1"
    args = []

    if is_family is not None:
        sql += " AND c.is_family = ?"
        args.append(1 if is_family else 0)

    sql += " GROUP BY c.id"

    if sort_by == 'message_count':
        sql += " ORDER BY c.display_name"
    else:
        sql += " ORDER BY c.updated_at DESC"

    rows = db.execute(sql, args).fetchall()
    db.close()
    return [dict(r) for r in rows]


def whatsapp_get_escalations(params):
    """Get pending/recent escalations."""
    status = params.get('status', 'pending')

    db = get_db()
    if status == 'all':
        rows = db.execute("SELECT * FROM escalations ORDER BY created_at DESC LIMIT 50").fetchall()
    else:
        rows = db.execute("SELECT * FROM escalations WHERE status = ? ORDER BY created_at DESC", (status,)).fetchall()
    db.close()
    return [dict(r) for r in rows]


def whatsapp_resolve_escalation(params):
    """Mark an escalation as resolved."""
    escalation_id = params.get('escalation_id', '')
    if not escalation_id:
        return {'error': 'escalation_id required'}

    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    db.execute("UPDATE escalations SET status = 'resolved', resolved_at = ? WHERE id = ?",
               (now, escalation_id))
    db.commit()
    db.close()
    return {'status': 'resolved', 'escalation_id': escalation_id}


def whatsapp_get_stats(params):
    """Get message/task statistics."""
    phone = params.get('phone', '')
    period = params.get('period', 'today')

    db = get_db()
    now = datetime.now(timezone.utc)

    if period == 'today':
        since = now.replace(hour=0, minute=0, second=0).isoformat()
    elif period == 'week':
        since = (now - timedelta(days=7)).isoformat()
    elif period == 'month':
        since = (now - timedelta(days=30)).isoformat()
    else:
        since = '2000-01-01T00:00:00'

    base_filter = " AND source_phone = ?" if phone else ""
    args_base = [phone] if phone else []

    msg_count = db.execute(
        f"SELECT COUNT(*) as c FROM messages WHERE timestamp >= ?{base_filter}",
        [since] + args_base
    ).fetchone()['c']

    task_count = db.execute(
        f"SELECT COUNT(*) as c FROM wa_tasks WHERE created_at >= ?{' AND source_phone = ?' if phone else ''}",
        [since] + args_base
    ).fetchone()['c']

    escalation_count = db.execute(
        "SELECT COUNT(*) as c FROM escalations WHERE created_at >= ?",
        [since]
    ).fetchone()['c']

    top_contacts = db.execute(
        f"""SELECT sender_phone, sender_name, COUNT(*) as msg_count 
            FROM messages WHERE timestamp >= ?{base_filter}
            GROUP BY sender_phone ORDER BY msg_count DESC LIMIT 5""",
        [since] + args_base
    ).fetchall()

    db.close()
    return {
        'period': period,
        'phone_filter': phone or 'all',
        'message_count': msg_count,
        'task_count': task_count,
        'escalation_count': escalation_count,
        'top_contacts': [dict(c) for c in top_contacts],
    }


def whatsapp_get_conversation(params):
    """Get full conversation thread with a contact."""
    contact_phone = params.get('contact_phone', '')
    phone = params.get('phone', '')
    limit = int(params.get('limit', 30))

    if not contact_phone:
        return {'error': 'contact_phone required'}

    db = get_db()
    sql = "SELECT id, source_phone, sender_phone, sender_name, text, media_type, timestamp FROM messages WHERE sender_phone LIKE ?"
    args = [f'%{contact_phone.lstrip("+")}%']

    if phone:
        sql += " AND source_phone = ?"
        args.append(phone)

    sql += " ORDER BY timestamp DESC LIMIT ?"
    args.append(limit)

    rows = db.execute(sql, args).fetchall()
    db.close()
    return [dict(r) for r in rows]


def whatsapp_list_phones(params):
    """List connected phone numbers and their status."""
    db = get_db()
    phones = db.execute("SELECT * FROM phones").fetchall()
    result = []
    for p in phones:
        msg_count = db.execute(
            "SELECT COUNT(*) as c FROM messages WHERE source_phone = ?",
            (p['id'],)
        ).fetchone()['c']
        result.append({**dict(p), 'message_count': msg_count})
    db.close()
    return result


# Tool registry
TOOLS = {
    'whatsapp_search_messages': {
        'fn': whatsapp_search_messages,
        'description': 'Search WhatsApp messages by text, sender, date, phone source',
        'params': ['query', 'sender', 'phone', 'date_from', 'date_to', 'is_group', 'limit']
    },
    'whatsapp_get_recent': {
        'fn': whatsapp_get_recent,
        'description': 'Get recent WhatsApp messages',
        'params': ['phone', 'sender', 'limit']
    },
    'whatsapp_list_tasks': {
        'fn': whatsapp_list_tasks,
        'description': 'List extracted tasks from WhatsApp messages',
        'params': ['status', 'priority', 'date_from']
    },
    'whatsapp_list_notes': {
        'fn': whatsapp_list_notes,
        'description': 'List extracted notes from WhatsApp messages',
        'params': ['date_from', 'keyword']
    },
    'whatsapp_list_contacts': {
        'fn': whatsapp_list_contacts,
        'description': 'List WhatsApp contacts',
        'params': ['sort_by', 'is_family']
    },
    'whatsapp_get_escalations': {
        'fn': whatsapp_get_escalations,
        'description': 'Get pending or recent escalations',
        'params': ['status']
    },
    'whatsapp_resolve_escalation': {
        'fn': whatsapp_resolve_escalation,
        'description': 'Mark an escalation as resolved',
        'params': ['escalation_id']
    },
    'whatsapp_get_stats': {
        'fn': whatsapp_get_stats,
        'description': 'Get WhatsApp message and task statistics',
        'params': ['phone', 'period']
    },
    'whatsapp_get_conversation': {
        'fn': whatsapp_get_conversation,
        'description': 'Get conversation thread with a specific contact',
        'params': ['contact_phone', 'phone', 'limit']
    },
    'whatsapp_list_phones': {
        'fn': whatsapp_list_phones,
        'description': 'List connected WhatsApp phone numbers and their status',
        'params': []
    },
}


class MCPHandler(BaseHTTPRequestHandler):
    """Simple JSON-RPC style MCP server over HTTP."""

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == '/health':
            self._respond(200, {'status': 'running', 'tools': list(TOOLS.keys())})
        elif parsed.path == '/tools':
            tools_list = []
            for name, info in TOOLS.items():
                tools_list.append({
                    'name': name,
                    'description': info['description'],
                    'parameters': info['params']
                })
            self._respond(200, {'tools': tools_list})
        elif parsed.path == '/call':
            # GET-based tool call: /call?tool=name&param1=val1&...
            qs = parse_qs(parsed.query)
            tool_name = qs.get('tool', [''])[0]
            if tool_name not in TOOLS:
                self._respond(404, {'error': f'Unknown tool: {tool_name}'})
                return
            params = {k: v[0] for k, v in qs.items() if k != 'tool'}
            try:
                result = TOOLS[tool_name]['fn'](params)
                self._respond(200, {'result': result})
            except Exception as e:
                self._respond(500, {'error': str(e)})
        else:
            self._respond(404, {'error': 'Not found'})

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == '/call':
            content_len = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_len).decode()
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self._respond(400, {'error': 'Invalid JSON'})
                return

            tool_name = data.get('tool', '')
            params = data.get('params', {})

            if tool_name not in TOOLS:
                self._respond(404, {'error': f'Unknown tool: {tool_name}'})
                return

            try:
                result = TOOLS[tool_name]['fn'](params)
                self._respond(200, {'result': result})
            except Exception as e:
                self._respond(500, {'error': str(e)})

        elif parsed.path == '/jsonrpc':
            # JSON-RPC 2.0 compatibility
            content_len = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_len).decode()
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self._respond(400, {'jsonrpc': '2.0', 'error': {'code': -32700, 'message': 'Parse error'}})
                return

            method = data.get('method', '')
            params = data.get('params', {})
            req_id = data.get('id', 1)

            if method == 'tools/list':
                tools_list = []
                for name, info in TOOLS.items():
                    tools_list.append({
                        'name': name,
                        'description': info['description'],
                        'inputSchema': {
                            'type': 'object',
                            'properties': {p: {'type': 'string'} for p in info['params']}
                        }
                    })
                self._respond(200, {'jsonrpc': '2.0', 'id': req_id, 'result': {'tools': tools_list}})

            elif method == 'tools/call':
                tool_name = params.get('name', '')
                tool_args = params.get('arguments', {})
                if tool_name not in TOOLS:
                    self._respond(200, {'jsonrpc': '2.0', 'id': req_id,
                                       'error': {'code': -32601, 'message': f'Unknown tool: {tool_name}'}})
                    return
                try:
                    result = TOOLS[tool_name]['fn'](tool_args)
                    self._respond(200, {'jsonrpc': '2.0', 'id': req_id,
                                       'result': {'content': [{'type': 'text', 'text': json.dumps(result, indent=2)}]}})
                except Exception as e:
                    self._respond(200, {'jsonrpc': '2.0', 'id': req_id,
                                       'error': {'code': -32000, 'message': str(e)}})

            elif method == 'initialize':
                self._respond(200, {'jsonrpc': '2.0', 'id': req_id, 'result': {
                    'protocolVersion': '2024-11-05',
                    'serverInfo': {'name': 'whatsapp-mcp', 'version': '1.0.0'},
                    'capabilities': {'tools': {}}
                }})
            else:
                self._respond(200, {'jsonrpc': '2.0', 'id': req_id,
                                   'error': {'code': -32601, 'message': f'Unknown method: {method}'}})
        else:
            self._respond(404, {'error': 'Not found'})

    def _respond(self, code, data):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def log_message(self, format, *args):
        pass  # suppress access logs


def main():
    print(f"[mcp] WhatsApp MCP Server starting on port {PORT}")
    print(f"[mcp] Tools available: {list(TOOLS.keys())}")
    server = HTTPServer(('0.0.0.0', PORT), MCPHandler)
    print(f"[mcp] Listening on 0.0.0.0:{PORT}")
    server.serve_forever()


if __name__ == '__main__':
    main()
