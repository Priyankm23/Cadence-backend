import sys
import httpx
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# Configuration
CLIENT_ID = input("Enter your GMAIL_CLIENT_ID: ").strip()
CLIENT_SECRET = input("Enter your GMAIL_CLIENT_SECRET: ").strip()

# We will use 8090 to avoid typical 8080 conflicts
PORT = 8090
REDIRECT_URI = f"http://localhost:{PORT}/"
SCOPES = "https://www.googleapis.com/auth/gmail.send"

auth_code = None

class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        query = urlparse(self.path).query
        params = parse_qs(query)
        if "code" in params:
            auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>Authentication successful!</h1><p>You can close this window now and return to the terminal.</p>")
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"<h1>Authentication failed</h1>")

def main():
    print(f"\nStep 1: Ensure you have added '{REDIRECT_URI}' to Authorized Redirect URIs in your Google Cloud Console credential settings.")
    input("Press Enter when you have confirmed this redirect URI is added...")
    
    # 1. Direct user to Google's OAuth 2.0 authorization page
    auth_url = (
        f"https://accounts.google.com/o/oauth2/v2/auth?"
        f"client_id={CLIENT_ID}&"
        f"redirect_uri={REDIRECT_URI}&"
        f"response_type=code&"
        f"scope={SCOPES}&"
        f"access_type=offline&"
        f"prompt=consent"
    )
    
    print("\nOpening your browser to authenticate...")
    
    # 2. Try starting the local server to listen for the redirect callback
    try:
        server = HTTPServer(("localhost", PORT), CallbackHandler)
    except PermissionError:
        print(f"\n[Error] Port {PORT} is blocked or restricted by your operating system.")
        print("Please check if another application is running on this port, or try running the terminal as Administrator.")
        sys.exit(1)
    except OSError as e:
        print(f"\n[Error] Failed to bind to port {PORT}: {e}")
        print("This usually means another app is already using this port. Make sure to free it or edit this script to use a different port.")
        sys.exit(1)

    webbrowser.open(auth_url)
    print(f"Waiting for redirection on {REDIRECT_URI} ...")
    server.handle_request()  # handle a single callback request
    
    if not auth_code:
        print("Error: Did not receive authorization code.")
        sys.exit(1)
        
    # 3. Exchange the authorization code for tokens
    print("\nExchanging authorization code for tokens...")
    token_url = "https://oauth2.googleapis.com/token"
    payload = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": auth_code,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code"
    }
    
    try:
        response = httpx.post(token_url, data=payload, timeout=10.0)
        if response.status_code == 200:
            data = response.json()
            refresh_token = data.get("refresh_token")
            print("\n==================================================")
            print("SUCCESS! Your GMAIL_REFRESH_TOKEN is:")
            print(refresh_token)
            print("==================================================\n")
            print("Copy the token above and paste it in your .env file.")
        else:
            print(f"Error: Token exchange failed ({response.status_code}): {response.text}")
    except Exception as e:
        print(f"Exception during token exchange: {e}")

if __name__ == "__main__":
    main()
