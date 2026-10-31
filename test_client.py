import hmac
import hashlib
import time
import requests
from urllib.parse import urlparse

# 1. Configuration
API_URL = "http://127.0.0.1:8000/api/v1/companies/"
SECRET_KEY = b"bluestock_commercial_secret_998877" # Must match your .env

# 2. Generate Timestamp and Path
timestamp = str(int(time.time()))
parsed_url = urlparse(API_URL)
path = parsed_url.path

# 3. Create the HMAC Signature
message = f"{timestamp}{path}".encode('utf-8')
signature = hmac.new(SECRET_KEY, message, hashlib.sha256).hexdigest()

# 4. Set Headers
headers = {
    'X-Timestamp': timestamp,
    'X-Signature': signature
}

# 5. Make the Secure Request
print(f"Sending request to {API_URL}...")
print(f"Here are your headers to copy: {headers}")

try:
    response = requests.get(API_URL, headers=headers)
    
    if response.status_code == 200:
        print("\nSuccess! Secure connection established.")
        data = response.json()
        if data:
            print(f"First Company Retrieved: {data[0]['company_name']} ({data[0]['symbol']})")
    else:
        print(f"\nFailed. Status Code: {response.status_code}")
        print(response.text)
except requests.exceptions.ConnectionError:
    print("\nError: Could not connect to Django. Is the server running in the webapp folder?")
    
    
