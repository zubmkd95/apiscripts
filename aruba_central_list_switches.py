"""
aruba_central_list_switches.py

WHAT IT DOES
    Logs in to Aruba Central (New Central / HPE GreenLake), downloads the list
    of all switches, and prints a simple report. Flags switches that are not online.

WHY IT'S USEFUL IN PRODUCTION
    A quick daily health check: "which switches does Central think are down?"
    You could run this on a schedule (cron) and email the output.

SETUP (do this once)
    1. In HPE GreenLake: Manage Workspace > Personal API clients > create a client.
       You get a Client ID and a Client Secret.
    2. Set these environment variables (never type secrets inside the script!):
         export CENTRAL_API_BASE="https://us5.api.central.arubanetworks.com"
         export CENTRAL_CLIENT_ID="your-client-id"
         export CENTRAL_CLIENT_SECRET="your-client-secret"
       (Your API base URL depends on your Central region/cluster.)

USAGE
    python aruba_central_list_switches.py
    python aruba_central_list_switches.py --raw     # shows the raw JSON of the first switch
"""

# ---------------------------------------------------------------------------
# BLOCK 1: Imports
# "import" loads tools other people wrote so we don't have to write them.
#   argparse -> lets us add options like --raw when we run the script
#   json     -> lets us print JSON nicely
#   os       -> lets us read environment variables (where we keep secrets)
#   sys      -> lets us exit the script with an error code
#   requests -> the most popular Python library for making web/API requests
# ---------------------------------------------------------------------------
import argparse
import json
import os
import sys

import requests

# ---------------------------------------------------------------------------
# BLOCK 2: Constants
# Values that never change while the script runs. By convention, Python
# programmers write constants in CAPITAL LETTERS.
# TIMEOUT means: if the server doesn't answer in 30 seconds, give up
# instead of hanging forever.
# ---------------------------------------------------------------------------
TOKEN_URL = "https://sso.common.cloud.hpe.com/as/token.oauth2"
SWITCH_PATH = "/network-monitoring/v1/switches"
TIMEOUT = 30


# ---------------------------------------------------------------------------
# BLOCK 3: Function to get an access token
# A function is a named, reusable block of code. This one logs in.
# APIs don't want your password on every request. Instead you trade your
# Client ID + Secret for a temporary "access token" (like a visitor badge),
# then show that badge on every later request.
# ---------------------------------------------------------------------------
def get_access_token(client_id, client_secret):
    # requests.post() sends an HTTP POST (used to send data to a server).
    # "client_credentials" is the OAuth2 name for "log in as an application".
    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=TIMEOUT,
    )
    # raise_for_status() stops the script with a clear error if the server
    # replied with a failure code (401 = bad login, 500 = server problem...).
    response.raise_for_status()
    # .json() turns the reply text into a Python dictionary.
    # ["access_token"] pulls out the one value we need from that dictionary.
    return response.json()["access_token"]


# ---------------------------------------------------------------------------
# BLOCK 4: Function to download ALL switches
# APIs usually return results in "pages" (for example 100 at a time) so one
# reply isn't gigantic. We loop until there are no more pages.
# ---------------------------------------------------------------------------
def get_all_switches(api_base, token):
    # The "Authorization: Bearer <token>" header is how we show our badge.
    headers = {"Authorization": f"Bearer {token}"}
    url = api_base + SWITCH_PATH

    switches = []                 # an empty list; we will add each switch to it
    params = {"limit": 100}       # ask for 100 switches per page

    while True:                   # keep looping until we hit "break"
        response = requests.get(url, headers=headers, params=params, timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()

        # .get("items", []) means: take the "items" list, or use an empty
        # list if it's missing. This avoids crashing on a missing key.
        switches.extend(data.get("items", []))

        # If the reply includes a "next" value, there is another page.
        # If not, we have everything, so stop. (Check Aruba's developer
        # docs if your Central version names this field differently.)
        next_page = data.get("next")
        if not next_page:
            break
        params["next"] = next_page

    return switches


# ---------------------------------------------------------------------------
# BLOCK 5: Small helper function
# Different API versions name fields slightly differently (for example
# "deviceName" vs "name"). This tries several names and returns the first
# one that exists, so the report doesn't break.
# ---------------------------------------------------------------------------
def pick(item, *keys, default="-"):
    for key in keys:
        if item.get(key) not in (None, ""):
            return item[key]
    return default


# ---------------------------------------------------------------------------
# BLOCK 6: The main function, where the script's steps happen in order
# ---------------------------------------------------------------------------
def main():
    # --- 6a: read the options typed after the script name ---
    parser = argparse.ArgumentParser(description="List switches from Aruba Central")
    parser.add_argument("--raw", action="store_true",
                        help="print the raw JSON of the first switch (to see field names)")
    args = parser.parse_args()

    # --- 6b: read secrets from environment variables ---
    api_base = os.getenv("CENTRAL_API_BASE")
    client_id = os.getenv("CENTRAL_CLIENT_ID")
    client_secret = os.getenv("CENTRAL_CLIENT_SECRET")
    if not (api_base and client_id and client_secret):
        sys.exit("Missing CENTRAL_API_BASE, CENTRAL_CLIENT_ID or CENTRAL_CLIENT_SECRET")

    # --- 6c: log in and fetch the data. try/except catches network
    #         problems and shows a friendly message instead of a wall of red text.
    try:
        token = get_access_token(client_id, client_secret)
        switches = get_all_switches(api_base, token)
    except requests.exceptions.RequestException as error:
        sys.exit(f"API request failed: {error}")

    # --- 6d: optional debug output ---
    if args.raw and switches:
        print(json.dumps(switches[0], indent=2))
        return

    # --- 6e: print a simple report, one switch per line ---
    print(f"Found {len(switches)} switches\n")
    print(f"{'NAME':<25} {'IP':<16} {'MODEL':<14} {'STATUS':<10}")
    problems = 0
    for sw in switches:
        name = pick(sw, "deviceName", "name", "hostname")
        ip = pick(sw, "ipv4", "ipAddress", "ip_address")
        model = pick(sw, "model", "deviceModel")
        status = str(pick(sw, "status", "deviceStatus"))
        print(f"{name:<25} {ip:<16} {model:<14} {status:<10}")
        if status.upper() not in ("ONLINE", "UP"):
            problems += 1

    print(f"\n{problems} switch(es) not reporting as online")


# ---------------------------------------------------------------------------
# BLOCK 7: Entry point
# This line means: "only run main() if this file is run directly" (not if
# another script imports it). It is standard in almost every Python script.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    main()
