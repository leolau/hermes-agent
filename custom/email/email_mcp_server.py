#!/usr/bin/env python3
"""
Email + Contact MCP Server for Hermes Agent

Exposes email data and unified contact management as MCP tools.
Runs on port 8651.
"""

import json
import sqlite3
import os
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import uuid

DB_PATH = '/opt/data/whatsapp-messages/whatsapp_data.db'
CONFIG_PATH = '/opt/data/email-messages/config.json'
PORT = 8651

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)

# === EMAIL TOOLS ===

def email_search(params):
    query = params.get('query', '')
    from_addr = params.get('from', '')
    account = params.get('account', '')
    date_from = params.get('date_from', '')
    date_to = params.get('date_to', '')
    has_attachments = params.get('has_attachments')
    limit = int(params.get('limit', 50))

    db = get_db()
    sql = "SELECT id, account_id, from_addr, from_name, subject, body_text, has_attachments, received_at, thread_id FROM email_messages WHERE 1=1"
    args = []

    if query:
        sql += " AND (subject LIKE ? OR body_text LIKE ?)"
        args.extend([f'%{query}%', f'%{query}%'])
    if from_addr:
        sql += " AND from_addr LIKE ?"
        args.append(f'%{from_addr}%')
    if account:
        sql += " AND account_id = ?"
        args.append(account)
    if date_from:
        sql += " AND received_at >= ?"
        args.append(date_from)
    if date_to:
        sql += " AND received_at <= ?"
        args.append(date_to)
    if has_attachments is not None:
        sql += " AND has_attachments = ?"
        args.append(1 if has_attachments else 0)

    sql += " ORDER BY received_at DESC LIMIT ?"
    args.append(limit)

    rows = db.execute(sql, args).fetchall()
    db.close()
    return [dict(r) for r in rows]


def email_get_recent(params):
    account = params.get('account', '')
    from_addr = params.get('from', '')
    limit = int(params.get('limit', 20))

    db = get_db()
    sql = "SELECT id, account_id, from_addr, from_name, subject, has_attachments, received_at, thread_id FROM email_messages WHERE 1=1"
    args = []

    if account:
        sql += " AND account_id = ?"
        args.append(account)
    if from_addr:
        sql += " AND from_addr LIKE ?"
        args.append(f'%{from_addr}%')

    sql += " ORDER BY received_at DESC LIMIT ?"
    args.append(limit)

    rows = db.execute(sql, args).fetchall()
    db.close()
    return [dict(r) for r in rows]


def email_get_thread(params):
    thread_id = params.get('thread_id', '')
    account = params.get('account', '')

    if not thread_id:
        return {'error': 'thread_id required'}

    db = get_db()
    sql = "SELECT id, account_id, from_addr, from_name, subject, body_text, has_attachments, received_at FROM email_messages WHERE thread_id = ?"
    args = [thread_id]

    if account:
        sql += " AND account_id = ?"
        args.append(account)

    sql += " ORDER BY received_at ASC"
    rows = db.execute(sql, args).fetchall()
    db.close()
    return [dict(r) for r in rows]


def email_list_tasks(params):
    status = params.get('status', '')
    priority = params.get('priority', '')
    date_from = params.get('date_from', '')

    db = get_db()
    sql = "SELECT * FROM email_tasks WHERE 1=1"
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


def email_list_notes(params):
    date_from = params.get('date_from', '')
    keyword = params.get('keyword', '')

    db = get_db()
    sql = "SELECT * FROM email_notes WHERE 1=1"
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


def email_get_escalations(params):
    status = params.get('status', 'pending')
    channel = params.get('channel', '')

    db = get_db()
    sql = "SELECT * FROM escalations WHERE 1=1"
    args = []

    if status and status != 'all':
        sql += " AND status = ?"
        args.append(status)
    if channel:
        sql += " AND channel = ?"
        args.append(channel)

    sql += " ORDER BY created_at DESC LIMIT 50"
    rows = db.execute(sql, args).fetchall()
    db.close()
    return [dict(r) for r in rows]


def email_resolve_escalation(params):
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


