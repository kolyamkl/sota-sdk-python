"""Credential storage and device-code auth flow (D-08, D-09, D-10)."""
import json
import os
import stat
import sys
import time
import webbrowser

import httpx

CREDENTIALS_DIR = os.path.expanduser("~/.sota")
CREDENTIALS_FILE = os.path.join(CREDENTIALS_DIR, "credentials")
DEFAULT_API_URL = os.environ.get(
    "SOTA_API_URL", "https://sota-backend-production.up.railway.app",
)


def save_credentials(data: dict) -> None:
    """Save credentials to ~/.sota/credentials with 600 permissions (D-09)."""
    os.makedirs(CREDENTIALS_DIR, exist_ok=True)
    with open(CREDENTIALS_FILE, "w") as f:
        json.dump(data, f, indent=2)
    # Set file permissions to 600 (owner read/write only)
    if os.name != "nt":  # Skip on Windows (Pitfall 6)
        os.chmod(CREDENTIALS_FILE, stat.S_IRUSR | stat.S_IWUSR)


def load_credentials() -> dict | None:
    """Load credentials from ~/.sota/credentials. Returns None if missing."""
    if not os.path.exists(CREDENTIALS_FILE):
        return None
    try:
        with open(CREDENTIALS_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def get_api_url() -> str:
    """Get API base URL from env or default."""
    return os.environ.get("SOTA_API_URL", DEFAULT_API_URL)


def device_code_login() -> dict:
    """Execute device-code login flow (D-08, D-10).
    1. Request device code
    2. Open browser for user to authorize
    3. Poll every 2s until authorized or expired
    Returns credentials dict with access_token, refresh_token, user_id, email."""
    api_url = get_api_url()

    # Step 1: Request device code
    resp = httpx.post(f"{api_url}/api/v1/auth/device-code")
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to create device code: {resp.text}")

    data = resp.json()
    device_code = data["device_code"]
    # verify_url is absolute (backend resolves DEVPORTAL_URL env var).
    verify_url = data["verify_url"]
    expires_in = data["expires_in"]

    print(f"\n  Your device code: {device_code}")
    print(f"  Opening browser to authorize...")
    print(f"  If browser doesn't open, visit: {verify_url}")
    print(f"  Code expires in {expires_in // 60} minutes.\n")

    # Try to open browser
    try:
        webbrowser.open(verify_url)
    except Exception:
        pass  # Browser may not be available (e.g., SSH)

    # Step 2: Poll every 2s (D-10)
    start = time.time()
    while time.time() - start < expires_in:
        time.sleep(2)
        poll_resp = httpx.post(
            f"{api_url}/api/v1/auth/device-poll",
            json={"device_code": device_code},
        )

        if poll_resp.status_code == 200:
            result = poll_resp.json()
            if result.get("status") == "authorized":
                credentials = {
                    "access_token": result["access_token"],
                    "refresh_token": result["refresh_token"],
                    "user_id": result["user_id"],
                    "email": result["email"],
                }
                save_credentials(credentials)
                return credentials
            # Still pending, continue polling
        elif poll_resp.status_code == 410:
            raise RuntimeError("Device code expired. Please try again.")
        elif poll_resp.status_code == 404:
            raise RuntimeError("Device code not found.")

        sys.stdout.write(".")
        sys.stdout.flush()

    raise RuntimeError("Device code expired (timeout). Please try again.")
