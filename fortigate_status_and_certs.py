"""
fortigate_status_and_certs.py

WHAT IT DOES
    Talks to a FortiGate firewall through its REST API and can:
      1. Show system status (hostname, model, firmware version, serial).
      2. List certificates that expire soon (default: within 30 days).
      3. Create a firewall address object (a safe WRITE example; it only
         previews unless you add --apply).

WHY IT'S USEFUL IN PRODUCTION
    - Expiring certificates cause outages. Checking them automatically
      (and warning early) is a classic automation win.
    - Creating address objects from a script is how you stop clicking
      through the GUI when you have dozens to add.

SETUP
    1. On the FortiGate: System > Administrators > Create New > REST API Admin.
       Give it the minimum access it needs (read-only for status/certs) and
       limit "Trusted Hosts" to the machine running this script.
       Copy the API token (it is shown only once).
    2. Set environment variables:
         export FGT_HOST="192.0.2.1"          # or the firewall's hostname
         export FGT_TOKEN="your-api-token"
         export VERIFY_TLS="true"             # "false" only for lab devices with self-signed certs

USAGE
    python fortigate_status_and_certs.py
    python fortigate_status_and_certs.py --expiring-days 60
    python fortigate_status_and_certs.py --raw
    python fortigate_status_and_certs.py --create-address TEST-NET "10.99.99.0 255.255.255.0"
    python fortigate_status_and_certs.py --create-address TEST-NET "10.99.99.0 255.255.255.0" --apply
"""

# ---------------------------------------------------------------------------
# BLOCK 1: Imports
#   datetime -> lets us work with dates (to count days until expiry)
# ---------------------------------------------------------------------------
import argparse
import json
import os
import sys
from datetime import datetime, timezone

import requests

TIMEOUT = 30


# ---------------------------------------------------------------------------
# BLOCK 2: Function to get system status
# FortiGate API addresses follow a pattern:
#   /api/v2/monitor/...  = read live information (status, sessions, certs)
#   /api/v2/cmdb/...     = read or change configuration (address objects, policies)
# ---------------------------------------------------------------------------
def get_status(base_url, headers, verify):
    response = requests.get(
        f"{base_url}/api/v2/monitor/system/status",
        headers=headers,
        verify=verify,
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


# ---------------------------------------------------------------------------
# BLOCK 3: Function to list ALL certificates on the firewall
# ---------------------------------------------------------------------------
def get_certificates(base_url, headers, verify):
    response = requests.get(
        f"{base_url}/api/v2/monitor/system/available-certificates",
        headers=headers,
        verify=verify,
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    # FortiGate wraps the useful data in a "results" key.
    return response.json().get("results", [])


# ---------------------------------------------------------------------------
# BLOCK 4: Work out how many days until a certificate expires
# The firewall gives us expiry as a "unix timestamp" (seconds since 1970).
# We try a few likely field names, because the exact name can differ between
# FortiOS versions. Run with --raw once to see the real names on your device.
# ---------------------------------------------------------------------------
def days_until_expiry(cert):
    for key in ("valid_to", "valid_to_timestamp", "expires", "expire"):
        value = cert.get(key)
        if isinstance(value, (int, float)) and value > 0:
            expiry = datetime.fromtimestamp(value, tz=timezone.utc)
            return (expiry - datetime.now(timezone.utc)).days
    return None      # None means "I couldn't find an expiry date"


# ---------------------------------------------------------------------------
# BLOCK 5: Create a firewall address object (a WRITE example)
# POST = "create something new". We send the new object as JSON.
# Safety: dry-run by default. It only prints what it WOULD do unless
# apply=True. This is a very good habit for any script that changes things.
# ---------------------------------------------------------------------------
def create_address(base_url, headers, verify, name, subnet, apply):
    body = {"name": name, "subnet": subnet, "comment": "Created by API script"}

    if not apply:
        print("DRY RUN (nothing sent). Would create:")
        print(json.dumps(body, indent=2))
        print("Add --apply to really create it.")
        return

    response = requests.post(
        f"{base_url}/api/v2/cmdb/firewall/address",
        headers=headers,
        json=body,
        verify=verify,
        timeout=TIMEOUT,
    )
    # 424 usually means "this name already exists", so show the reply
    # instead of crashing.
    if response.status_code >= 400:
        sys.exit(f"Create failed ({response.status_code}): {response.text}")
    print(f"Created address object {name} ({subnet})")


# ---------------------------------------------------------------------------
# BLOCK 6: Main - the steps in order
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="FortiGate REST API examples")
    parser.add_argument("--expiring-days", type=int, default=30,
                        help="warn about certificates expiring within this many days")
    parser.add_argument("--raw", action="store_true",
                        help="print raw JSON (to discover field names)")
    parser.add_argument("--create-address", nargs=2, metavar=("NAME", "SUBNET"),
                        help='create an address object, e.g. TEST-NET "10.99.99.0 255.255.255.0"')
    parser.add_argument("--apply", action="store_true",
                        help="actually make the change (otherwise it is a dry run)")
    args = parser.parse_args()

    host = os.getenv("FGT_HOST")
    token = os.getenv("FGT_TOKEN")
    verify = os.getenv("VERIFY_TLS", "true").lower() != "false"
    if not (host and token):
        sys.exit("Missing FGT_HOST or FGT_TOKEN")

    if not verify:
        requests.packages.urllib3.disable_warnings()

    base_url = f"https://{host}"
    # FortiGate accepts the token in the Authorization header as a Bearer token.
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    try:
        # --- 6a: write example, then stop ---
        if args.create_address:
            name, subnet = args.create_address
            create_address(base_url, headers, verify, name, subnet, args.apply)
            return

        # --- 6b: system status ---
        status = get_status(base_url, headers, verify)
        if args.raw:
            print(json.dumps(status, indent=2))
        results = status.get("results", {})
        print(f"Hostname: {results.get('hostname', '-')}")
        print(f"Model:    {results.get('model_name', '-')} {results.get('model_number', '')}")
        print(f"Version:  {status.get('version', '-')} (build {status.get('build', '-')})")
        print(f"Serial:   {status.get('serial', '-')}")

        # --- 6c: certificate expiry check ---
        certs = get_certificates(base_url, headers, verify)
        if args.raw and certs:
            print("\nFirst certificate (raw):")
            print(json.dumps(certs[0], indent=2))

        print(f"\nCertificates expiring within {args.expiring_days} days:")
        warnings = 0
        for cert in certs:
            days = days_until_expiry(cert)
            if days is not None and days <= args.expiring_days:
                print(f"  {cert.get('name', '-')}: {days} days left")
                warnings += 1
        if warnings == 0:
            print("  none found")
    except requests.exceptions.RequestException as error:
        sys.exit(f"API request failed: {error}")


if __name__ == "__main__":
    main()