def email_get_stats(params):
    account = params.get('account', '')
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

    base = " AND account_id = ?" if account else ""
    args_base = [account] if account else []

    email_count = db.execute(
        f"SELECT COUNT(*) as c FROM email_messages WHERE received_at >= ?{base}",
        [since] + args_base
    ).fetchone()['c']

    task_count = db.execute(
        f"SELECT COUNT(*) as c FROM email_tasks WHERE created_at >= ?{' AND account_id = ?' if account else ''}",
        [since] + args_base
    ).fetchone()['c']

    esc_count = db.execute(
        "SELECT COUNT(*) as c FROM escalations WHERE channel = 'email' AND created_at >= ?",
        [since]
    ).fetchone()['c']

    top_senders = db.execute(
        f"SELECT from_addr, from_name, COUNT(*) as cnt FROM email_messages WHERE received_at >= ?{base} GROUP BY from_addr ORDER BY cnt DESC LIMIT 5",
        [since] + args_base
    ).fetchall()

    db.close()
    return {
        'period': period,
        'account_filter': account or 'all',
        'email_count': email_count,
        'task_count': task_count,
        'escalation_count': esc_count,
        'top_senders': [dict(s) for s in top_senders],
    }


def email_list_accounts(params):
    db = get_db()
    accounts = db.execute("SELECT * FROM email_accounts").fetchall()
    result = []
    for a in accounts:
        msg_count = db.execute(
            "SELECT COUNT(*) as c FROM email_messages WHERE account_id = ?",
            (a['id'],)
        ).fetchone()['c']
        result.append({**dict(a), 'message_count': msg_count})
    db.close()
    return result


# === CONTACT TOOLS ===

def contact_search(params):
    query = params.get('query', '')
    if not query:
        return {'error': 'query required'}

    db = get_db()
    sql = """
        SELECT c.*, GROUP_CONCAT(h.handle_type || ':' || h.handle_value, ', ') as handles
        FROM unified_contacts c
        LEFT JOIN contact_handles h ON c.id = h.contact_id
        WHERE c.display_name LIKE ?
           OR c.id IN (SELECT contact_id FROM contact_handles WHERE handle_value LIKE ?)
        GROUP BY c.id
        ORDER BY c.updated_at DESC
    """
    rows = db.execute(sql, [f'%{query}%', f'%{query}%']).fetchall()
    db.close()
    return [dict(r) for r in rows]


def contact_get(params):
    contact_id = params.get('contact_id', '')
    if not contact_id:
        return {'error': 'contact_id required'}

    db = get_db()
    contact = db.execute("SELECT * FROM unified_contacts WHERE id = ?", (contact_id,)).fetchone()
    if not contact:
        db.close()
        return {'error': 'Contact not found'}

    handles = db.execute("SELECT * FROM contact_handles WHERE contact_id = ?", (contact_id,)).fetchall()
    db.close()

    return {
        **dict(contact),
        'handles': [dict(h) for h in handles]
    }


def contact_get_history(params):
    contact_id = params.get('contact_id', '')
    channel = params.get('channel', '')
    limit = int(params.get('limit', 30))

    if not contact_id:
        return {'error': 'contact_id required'}

    db = get_db()
    handles = db.execute("SELECT * FROM contact_handles WHERE contact_id = ?", (contact_id,)).fetchall()

    results = []

    for h in handles:
        if h['handle_type'] == 'phone' and (not channel or channel == 'whatsapp'):
            phone = h['handle_value']
            msgs = db.execute(
                "SELECT 'whatsapp' as channel, id, source_phone, sender_name, text, timestamp as ts FROM messages WHERE sender_phone LIKE ? ORDER BY timestamp DESC LIMIT ?",
                (f'%{phone.lstrip("+")}%', limit)
            ).fetchall()
            results.extend([dict(m) for m in msgs])

        elif h['handle_type'] == 'email' and (not channel or channel == 'email'):
            addr = h['handle_value']
            emails = db.execute(
                "SELECT 'email' as channel, id, account_id, from_name, subject, received_at as ts FROM email_messages WHERE from_addr = ? ORDER BY received_at DESC LIMIT ?",
                (addr, limit)
            ).fetchall()
            results.extend([dict(e) for e in emails])

    # Sort combined results by timestamp
    results.sort(key=lambda x: x.get('ts', ''), reverse=True)
    db.close()
    return results[:limit]


