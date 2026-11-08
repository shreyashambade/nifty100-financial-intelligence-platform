#!/usr/bin/env python3
"""
Test client for NiftyIQ Phase 2 Secure Channel Partner API.
Demonstrates canonical HMAC-SHA256 signing, nonce generation, and replay protection.
"""
import sys
import hmac
import hashlib
import time
import secrets
import argparse
import json
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


def make_hmac_request(url, api_key, secret_key, method="GET", body=None):
    parsed = urlparse(url)
    path = parsed.path
    if parsed.query:
        path = f"{parsed.path}?{parsed.query}"

    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    body_bytes = body.encode('utf-8') if body else b""

    # Canonical string format: METHOD\nPATH?QUERY\nTIMESTAMP\nNONCE\nBODY_HASH
    body_hash = hashlib.sha256(body_bytes).hexdigest().lower()
    canonical_string = f"{method.upper()}\n{path}\n{timestamp}\n{nonce}\n{body_hash}"

    signature = hmac.new(
        secret_key.encode('utf-8'),
        canonical_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest().lower()

    headers = {
        'X-API-Key-ID': api_key,
        'X-API-Key': api_key,
        'X-Timestamp': timestamp,
        'X-Nonce': nonce,
        'X-Signature': signature,
        'Accept': 'application/json',
    }
    if body_bytes:
        headers['Content-Type'] = 'application/json'

    print("=" * 65)
    print(f"Request: {method} {url}")
    print("Canonical String:")
    for line in canonical_string.split('\n'):
        print(f"  > {line}")
    print("\nSecurity Headers:")
    print(f"  X-API-Key-ID: {api_key}")
    print(f"  X-Timestamp:  {timestamp}")
    print(f"  X-Nonce:      {nonce}")
    print(f"  X-Signature:  {signature}")
    print("=" * 65)

    req = Request(url, data=body_bytes if method in ['POST', 'PUT'] else None, headers=headers, method=method)

    try:
        with urlopen(req, timeout=10) as response:
            status_code = response.getcode()
            content = response.read().decode('utf-8')
            print(f"\n[HTTP {status_code} OK]")
            try:
                parsed_json = json.loads(content)
                print(json.dumps(parsed_json, indent=2)[:1500])
                if len(content) > 1500:
                    print(f"\n... [Truncated {len(content) - 1500} bytes]")
            except json.JSONDecodeError:
                print(content)
            return True
    except HTTPError as e:
        print(f"\n[HTTP {e.code} Error]: {e.reason}")
        try:
            error_body = e.read().decode('utf-8')
            print(json.dumps(json.loads(error_body), indent=2))
        except Exception:
            pass
        return False
    except URLError as e:
        print(f"\n[Connection Error]: {e.reason}")
        print("Ensure the Django development server is running (e.g. python manage.py runserver).")
        return False


def main():
    parser = argparse.ArgumentParser(description="Test NiftyIQ Channel Partner HMAC-SHA256 API")
    parser.add_argument('--api-key', required=True, help="Channel Partner API Key (UUID)")
    parser.add_argument('--secret-key', required=True, help="Channel Partner Secret Key (Hex)")
    parser.add_argument('--url', default="http://127.0.0.1:8000/api/partner/v1/companies/", help="API Endpoint URL")
    parser.add_argument('--symbol', default=None, help="Specific company symbol to test (e.g., RELIANCE, TCS)")

    args = parser.parse_args()

    target_url = args.url
    if args.symbol:
        base = target_url.rstrip('/')
        if not base.endswith(args.symbol.upper()):
            target_url = f"{base}/{args.symbol.upper()}/"

    make_hmac_request(target_url, args.api_key, args.secret_key)


if __name__ == '__main__':
    main()
