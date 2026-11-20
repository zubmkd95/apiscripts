"""
clearpass_network_devices.py

WHAT IT DOES
    Logs in to Aruba ClearPass and:
      1. Downloads every network device (switches, APs, controllers) that is
         allowed to talk RADIUS/TACACS+ to ClearPass.
      2. Saves them to a CSV file you can open in Excel.
      3. Optionally looks up one endpoint (a device on the network) by MAC address.

WHY IT'S USEFUL IN PRODUCTION
    - Audit: "which switches are registered in ClearPass?" (compare against
      your real switch list to find ones that are missing).
    - Help desk: "is this MAC address known, and what is its status?"

SETUP
    1. In ClearPass: Administration > API Services > API Clients > create a
       client with grant type "client_credentials" (read-only access is enough).
    2. Set environment variables:
         export CLEARPASS_HOST="clearpass.example.edu"
         export CLEARPASS_CLIENT_ID="your-client-id"
         export CLEARPASS_CLIENT_SECRET="your-client-secret"
         export VERIFY_TLS="true"     # use "false" ONLY for lab servers with self-signed certs

USAGE
    python clearpass_network_devices.py
    python clearpass_network_devices.py --csv devices.csv
    python clearpass_network_devices.py --mac aa:bb:cc:dd:ee:ff
"""

# ---------------------------------------------------------------------------
# BLOCK 1: Imports (tools we borrow)
#   csv -> writes spreadsheet-style files
# ---------------------------------------------------------------------------
import argparse
import csv
import os
import sys

import requests

TIMEOUT = 30
PAGE_SIZE = 100


# ---------------------------------------------------------------------------
# BLOCK 2: Log in and get a token
# ClearPass uses the same "trade ID for a temporary token" idea.
# Here we send JSON (json=...) instead of form data (data=...).
# ---------------------------------------------------------------------------
def get_token(base_url, client_id, client_secret, verify):
    response = requests.post(
        f"{base_url}/api/oauth",
        json={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        verify=verify,       # verify=True checks the server's HTTPS certificate
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return response.json()["access_token"]


# ---------------------------------------------------------------------------
# BLOCK 3: Download all network devices, one page at a time
# "offset" means "skip this many results" and "limit" means "give me this many".
# Offset 0 limit 100 = first 100, then offset 100 = next 100, and so on.
# ---------------------------------------------------------------------------
def get_network_devices(base_url, headers, verify):
    devices = []
    offset = 0

    while True:
        response = requests.get(
            f"{base_url}/api/network-device",
            headers=headers,
            params={"offset": offset, "limit": PAGE_SIZE, "calculate_count": "true"},
            verify=verify,
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()

        # ClearPass puts the list under data["_embedded"]["items"].
        items = data.get("_embedded", {}).get("items", [])
        devices.extend(items)

        # If this page was shorter than a full page, we reached the end.
        if len(items) < PAGE_SIZE:
            break
        offset += PAGE_SIZE      # move to the next page

    return devices


# ---------------------------------------------------------------------------
# BLOCK 4: Save the devices into a CSV file
# DictWriter writes a list of dictionaries as rows. extrasaction="ignore"
# means "skip any fields that aren't in my column list".
# ---------------------------------------------------------------------------
def save_csv(devices, filename):
    columns = ["id", "name", "ip_address", "vendor_name", "description"]
    with open(filename, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(devices)
    print(f"Saved {len(devices)} devices to {filename}")


# ---------------------------------------------------------------------------
# BLOCK 5: Look up one endpoint by MAC address
# A 404 reply simply means "ClearPass has never seen this MAC", which is a
# normal answer and not a crash, so we handle it separately.
# ---------------------------------------------------------------------------
def lookup_endpoint(base_url, headers, verify, mac):
    response = requests.get(
        f"{base_url}/api/endpoint/mac-address/{mac}",
        headers=headers,
        verify=verify,
        timeout=TIMEOUT,
    )
    if response.status_code == 404:
        print(f"{mac}: not found in ClearPass")
        return
    response.raise_for_status()
    endpoint = response.json()
    print(f"MAC:         {endpoint.get('mac_address', mac)}")
    print(f"Status:      {endpoint.get('status', '-')}")
    print(f"Description: {endpoint.get('description', '-')}")


# ---------------------------------------------------------------------------
# BLOCK 6: Main - the steps in order
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="ClearPass network device report")
    parser.add_argument("--csv", help="save the device list to this CSV file")
    parser.add_argument("--mac", help="look up one endpoint by MAC address")
    args = parser.parse_args()

    host = os.getenv("CLEARPASS_HOST")
    client_id = os.getenv("CLEARPASS_CLIENT_ID")
    client_secret = os.getenv("CLEARPASS_CLIENT_SECRET")
    verify = os.getenv("VERIFY_TLS", "true").lower() != "false"
    if not (host and client_id and client_secret):
        sys.exit("Missing CLEARPASS_HOST, CLEARPASS_CLIENT_ID or CLEARPASS_CLIENT_SECRET")

    # If we skip certificate checking (lab only!), hide the repeating warning.
    if not verify:
        requests.packages.urllib3.disable_warnings()

    base_url = f"https://{host}"

    try:
        token = get_token(base_url, client_id, client_secret, verify)
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

        if args.mac:
            lookup_endpoint(base_url, headers, verify, args.mac)
            return

        devices = get_network_devices(base_url, headers, verify)
    except requests.exceptions.RequestException as error:
        sys.exit(f"API request failed: {error}")

    print(f"ClearPass has {len(devices)} network devices\n")
    for device in devices[:20]:          # show only the first 20 on screen
        print(f"{device.get('name', '-'):<30} {device.get('ip_address', '-')}")
    if len(devices) > 20:
        print(f"... and {len(devices) - 20} more")

    if args.csv:
        save_csv(devices, args.csv)


if __name__ == "__main__":
    main()