def contact_merge(params):
    id1 = params.get('contact_id_1', '')
    id2 = params.get('contact_id_2', '')

    if not id1 or not id2:
        return {'error': 'contact_id_1 and contact_id_2 required'}

    db = get_db()
    c1 = db.execute("SELECT * FROM unified_contacts WHERE id = ?", (id1,)).fetchone()
    c2 = db.execute("SELECT * FROM unified_contacts WHERE id = ?", (id2,)).fetchone()

    if not c1 or not c2:
        db.close()
        return {'error': 'One or both contacts not found'}

    # Move all handles from c2 to c1
    db.execute("UPDATE contact_handles SET contact_id = ? WHERE contact_id = ?", (id1, id2))
    # Update escalations
    db.execute("UPDATE escalations SET contact_id = ? WHERE contact_id = ?", (id1, id2))
    # Increment merge count
    now = datetime.now(timezone.utc).isoformat()
    db.execute("UPDATE unified_contacts SET auto_merged_count = auto_merged_count + 1, updated_at = ? WHERE id = ?", (now, id1))
    # Delete c2
    db.execute("DELETE FROM unified_contacts WHERE id = ?", (id2,))
    db.commit()
    db.close()

    return {'status': 'merged', 'kept': id1, 'removed': id2}


def contact_split(params):
    contact_id = params.get('contact_id', '')
    handle_id = params.get('handle_id', '')

    if not contact_id or not handle_id:
        return {'error': 'contact_id and handle_id required'}

    db = get_db()
    handle = db.execute("SELECT * FROM contact_handles WHERE id = ? AND contact_id = ?", (handle_id, contact_id)).fetchone()
    if not handle:
        db.close()
        return {'error': 'Handle not found or does not belong to contact'}

    # Create new contact
    new_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO unified_contacts (id, display_name, is_family, is_vip, created_at, updated_at) VALUES (?, ?, 0, 0, ?, ?)",
        (new_id, handle['display_name'] or handle['handle_value'], now, now)
    )
    db.execute("UPDATE contact_handles SET contact_id = ? WHERE id = ?", (new_id, handle_id))
    db.commit()
    db.close()

    return {'status': 'split', 'new_contact_id': new_id, 'handle_id': handle_id}


def contact_list(params):
    sort_by = params.get('sort_by', 'updated_at')
    is_family = params.get('is_family')
    is_vip = params.get('is_vip')

    db = get_db()
    sql = """
        SELECT c.*, GROUP_CONCAT(h.handle_type || ':' || h.handle_value, ', ') as handles,
               SUM(h.message_count) as total_messages
        FROM unified_contacts c
        LEFT JOIN contact_handles h ON c.id = h.contact_id
        WHERE 1=1
    """
    args = []

    if is_family is not None:
        sql += " AND c.is_family = ?"
        args.append(1 if is_family else 0)
    if is_vip is not None:
        sql += " AND c.is_vip = ?"
        args.append(1 if is_vip else 0)

    sql += " GROUP BY c.id"

    if sort_by == 'messages':
        sql += " ORDER BY total_messages DESC"
    else:
        sql += " ORDER BY c.updated_at DESC"

    rows = db.execute(sql, args).fetchall()
    db.close()
    return [dict(r) for r in rows]


def contact_pending_merges(params):
    db = get_db()
    rows = db.execute("""
        SELECT s.*, c.display_name as candidate_name
        FROM contact_merge_suggestions s
        LEFT JOIN unified_contacts c ON s.candidate_contact_id = c.id
        WHERE s.status = 'pending'
        ORDER BY s.created_at DESC
    """).fetchall()
    db.close()
    return [dict(r) for r in rows]


def contact_resolve_merge(params):
    suggestion_id = params.get('suggestion_id', '')
    action = params.get('action', '')

    if not suggestion_id or action not in ('approve', 'reject', 'ignore'):
        return {'error': 'suggestion_id and action (approve/reject/ignore) required'}

    db = get_db()
    now = datetime.now(timezone.utc).isoformat()

    suggestion = db.execute("SELECT * FROM contact_merge_suggestions WHERE id = ?", (suggestion_id,)).fetchone()
    if not suggestion:
        db.close()
        return {'error': 'Suggestion not found'}

    if action == 'approve':
        # Merge: move handles from new_contact to candidate
        new_id = suggestion['new_contact_id']
        candidate_id = suggestion['candidate_contact_id']
        if new_id and candidate_id:
            db.execute("UPDATE contact_handles SET contact_id = ? WHERE contact_id = ?", (candidate_id, new_id))
            db.execute("UPDATE escalations SET contact_id = ? WHERE contact_id = ?", (candidate_id, new_id))
            db.execute("UPDATE unified_contacts SET auto_merged_count = auto_merged_count + 1, updated_at = ? WHERE id = ?", (now, candidate_id))
            db.execute("DELETE FROM unified_contacts WHERE id = ?", (new_id,))
        db.execute("UPDATE contact_merge_suggestions SET status = 'approved', resolved_at = ? WHERE id = ?", (now, suggestion_id))
    elif action == 'reject':
        db.execute("UPDATE contact_merge_suggestions SET status = 'rejected', resolved_at = ? WHERE id = ?", (now, suggestion_id))
    elif action == 'ignore':
        db.execute("UPDATE contact_merge_suggestions SET status = 'ignored', resolved_at = ? WHERE id = ?", (now, suggestion_id))

    db.commit()
    db.close()
    return {'status': action, 'suggestion_id': suggestion_id}


