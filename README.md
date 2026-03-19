# cisco-netbackup

A production-grade Python tool for automating configuration backups across Cisco IOS devices via SSH. Supports concurrent execution, YAML-based inventory, timestamped output, and diff-based change detection.

> **Note:** This repository contains the project structure, documentation, and inventory template. The full source is not publicly available. Read the companion article on Medium for a detailed walkthrough of the implementation and design decisions.

---

## Features

- **Concurrent SSH sessions** — backs up multiple devices simultaneously using `ThreadPoolExecutor`
- **YAML inventory** — devices, credentials, and commands defined externally; no hardcoded values in code
- **Full config capture** — handles Cisco IOS pagination reliably with `terminal length 0` + `terminal width 0`, and uses timing-based reads for large outputs like `sh run`
- **Change detection** — automatically diffs each backup against the previous run and saves a `.diff` file when changes are detected
- **Structured logging** — timestamped logs written simultaneously to console and a rotating log file
- **CLI interface** — configurable inventory path, output directory, and worker count via `argparse`
- **Cross-platform** — tested on Windows (PowerShell) and Linux; UTF-8 safe logging

---

## Project Structure

```
cisco-netbackup/
├── network_backup.py       # Main script (not publicly distributed)
├── inventory.yaml          # Device inventory template
├── requirements.txt        # Python dependencies
├── .gitignore              # Excludes backups, logs, and secrets
└── README.md
```

### Output structure (generated at runtime)

```
switch_backups/
  <hostname>/
    2026-03-19_09-00-00_shrun.txt
    2026-03-19_09-00-00_shipintbrief.txt
    2026-03-19_09-00-00_shrun.diff      ← generated only when changes detected
logs/
  backup_20260319_090000.log
```

---

## Requirements

- Python 3.10+
- SSH access to target devices (port 22)
- A user account with at minimum privilege 1 and enable access, or privilege 15

Install dependencies:

```bash
pip install -r requirements.txt
```

---

## Configuration

Copy and edit the inventory template:

```bash
cp inventory.yaml my_inventory.yaml
```

```yaml
credentials:
  username: admin
  password: your_password
  enable_secret: your_enable_secret   # leave as "" if not required
  device_type: cisco_ios              # see Netmiko supported platforms

commands:
  - sh run
  - sh ip int brief
  - sh version
  - sh cdp neighbors detail

devices:
  - ip: 10.1.1.1
  - ip: 10.1.1.2
  - ip: 10.1.1.3
```

> **Security:** Never commit a populated `inventory.yaml` with real credentials. The `.gitignore` in this repo excludes it by default. Use environment variables or a secrets manager in production.

---

## Usage

```bash
# Basic run using default inventory.yaml
python network_backup.py

# Custom inventory, output directory, and worker count
python network_backup.py -i my_inventory.yaml -o /backups/network -w 10
```

### CLI Arguments

| Argument | Short | Default | Description |
|---|---|---|---|
| `--inventory` | `-i` | `inventory.yaml` | Path to YAML inventory file |
| `--output` | `-o` | `switch_backups` | Directory to save backup files |
| `--workers` | `-w` | `5` | Max concurrent SSH sessions |

---

## Scheduling

### Linux (cron)

```bash
# Run every night at 2:00 AM
0 2 * * * /usr/bin/python3 /opt/netbackup/network_backup.py \
    -i /opt/netbackup/inventory.yaml \
    -o /mnt/backups/network \
    -w 5
```

### Windows (Task Scheduler)

Create a basic task pointing to:
```
Program:   python.exe
Arguments: C:\netbackup\network_backup.py -i C:\netbackup\inventory.yaml -o C:\backups\network
```

---

## Supported Platforms

The `device_type` field in `inventory.yaml` maps directly to [Netmiko's supported platforms](https://github.com/ktbyers/netmiko/blob/develop/PLATFORMS.md). Common values:

| Platform | device_type |
|---|---|
| Cisco IOS / IOS-XE | `cisco_ios` |
| Cisco NX-OS | `cisco_nxos` |
| Cisco ASA | `cisco_asa` |
| Aruba OS | `aruba_os` |
| Juniper JunOS | `juniper_junos` |

---

## Change Detection

Every time a backup runs, the new output is compared against the most recent previous backup for that device and command. If differences are found, a unified diff is saved alongside the backup:

```diff
--- previous
+++ current
@@ -45,7 +45,7 @@
 interface GigabitEthernet1/0/10
- description USER_PORT
+ description PRINTER_3FL
  switchport access vlan 20
```

This gives you a lightweight, file-based audit trail of configuration changes without requiring a separate NMS or SIEM.

---

## Read More

Full implementation walkthrough, design decisions, and key library explanations:

**[Automating Network Config Backups with Python and Netmiko — Medium](https://medium.com/@romario_bennett)**

---

## Author

**Romario Bennett** — Network & Cloud Engineer, Kingston, Jamaica

[![Medium](https://img.shields.io/badge/Medium-@romario__bennett-black?logo=medium)](https://medium.com/@romario_bennett)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-Connect-blue?logo=linkedin)](https://linkedin.com/in/romario-bennett)
