#!/usr/bin/env python3
"""
Network Configuration Backup Tool
Author: Romario Bennett
Description:
    Connects to Cisco IOS devices via SSH, captures show commands,
    and archives timestamped backups with structured logging.
    Supports concurrent execution, YAML-based inventory,
    and diff-based change detection.
"""

import os
import sys
import logging
import difflib
import argparse
import concurrent.futures
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml
from netmiko import ConnectHandler, NetmikoTimeoutException, NetmikoAuthenticationException

# ─── Logging Setup ────────────────────────────────────────────────────────────

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ─── Constants ────────────────────────────────────────────────────────────────

DEFAULT_INVENTORY = "inventory.yaml"
DEFAULT_OUTPUT_DIR = Path("switch_backups")
TIMESTAMP = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
MAX_WORKERS = 5  # Max concurrent SSH sessions


# ─── Inventory Loader ─────────────────────────────────────────────────────────

def load_inventory(inventory_path: str) -> dict:
    """
    Load device inventory and credentials from a YAML file.

    Expected format:
        credentials:
          username: admin
          password: secret
          enable_secret: enable_pw   # optional
          device_type: cisco_ios

        commands:
          - sh run
          - sh ip int brief

        devices:
          - ip: 10.1.1.1
          - ip: 10.1.1.2
    """
    path = Path(inventory_path)
    if not path.exists():
        log.error(f"Inventory file not found: {inventory_path}")
        sys.exit(1)

    with open(path) as f:
        inventory = yaml.safe_load(f)

    required_keys = {"credentials", "commands", "devices"}
    missing = required_keys - inventory.keys()
    if missing:
        log.error(f"Inventory file missing required keys: {missing}")
        sys.exit(1)

    return inventory


# ─── Helpers ──────────────────────────────────────────────────────────────────

def sanitize(name: str) -> str:
    """Strip characters unsafe for filenames."""
    return "".join(c for c in name if c.isalnum() or c in "-_")


def detect_changes(new_output: str, existing_path: Path) -> Optional[str]:
    """
    Compare new output against the most recent backup.
    Returns a unified diff string if changes are found, else None.
    """
    if not existing_path.exists():
        return None

    with open(existing_path) as f:
        old_lines = f.readlines()

    new_lines = new_output.splitlines(keepends=True)
    diff = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile="previous",
        tofile="current",
        lineterm=""
    ))
    return "".join(diff) if diff else None


def get_latest_backup(device_dir: Path, command_filename: str) -> Optional[Path]:
    """Find the most recent backup file for a given command."""
    matches = sorted(device_dir.glob(f"*_{command_filename}"), reverse=True)
    return matches[0] if matches else None


# ─── Long Output Helper ───────────────────────────────────────────────────────

# Commands known to produce very large output that may exceed normal read timeouts
LONG_OUTPUT_COMMANDS = {"sh run", "show run", "show running-config", "sh running-config"}

def send_long_command(conn, command: str) -> str:
    """
    Use send_command_timing for commands with very large output (e.g. sh run).
    This reads until the device goes quiet rather than waiting for a prompt match,
    which is more reliable for configs that span hundreds or thousands of lines.
    """
    return conn.send_command_timing(
        command,
        delay_factor=4,       # Wait 4x longer between reads
        read_timeout=300,     # Allow up to 5 minutes for very large configs
        strip_prompt=True,
        strip_command=True,
        max_loops=10000,
    )


# ─── Core Logic ───────────────────────────────────────────────────────────────

