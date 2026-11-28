#!/usr/bin/env python3
# ^ The "shebang" line. On Linux/macOS it tells the system to run this file
#   with Python. On Windows it is ignored, so you can leave it alone.

# A string at the very top of a file is a "docstring": built-in documentation.
# Python ignores it when running, but tools (and people) can read it.
"""Collect CPU and memory usage for running processes.

Requires: pip install psutil

Examples:
    python proc_stats.py                      # one snapshot, top 20 by CPU
    python proc_stats.py --sort mem --top 10  # top 10 by memory
    python proc_stats.py --interval 5 --count 12 --csv stats.csv   # sample every 5s for 1 min
    python proc_stats.py --name chrome        # only processes whose name contains "chrome"
"""

# ---------------------------------------------------------------------------
# IMPORTS
# "import" loads code other people wrote so we can use it here.
# The first six come with Python ("the standard library"); psutil is a
# third-party package you install with pip.
# ---------------------------------------------------------------------------
import argparse            # reads options typed on the command line, e.g. --top 10
import csv                 # writes spreadsheet-style .csv files
import os                  # talks to the operating system (here: checking if a file exists)
import sys                 # access to the Python interpreter (here: exiting cleanly)
import time                # time.sleep() pauses the program
from datetime import datetime  # gives us the current date and time

import psutil              # "process and system utilities": reads CPU, memory, processes

# ---------------------------------------------------------------------------
# CONSTANTS
# A list of the column names we save for every process. Writing it in capital
# letters is a Python convention meaning "this value never changes".
# ---------------------------------------------------------------------------
FIELDS = ["timestamp", "pid", "name", "user", "cpu_percent", "mem_percent", "rss_mb", "vms_mb", "threads"]