# Tool registry
TOOLS = {
    # Email tools
    'email_search': {
        'fn': email_search,
        'description': 'Search emails by text, sender, date, account',
        'params': ['query', 'from', 'account', 'date_from', 'date_to', 'has_attachments', 'limit']
    },
    'email_get_recent': {
        'fn': email_get_recent,
        'description': 'Get recent emails',
        'params': ['account', 'from', 'limit']
    },
    'email_get_thread': {
        'fn': email_get_thread,
        'description': 'Get full email thread by thread_id',
        'params': ['thread_id', 'account']
    },
    'email_list_tasks': {
        'fn': email_list_tasks,
        'description': 'List extracted tasks from emails',
        'params': ['status', 'priority', 'date_from']
    },
    'email_list_notes': {
        'fn': email_list_notes,
        'description': 'List extracted notes from emails',
        'params': ['date_from', 'keyword']
    },
    'email_get_escalations': {
        'fn': email_get_escalations,
        'description': 'Get escalations (filter by channel)',
        'params': ['status', 'channel']
    },
    'email_resolve_escalation': {
        'fn': email_resolve_escalation,
        'description': 'Mark an escalation as resolved',
        'params': ['escalation_id']
    },
    'email_get_stats': {
        'fn': email_get_stats,
        'description': 'Get email statistics',
        'params': ['account', 'period']
    },
    'email_list_accounts': {
        'fn': email_list_accounts,
        'description': 'List connected email accounts and status',
        'params': []
    },
    # Contact tools
    'contact_search': {
        'fn': contact_search,
        'description': 'Search contacts by name, phone, or email',
        'params': ['query']
    },
    'contact_get': {
        'fn': contact_get,
        'description': 'Get full contact details with all handles',
        'params': ['contact_id']
    },
    'contact_get_history': {
        'fn': contact_get_history,
        'description': 'Get all messages from a contact across WhatsApp and Email',
        'params': ['contact_id', 'channel', 'limit']
    },
    'contact_merge': {
        'fn': contact_merge,
        'description': 'Manually merge two contacts',
        'params': ['contact_id_1', 'contact_id_2']
    },
    'contact_split': {
        'fn': contact_split,
        'description': 'Detach a handle from a contact into a new contact',
        'params': ['contact_id', 'handle_id']
    },
    'contact_list': {
        'fn': contact_list,
        'description': 'List all contacts with handles',
        'params': ['sort_by', 'is_family', 'is_vip']
    },
    'contact_pending_merges': {
        'fn': contact_pending_merges,
        'description': 'Show pending contact merge suggestions',
        'params': []
    },
    'contact_resolve_merge': {
        'fn': contact_resolve_merge,
        'description': 'Approve, reject, or ignore a contact merge suggestion',
        'params': ['suggestion_id', 'action']
    },
}


class MCPHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == '/health':
            self._respond(200, {'status': 'running', 'tools': list(TOOLS.keys()), 'tool_count': len(TOOLS)})
        elif parsed.path == '/tools':
            tools_list = [{'name': n, 'description': t['description'], 'parameters': t['params']} for n, t in TOOLS.items()]
            self._respond(200, {'tools': tools_list})
        elif parsed.path == '/call':
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
                    'serverInfo': {'name': 'email-contacts-mcp', 'version': '1.0.0'},
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
        pass


def main():
    print(f"[email-mcp] Email + Contact MCP Server starting on port {PORT}")
    print(f"[email-mcp] Tools available: {len(TOOLS)} ({list(TOOLS.keys())})")
    server = HTTPServer(('0.0.0.0', PORT), MCPHandler)
    print(f"[email-mcp] Listening on 0.0.0.0:{PORT}")
    server.serve_forever()


if __name__ == '__main__':
    main()
