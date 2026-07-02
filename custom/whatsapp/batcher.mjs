/**
 * WhatsApp Unified Batcher
 * 
 * Polls both Bridge A (port 3000) and Bridge B (port 3001) for messages.
 * - Writes raw messages to SQLite immediately
 * - Groups messages by sender+source_phone with 5s debounce window
 * - Emits completed batches for downstream triage processing
 * - Downloads media to /opt/data/whatsapp-messages/media/
 * - Updates contacts table
 */

import { createRequire } from 'module';
const require = createRequire(import.meta.url);
const Database = require('better-sqlite3');
const { randomUUID } = await import('crypto');
const { writeFileSync, mkdirSync, existsSync, readFileSync } = await import('fs');
const { join } = await import('path');
const http = await import('http');

// Config
const CONFIG_PATH = '/opt/data/whatsapp-messages/config.json';
const DB_PATH = '/opt/data/whatsapp-messages/whatsapp_data.db';
const MEDIA_DIR = '/opt/data/whatsapp-messages/media';
const BATCH_OUTPUT_DIR = '/opt/data/whatsapp-messages/batches';

// Load config
function loadConfig() {
  return JSON.parse(readFileSync(CONFIG_PATH, 'utf-8'));
}

const config = loadConfig();
const BATCH_WINDOW_MS = (config.batching?.window_seconds || 5) * 1000;

// Ensure directories exist
mkdirSync(MEDIA_DIR, { recursive: true });
mkdirSync(BATCH_OUTPUT_DIR, { recursive: true });

// Open SQLite
const db = new Database(DB_PATH);
db.pragma('journal_mode = WAL');
db.pragma('busy_timeout = 5000');

// Prepare statements
const insertMessage = db.prepare(`
  INSERT OR IGNORE INTO messages (id, source_phone, sender_phone, sender_name, chat_id, is_group, text, media_type, media_path, media_mimetype, timestamp, received_at, batch_id, raw_json)
  VALUES (@id, @source_phone, @sender_phone, @sender_name, @chat_id, @is_group, @text, @media_type, @media_path, @media_mimetype, @timestamp, @received_at, @batch_id, @raw_json)
`);

const upsertContact = db.prepare(`
  INSERT INTO contacts (phone, name, is_family, first_seen, last_seen, message_count)
  VALUES (@phone, @name, 0, @now, @now, 1)
  ON CONFLICT(phone) DO UPDATE SET
    name = CASE WHEN @name IS NOT NULL AND @name != '' THEN @name ELSE contacts.name END,
    last_seen = @now,
    message_count = contacts.message_count + 1
`);

const checkFamily = db.prepare(`SELECT is_family FROM contacts WHERE phone = ?`);

// Batching state: key = "sender_phone:source_phone" -> { messages: [], timer: timeout, batch_id: string }
const pendingBatches = new Map();

// Family contacts from config (for quick lookup)
const familyPhones = new Set(
  (config.escalation?.criteria?.family_contacts || []).map(c => c.phone)
);

// Ensure family contacts are marked in DB
const markFamily = db.prepare(`UPDATE contacts SET is_family = 1, relation = ? WHERE phone = ?`);
for (const fc of (config.escalation?.criteria?.family_contacts || [])) {
  markFamily.run(fc.relation, fc.phone);
}

function httpGet(url) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, { timeout: 30000 }, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          resolve(JSON.parse(data));
        } catch (e) {
          resolve(data);
        }
      });
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('timeout')); });
  });
}

function extractSenderPhone(msg) {
  // WhatsApp JID format: "85294066060@s.whatsapp.net" or "85294066060-123456@g.us"
  const sender = msg.key?.remoteJid || msg.sender || '';
  if (sender.includes('@g.us')) {
    // Group message - use participant
    const participant = msg.key?.participant || msg.participant || sender;
    const match = participant.match(/^(\d+)/);
    return match ? '+' + match[1] : sender;
  }
  const match = sender.match(/^(\d+)/);
  return match ? '+' + match[1] : sender;
}

function extractChatId(msg) {
  return msg.key?.remoteJid || '';
}

function isGroupMessage(msg) {
  const jid = msg.key?.remoteJid || '';
  return jid.includes('@g.us');
}

function extractText(msg) {
  const m = msg.message || {};
  return m.conversation || 
         m.extendedTextMessage?.text || 
         m.imageMessage?.caption || 
         m.videoMessage?.caption ||
         m.documentMessage?.fileName ||
         '';
}

function extractMediaInfo(msg) {
  const m = msg.message || {};
  if (m.imageMessage) return { type: 'image', mimetype: m.imageMessage.mimetype };
  if (m.videoMessage) return { type: 'video', mimetype: m.videoMessage.mimetype };
  if (m.audioMessage) return { type: 'audio', mimetype: m.audioMessage.mimetype };
  if (m.documentMessage) return { type: 'document', mimetype: m.documentMessage.mimetype };
  if (m.stickerMessage) return { type: 'sticker', mimetype: m.stickerMessage.mimetype };
  return { type: null, mimetype: null };
}

function getSenderName(msg) {
  return msg.pushName || msg.verifiedBizName || null;
}