def run_commands_on_device(ip: str, credentials: dict, commands: list[str], output_base: Path):
    """
    Connect to a single device, execute all commands, save output,
    and optionally log configuration changes.
    """
    device = {
        "device_type": credentials.get("device_type", "cisco_ios"),
        "ip": ip,
        "username": credentials["username"],
        "password": credentials["password"],
        "secret": credentials.get("enable_secret", ""),
        "timeout": 60,
        "session_timeout": 60,
        "conn_timeout": 30,
        "banner_timeout": 30,
        "fast_cli": False,
    }

    log.info(f"[{ip}] Connecting...")

    try:
        with ConnectHandler(**device) as conn:

            # Enter enable mode if secret is provided
            if device["secret"]:
                conn.enable()
            else:
                # Attempt enable without secret (e.g., privilege 15 user)
                try:
                    conn.send_command("enable", expect_string=r"[#>]", read_timeout=5)
                except Exception:
                    log.warning(f"[{ip}] Could not enter enable mode — continuing in user exec.")

            # Disable pagination - send both commands to ensure full output capture
            # terminal width 0 prevents line-wrapping that can break regex matching
            conn.send_command("terminal length 0", read_timeout=10, expect_string=r"#")
            conn.send_command("terminal width 0", read_timeout=10, expect_string=r"#")

            hostname = sanitize(conn.find_prompt().strip("#>"))
            device_dir = output_base / hostname
            device_dir.mkdir(parents=True, exist_ok=True)

            results = {"success": [], "failed": []}

            for command in commands:
                log.info(f"[{hostname}] Running: {command}")
                try:
                    # Use timing-based read for commands with very large output
                    if command.strip().lower() in LONG_OUTPUT_COMMANDS:
                        log.info(f"[{hostname}] Using extended read for: {command}")
                        output = send_long_command(conn, command)
                    else:
                        output = conn.send_command(
                            command,
                            read_timeout=120,
                            expect_string=r"#",
                            strip_prompt=True,
                            strip_command=True,
                            max_loops=10000,
                        )

                    cmd_slug = sanitize(command)
                    filename = f"{TIMESTAMP}_{cmd_slug}.txt"
                    filepath = device_dir / filename

                    # Save current backup
                    filepath.write_text(output)
                    log.info(f"[{hostname}] Saved -> {filepath}")

                    # Diff against previous backup
                    latest = get_latest_backup(device_dir, f"{cmd_slug}.txt")
                    if latest and latest != filepath:
                        diff = detect_changes(output, latest)
                        if diff:
                            diff_path = device_dir / f"{TIMESTAMP}_{cmd_slug}.diff"
                            diff_path.write_text(diff)
                            log.warning(f"[{hostname}] WARNING: Changes detected -> {diff_path}")
                        else:
                            log.info(f"[{hostname}] No changes since last backup.")

                    results["success"].append(command)

                except (NetmikoTimeoutException, Exception) as e:
                    log.error(f"[{hostname}] Command failed '{command}': {e}")
                    results["failed"].append(command)

            log.info(
                f"[{hostname}] Done. OK {len(results['success'])} succeeded, "
                f"FAIL {len(results['failed'])} failed."
            )
            return hostname, results

    except NetmikoAuthenticationException:
        log.error(f"[{ip}] Authentication failed. Check credentials.")
    except NetmikoTimeoutException:
        log.error(f"[{ip}] Connection timed out.")
    except Exception as e:
        log.error(f"[{ip}] Unexpected error: {e}")

    return ip, {"success": [], "failed": commands}


# ─── Summary Report ───────────────────────────────────────────────────────────

def print_summary(results: list[tuple]):
    """Print a formatted summary table after all jobs complete."""
    print("\n" + "=" * 60)
    print(f"{'BACKUP SUMMARY':^60}")
    print("=" * 60)
    print(f"{'Device':<25} {'Success':>10} {'Failed':>10}")
    print("-" * 60)
    for hostname, result in results:
        print(
            f"{str(hostname):<25} "
            f"{len(result.get('success', [])):>10} "
            f"{len(result.get('failed', [])):>10}"
        )
    print("=" * 60)


# ─── Entry Point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Network Configuration Backup Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-i", "--inventory",
        default=DEFAULT_INVENTORY,
        help=f"Path to YAML inventory file (default: {DEFAULT_INVENTORY})",
    )
    parser.add_argument(
        "-o", "--output",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Output base directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "-w", "--workers",
        type=int,
        default=MAX_WORKERS,
        help=f"Max concurrent SSH sessions (default: {MAX_WORKERS})",
    )
    args = parser.parse_args()

    inventory = load_inventory(args.inventory)
    credentials = inventory["credentials"]
    commands = inventory["commands"]
    devices = inventory["devices"]
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    log.info(f"Starting backup for {len(devices)} device(s) with {args.workers} worker(s).")
    log.info(f"Commands: {commands}")

    all_results = []

    # Concurrent execution across devices
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                run_commands_on_device,
                device["ip"],
                credentials,
                commands,
                output_dir,
            ): device["ip"]
            for device in devices
        }

        for future in concurrent.futures.as_completed(futures):
            ip = futures[future]
            try:
                result = future.result()
                all_results.append(result)
            except Exception as e:
                log.error(f"[{ip}] Unhandled exception in worker: {e}")
                all_results.append((ip, {"success": [], "failed": commands}))

    print_summary(all_results)
    log.info("Backup run complete.")


if __name__ == "__main__":
    main()