# ---------------------------------------------------------------------------
# collect(): take ONE measurement of every process.
# "def" defines a function: a named block of code you can call (run) later.
# The values in parentheses are parameters; "= 1.0" and "= None" are defaults
# used when the caller doesn't supply a value.
# ---------------------------------------------------------------------------
def collect(sample_secs=1.0, name_filter=None):
    """Return a list of dicts with CPU/memory stats for each process.

    CPU % is measured over `sample_secs`: psutil needs two readings to compute it.
    """
    # --- Step 1: find the processes and start their CPU stopwatch ----------
    # CPU % means "how busy was this process over some period of time", so
    # psutil needs a "before" reading and an "after" reading. Here we take the
    # "before" reading for every process and remember the process in a list.
    procs = []  # an empty list; we'll add processes to it with .append()

    # process_iter() hands us each running process one at a time.
    # The list of names asks psutil to fetch those details up front; they
    # end up in the dictionary p.info (e.g. p.info["name"]).
    for p in psutil.process_iter(["pid", "name", "username"]):
        if p.pid == 0:  # skip "System Idle Process" (its CPU% is just idle time)
            continue    # "continue" jumps straight to the next process

        # If the user asked for --name, skip processes whose name doesn't match.
        # .lower() makes the comparison case-insensitive ("Chrome" == "chrome").
        # "or ''" swaps in an empty string if the name is missing (None).
        if name_filter and name_filter.lower() not in (p.info["name"] or "").lower():
            continue

        # try/except: "attempt this, and if one of these errors happens, don't
        # crash, do the except part instead". Processes can exit while we look
        # at them (NoSuchProcess) or be off-limits to us (AccessDenied).
        try:
            p.cpu_percent(None)  # the "before" reading; its return value is meaningless
            procs.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass  # "pass" means "do nothing": just skip this process

    # --- Step 2: wait, so processes have time to use some CPU ---------------
    time.sleep(sample_secs)

    # --- Step 3: take the "after" readings and build our results -----------
    # isoformat gives a sortable time string like "2026-11-28T00:59:09".
    ts = datetime.now().isoformat(timespec="seconds")

    # psutil reports CPU per core (one fully busy core = 100%, so 8 cores can
    # add up to 800%). Dividing by the core count makes 100% = whole machine.
    ncpu = psutil.cpu_count() or 1  # "or 1" avoids dividing by zero if unknown

    rows = []
    for p in procs:
        try:
            # oneshot() is a speed-up: psutil gathers this process's details in
            # one go instead of asking the operating system separately each time.
            with p.oneshot():
                mem = p.memory_info()  # memory numbers, in bytes

                # A dict (dictionary) stores values under names ("keys").
                # Each process becomes one dict = one row of our table.
                rows.append({
                    "timestamp": ts,
                    "pid": p.pid,                 # process ID: the OS's number for it
                    "name": p.info["name"],
                    "user": p.info["username"] or "",
                    # the "after" reading; round(x, 2) keeps 2 decimal places
                    "cpu_percent": round(p.cpu_percent(None) / ncpu, 2),
                    # share of total RAM this process uses
                    "mem_percent": round(p.memory_percent(), 2),
                    # RSS = physical RAM in use. Bytes / 1024**2 = megabytes.
                    # (** means "to the power of": 1024**2 = 1,048,576)
                    "rss_mb": round(mem.rss / 1024 ** 2, 1),
                    # VMS = virtual memory reserved (usually bigger than RSS)
                    "vms_mb": round(mem.vms / 1024 ** 2, 1),
                    "threads": p.num_threads(),   # how many threads it runs
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass  # process ended or is protected; leave it out

    return rows  # hand the finished list back to whoever called collect()


# ---------------------------------------------------------------------------
# print_table(): show the first `top` rows as neat, lined-up columns.
# ---------------------------------------------------------------------------
def print_table(rows, top):
    # f"..." is an "f-string": anything inside {} is replaced by its value.
    # The bit after ":" controls formatting:
    #   >7   right-align in 7 characters      <30  left-align in 30 characters
    #   .1f  number with 1 decimal place
    print(f"{'PID':>7}  {'NAME':<30} {'CPU%':>6} {'MEM%':>6} {'RSS MB':>9} {'THR':>5}")

    # rows[:top] is a "slice": the first `top` items of the list.
    for r in rows[:top]:
        # r['name'][:30] cuts long names down to 30 characters.
        # Two f-strings side by side are joined into one string automatically.
        print(f"{r['pid']:>7}  {r['name'][:30]:<30} {r['cpu_percent']:>6.1f} "
              f"{r['mem_percent']:>6.2f} {r['rss_mb']:>9.1f} {r['threads']:>5}")


# ---------------------------------------------------------------------------
# main(): the "control centre" — reads options, loops, prints, saves.
# ---------------------------------------------------------------------------
def main():
    # --- Read the command-line options --------------------------------------
    # Each add_argument() defines one option. argparse also builds a --help
    # message from the help= text for free: try  python proc_stats.py --help
    ap = argparse.ArgumentParser(description="Collect CPU and memory usage per process.")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds per sample (default 1)")
    ap.add_argument("--count", type=int, default=1, help="number of samples, 0 = run forever (default 1)")
    ap.add_argument("--top", type=int, default=20, help="rows to print per sample (default 20)")
    ap.add_argument("--sort", choices=["cpu", "mem"], default="cpu", help="sort key (default cpu)")
    ap.add_argument("--name", help="only include processes whose name contains this text")
    ap.add_argument("--csv", help="append all rows to this CSV file")
    args = ap.parse_args()  # now args.top, args.csv, etc. hold what the user typed

    # Pick which column to sort by. This is a one-line if/else:
    #   value_if_true if condition else value_if_false
    key = "cpu_percent" if args.sort == "cpu" else "rss_mb"

    # --- Open the CSV file, if one was requested -----------------------------
    # None is Python's "nothing yet" value. If --csv wasn't given these stay None.
    writer = None
    fh = None  # "fh" = file handle, the open file
    if args.csv:
        # Only write the header row if the file is new or empty, so that
        # running the script again keeps adding rows under the same header.
        new_file = not os.path.exists(args.csv) or os.path.getsize(args.csv) == 0
        # "a" = append mode: add to the end instead of wiping the file.
        # newline="" is what the csv module needs to avoid blank lines on Windows.
        fh = open(args.csv, "a", newline="", encoding="utf-8")
        # DictWriter turns each of our row dicts into one line of the CSV.
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        if new_file:
            writer.writeheader()

    # --- The main loop -------------------------------------------------------
    # try/except KeyboardInterrupt: pressing Ctrl+C stops the loop quietly
    # instead of printing a scary error. "finally" ALWAYS runs at the end,
    # error or not, so the CSV file is always closed properly.
    try:
        i = 0  # counts how many samples we've taken
        # Keep going forever if --count 0, otherwise until we reach the count.
        while args.count == 0 or i < args.count:
            # Measure, then sort biggest first.
            # "lambda r: r[key]" is a tiny unnamed function that tells sorted()
            # what to compare: the chosen column of each row.
            rows = sorted(collect(args.interval, args.name), key=lambda r: r[key], reverse=True)

            # Whole-system memory figures for the summary line.
            vm = psutil.virtual_memory()

            # Summary line: time, overall CPU, overall RAM, number of processes.
            # "\n" is a newline, giving a blank line between samples.
            # 1024 ** 3 converts bytes to gigabytes.
            print(f"\n[{rows[0]['timestamp'] if rows else datetime.now():}] "
                  f"system CPU {psutil.cpu_percent():.1f}%  "
                  f"RAM {vm.percent:.1f}% ({vm.used / 1024 ** 3:.1f}/{vm.total / 1024 ** 3:.1f} GB)  "
                  f"{len(rows)} processes")
            print_table(rows, args.top)

            # Save every row (not just the top ones) to the CSV.
            if writer:
                writer.writerows(rows)
                fh.flush()  # push the data to disk now, in case the script is stopped

            i += 1  # same as i = i + 1
    except KeyboardInterrupt:
        pass
    finally:
        if fh:
            fh.close()


# ---------------------------------------------------------------------------
# ENTRY POINT
# __name__ is "__main__" only when you run this file directly
# (python proc_stats.py). If another script imports this file to reuse
# collect(), main() does NOT run automatically. A common Python idiom.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    sys.exit(main())


#python proc_stats.py
#python proc_stats.py --sort mem --top 10

#python proc_stats.py --interval 5 --count 12 --csv stats.csv
#Takes a sample every 5 seconds for one minute and adds every row to stats.csv.

#python proc_stats.py --name chrome --count 0
#Watches only processes with "chrome" in the name until you press Ctrl+C.