function processMessage(msg, sourcePhone) {
  const msgId = msg.key?.id || randomUUID();
  const senderPhone = extractSenderPhone(msg);
  const senderName = getSenderName(msg);
  const chatId = extractChatId(msg);
  const isGroup = isGroupMessage(msg) ? 1 : 0;
  const text = extractText(msg);
  const mediaInfo = extractMediaInfo(msg);
  const timestamp = msg.messageTimestamp 
    ? new Date(Number(msg.messageTimestamp) * 1000).toISOString()
    : new Date().toISOString();
  const now = new Date().toISOString();

  // Write to SQLite immediately
  try {
    insertMessage.run({
      id: msgId,
      source_phone: sourcePhone,
      sender_phone: senderPhone,
      sender_name: senderName,
      chat_id: chatId,
      is_group: isGroup,
      text: text || null,
      media_type: mediaInfo.type,
      media_path: null, // media download handled separately
      media_mimetype: mediaInfo.mimetype,
      timestamp: timestamp,
      received_at: now,
      batch_id: null, // will be updated when batch completes
      raw_json: JSON.stringify(msg),
    });
  } catch (e) {
    if (!e.message.includes('UNIQUE constraint')) {
      console.error(`[batcher] DB insert error: ${e.message}`);
    }
    return; // duplicate message, skip
  }

  // Update contact
  try {
    upsertContact.run({
      phone: senderPhone,
      name: senderName,
      now: now,
    });
  } catch (e) {
    // ignore contact update errors
  }

  // Add to batch
  const batchKey = `${senderPhone}:${sourcePhone}`;
  if (pendingBatches.has(batchKey)) {
    const batch = pendingBatches.get(batchKey);
    batch.messages.push({ msgId, text, mediaInfo, timestamp, senderName, chatId, isGroup });
    // Reset the timer (extend the window)
    clearTimeout(batch.timer);
    batch.timer = setTimeout(() => flushBatch(batchKey), BATCH_WINDOW_MS);
  } else {
    const batchId = randomUUID();
    const timer = setTimeout(() => flushBatch(batchKey), BATCH_WINDOW_MS);
    pendingBatches.set(batchKey, {
      batchId,
      senderPhone,
      sourcePhone,
      messages: [{ msgId, text, mediaInfo, timestamp, senderName, chatId, isGroup }],
      timer,
      startedAt: now,
    });
  }

  console.log(`[batcher] ${sourcePhone} <- ${senderPhone}: ${(text || '[media]').substring(0, 60)}`);
}

function flushBatch(batchKey) {
  const batch = pendingBatches.get(batchKey);
  if (!batch) return;
  pendingBatches.delete(batchKey);

  const batchRecord = {
    batch_id: batch.batchId,
    sender_phone: batch.senderPhone,
    source_phone: batch.sourcePhone,
    message_count: batch.messages.length,
    messages: batch.messages,
    started_at: batch.startedAt,
    completed_at: new Date().toISOString(),
    is_family: familyPhones.has(batch.senderPhone),
  };

  // Update batch_id on all messages in this batch
  const updateBatchId = db.prepare(`UPDATE messages SET batch_id = ? WHERE id = ?`);
  const updateMany = db.transaction((msgs) => {
    for (const m of msgs) {
      updateBatchId.run(batch.batchId, m.msgId);
    }
  });
  updateMany(batch.messages);

  // Write batch to file for triage to pick up
  const batchFile = join(BATCH_OUTPUT_DIR, `${batch.batchId}.json`);
  writeFileSync(batchFile, JSON.stringify(batchRecord, null, 2));

  console.log(`[batcher] Batch completed: ${batch.senderPhone} on ${batch.sourcePhone} (${batch.messages.length} msgs) -> ${batchFile}`);
}

// Polling loop for a bridge
async function pollBridge(port, sourcePhone) {
  const url = `http://localhost:${port}/messages`;
  while (true) {
    try {
      const messages = await httpGet(url);
      if (Array.isArray(messages) && messages.length > 0) {
        for (const msg of messages) {
          processMessage(msg, sourcePhone);
        }
      }
    } catch (e) {
      if (e.message !== 'timeout') {
        console.error(`[batcher] Error polling port ${port}: ${e.message}`);
      }
    }
    // Small delay between polls if no messages (the bridge long-polls so this mostly just handles errors)
    await new Promise(r => setTimeout(r, 500));
  }
}

// Health check endpoint
const healthServer = http.createServer((req, res) => {
  if (req.url === '/health') {
    const stats = {
      status: 'running',
      pending_batches: pendingBatches.size,
      uptime: process.uptime(),
      phones: config.phones.map(p => ({ id: p.id, port: p.bridge_port })),
    };
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(stats));
  } else {
    res.writeHead(404);
    res.end('Not found');
  }
});
healthServer.listen(7900, '0.0.0.0');

// Start polling both bridges
console.log(`[batcher] Starting unified batcher (window: ${BATCH_WINDOW_MS}ms)`);
console.log(`[batcher] Polling Bridge A (port 3000) for phone1`);
console.log(`[batcher] Polling Bridge B (port 3001) for phone2`);

for (const phone of config.phones) {
  if (phone.enabled) {
    pollBridge(phone.bridge_port, phone.id);
  }
}

console.log(`[batcher] Health endpoint on port 7900`);
