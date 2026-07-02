#!/usr/bin/env python3
"""
Google Calendar OAuth2 Authentication Flow

One-time script to obtain refresh tokens for each Google account.
Run locally (not on the server) — it opens a browser for consent.

Usage:
    python3 calendar_auth.py --email leo11lau@gmail.com

Environment variables required:
    GCAL_CLIENT_ID      - OAuth2 client ID from Google Cloud Console
    GCAL_CLIENT_SECRET  - OAuth2 client secret

The script will:
1. Open a browser for the user to approve access
2. Listen on localhost for the OAuth callback
3. Exchange the auth code for access + refresh tokens
4. Print the refresh token to save as a Devin secret
"""

import http.server
import json
import os
import sys
import urllib.parse
import urllib.request
import webbrowser

SCOPES = 'https://www.googleapis.com/auth/calendar'
REDIRECT_PORT = 8090
REDIRECT_URI = f'http://localhost:{REDIRECT_PORT}'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'


def get_auth_url(client_id, email):
    """Build the OAuth2 authorization URL."""
    params = urllib.parse.urlencode({
        'client_id': client_id,
        'redirect_uri': REDIRECT_URI,
        'response_type': 'code',
        'scope': SCOPES,
        'access_type': 'offline',
        'prompt': 'consent',
        'login_hint': email,
    })
    return f'{AUTH_URL}?{params}'


def exchange_code(code, client_id, client_secret):
    """Exchange authorization code for tokens."""
    data = urllib.parse.urlencode({
        'code': code,
        'client_id': client_id,
        'client_secret': client_secret,
        'redirect_uri': REDIRECT_URI,
        'grant_type': 'authorization_code',
    }).encode()

    req = urllib.request.Request(TOKEN_URL, data=data, method='POST')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')

    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def refresh_access_token(refresh_token, client_id, client_secret):
    """Get a new access token using a refresh token."""
    data = urllib.parse.urlencode({
        'refresh_token': refresh_token,
        'client_id': client_id,
        'client_secret': client_secret,
        'grant_type': 'refresh_token',
    }).encode()

    req = urllib.request.Request(TOKEN_URL, data=data, method='POST')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')

    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read().decode())
        return result.get('access_token')


class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    """Handles the OAuth2 redirect callback."""
    auth_code = None

    def do_GET(self):
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)

        if 'code' in params:
            OAuthCallbackHandler.auth_code = params['code'][0]
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<html><body><h2>Authorization successful!</h2>'
                             b'<p>You can close this tab and return to the terminal.</p>'
                             b'</body></html>')
        elif 'error' in params:
            self.send_response(400)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            error = params.get('error', ['unknown'])[0]
            self.wfile.write(f'<html><body><h2>Error: {error}</h2></body></html>'.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # suppress HTTP logs


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Google Calendar OAuth2 auth flow')
    parser.add_argument('--email', required=True, help='Google account email')
    args = parser.parse_args()

    client_id = os.environ.get('GCAL_CLIENT_ID', '')
    client_secret = os.environ.get('GCAL_CLIENT_SECRET', '')

    if not client_id or not client_secret:
        print("ERROR: Set GCAL_CLIENT_ID and GCAL_CLIENT_SECRET environment variables")
        sys.exit(1)

    auth_url = get_auth_url(client_id, args.email)
    print(f"\nOpening browser for {args.email}...")
    print(f"If the browser doesn't open, visit this URL:\n{auth_url}\n")
    webbrowser.open(auth_url)

    print(f"Waiting for OAuth callback on port {REDIRECT_PORT}...")
    server = http.server.HTTPServer(('localhost', REDIRECT_PORT), OAuthCallbackHandler)
    server.handle_request()

    if not OAuthCallbackHandler.auth_code:
        print("ERROR: No authorization code received")
        sys.exit(1)

    print("Exchanging code for tokens...")
    tokens = exchange_code(OAuthCallbackHandler.auth_code, client_id, client_secret)

    if 'error' in tokens:
        print(f"ERROR: {tokens['error']} - {tokens.get('error_description', '')}")
        sys.exit(1)

    refresh_token = tokens.get('refresh_token', '')
    access_token = tokens.get('access_token', '')

    if not refresh_token:
        print("WARNING: No refresh token received. You may need to revoke access and re-authorize.")
        print("Go to https://myaccount.google.com/permissions and remove 'Hermes Agent'")

    print(f"\n{'='*60}")
    print(f"Account: {args.email}")
    print(f"Refresh Token: {refresh_token}")
    print(f"{'='*60}")
    print(f"\nSave this refresh token as a Devin secret.")
    print(f"Access token (expires in {tokens.get('expires_in', '?')}s): {access_token[:20]}...")


if __name__ == '__main__':
    main()
