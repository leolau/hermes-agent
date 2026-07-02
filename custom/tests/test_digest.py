#!/usr/bin/env python3
"""Run one merged digest for testing."""
import sys, os
sys.path.insert(0, '/opt/data/whatsapp-messages')

# Exec the digest module and call generate_digest
with open('/opt/data/whatsapp-messages/digest_cron.py') as f:
    code = f.read()

# Extract everything before main()
parts = code.split('def main')
exec(parts[0])
generate_digest()
print("Digest generated successfully")
