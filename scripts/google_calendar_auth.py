"""One-time helper (Pedro runs it on his own computer): signs in to Google, asks for permission to write
calendar events, and prints the refresh token to put in the Hub's environment.

Usage:
    python scripts/google_calendar_auth.py <client_id> <client_secret>

Needs: a Google Cloud project with the Calendar API on, its OAuth consent screen PUBLISHED (Publishing
status "In production"; Google's "unverified app" warning is fine for personal use) and an OAuth client of
type "Desktop app" (docs/SYSADMIN.md, Google Calendar). Publish first: in Testing mode Google expires the
token after 7 days. Uses only the standard library and httpx."""

import http.server
import secrets
import sys
import threading
import urllib.parse
import webbrowser

import httpx

SCOPE = "https://www.googleapis.com/auth/calendar.events"
PORT = 8765


def main(client_id: str, client_secret: str) -> int:
    redirect = f"http://127.0.0.1:{PORT}/"
    state = secrets.token_urlsafe(16)
    result: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if query.get("state") == [state] and "code" in query:
                result["code"] = query["code"][0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"You can close this tab and go back to the terminal.")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    thread = threading.Thread(target=server.handle_request)
    thread.start()
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state})
    print("Opening your browser to ask Google for permission. If it does not open, visit:\n" + url)
    webbrowser.open(url)
    thread.join(timeout=300)
    if "code" not in result:
        print("No answer from Google within five minutes. Run the script again.")
        return 1
    resp = httpx.post("https://oauth2.googleapis.com/token", data={
        "code": result["code"], "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": redirect, "grant_type": "authorization_code"})
    token = resp.json().get("refresh_token")
    if resp.status_code != 200 or not token:
        print(f"Google did not give a refresh token (HTTP {resp.status_code}): {resp.text[:300]}")
        return 1
    print("\nGOOGLE_OAUTH_REFRESH_TOKEN=" + token)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
