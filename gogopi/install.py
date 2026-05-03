"""gogopi-install — install or remove GoGoPi systemd services."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVICES_DIR = REPO_ROOT / "services"
SYSTEMD_DIR = Path("/etc/systemd/system")

SERVICES = ["hw_button_scan", "hw_wifi", "hw_find"]


def _require_root():
    if os.geteuid() != 0:
        sys.exit("gogopi-install must be run as root (use sudo).")


def _run(*cmd):
    subprocess.run(cmd, check=True)


def _generate_service(name: str) -> str:
    template = (SERVICES_DIR / f"{name}.service.in").read_text()
    return template.replace("${LOC}", str(REPO_ROOT))


def install():
    _require_root()
    for name in SERVICES:
        dest = SYSTEMD_DIR / f"{name}.service"
        dest.write_text(_generate_service(name))
        dest.chmod(0o644)
        _run("systemctl", "enable", f"{name}.service")
        _run("systemctl", "start", f"{name}.service")
        print(f"{name}.service installed and started")


def remove():
    _require_root()
    for name in SERVICES:
        _run("systemctl", "stop", f"{name}.service", "--ignore-not-loaded")
        _run("systemctl", "disable", f"{name}.service", "--ignore-not-found")
        dest = SYSTEMD_DIR / f"{name}.service"
        if dest.exists():
            dest.unlink()
            print(f"{name}.service removed")
    _run("systemctl", "daemon-reload")


def main():
    parser = argparse.ArgumentParser(description="Install or remove GoGoPi systemd services.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("install", help="Generate, enable, and start all services")
    sub.add_parser("remove", help="Stop, disable, and remove all services")

    args = parser.parse_args()
    {"install": install, "remove": remove}[args.command]()


if __name__ == "__main__":
    main()
