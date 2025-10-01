#!/usr/bin/env python3
"""
WikiWare MongoDB Backup & Restore (menu-driven)

- No command-line flags. Navigate with 1-5 and numeric inputs.
- Uses mongodump/mongorestore under the hood.
- Stores settings in .wikiware_backup_config.json beside this script.
- Backups saved to ./backups/ (beside this script).

Menu
  1) Backup now
  2) Restore (latest)
  3) Restore (choose backup)
  4) List backups
  5) Settings / Exit

Requirements:
  - MongoDB Database Tools installed (mongodump, mongorestore on PATH)
  - Python 3.8+

Environment defaults (optional):
  - MONGODB_URL (e.g. mongodb://localhost:27017 or mongodb+srv://...)
  - MONGODB_DB  (defaults to "wikiware")
"""

import os
import json
import sys
import shutil
import datetime as dt
import subprocess
from pathlib import Path
from typing import Optional, List
import shlex

SCRIPT_DIR = Path(__file__).resolve().parent
BACKUP_DIR = SCRIPT_DIR / "backups"
CONF_PATH  = SCRIPT_DIR / ".wikiware_backup_config.json"

DEFAULTS = {
    "uri": os.getenv("MONGODB_URL", "mongodb://localhost:27017"),
    "db":  os.getenv("MONGODB_DB", "wikiware"),
    "keep": 10,            # keep last N backups (rotation); set 0 to disable
    "drop_on_restore": True
}

# --------------------------- Helpers ---------------------------

def clear():
    try:
        os.system("cls" if os.name == "nt" else "clear")
    except Exception:
        pass

def pause(msg="Press Enter to continue..."):
    try:
        input(msg)
    except KeyboardInterrupt:
        pass

def require_tool(name: str):
    if shutil.which(name) is None:
        print(f"\nERROR: '{name}' not found on PATH.")
        print("Install MongoDB Database Tools and ensure 'mongodump'/'mongorestore' are available.")
        print("Download: https://www.mongodb.com/try/download/database-tools\n")
        pause()
        return False
    return True

def load_conf() -> dict:
    if CONF_PATH.exists():
        try:
            with open(CONF_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in DEFAULTS.items():
                data.setdefault(k, v)
            return data
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: failed to load settings from {CONF_PATH}, using defaults. Error: {e}")
    return DEFAULTS.copy()

def save_conf(conf: dict):
    try:
        with open(CONF_PATH, "w", encoding="utf-8") as f:
            json.dump(conf, f, indent=2)
    except OSError as e:
        print(f"Warning: failed to save settings: {e}")

def timestamp() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d_%H%M")

def list_archives(db: str) -> List[Path]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(BACKUP_DIR.glob(f"{db}-*.archive.gz"))

def latest_archive(db: str) -> Optional[Path]:
    files = list_archives(db)
    return files[-1] if files else None

def rotate_backups(db: str, keep: int):
    if keep is None or keep <= 0:
        return
    files = list_archives(db)
    excess = len(files) - keep
    if excess > 0:
        print(f"Rotation: keeping last {keep}, deleting {excess} older backup(s).")
        for p in files[:excess]:
            try:
                p.unlink()
                print(f"  deleted {p.name}")
            except Exception as e:
                print(f"  failed to delete {p.name}: {e}")

def run(cmd: list) -> bool:
    try:
        print(f"\nRunning:\n  {shlex.join(cmd)}")
        subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"\nCommand failed with exit code {e.returncode}")
    except FileNotFoundError:
        print("\nCommand not found (is it on PATH?)")
    return False

def prompt_int(prompt_text: str, min_val: Optional[int] = None, max_val: Optional[int] = None) -> int:
    while True:
        val = input(prompt_text).strip()
        if not val.isdigit():
            print("Please enter a number.")
            continue
        num = int(val)
        if min_val is not None and num < min_val:
            print(f"Please enter a number >= {min_val}.")
            continue
        if max_val is not None and num > max_val:
            print(f"Please enter a number <= {max_val}.")
            continue
        return num

def prompt_yes_no(prompt_text: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        ans = input(f"{prompt_text} {suffix} ").strip().lower()
        if ans == "" and default is not None:
            return default
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        print("Please answer y or n.")

# --------------------------- Actions ---------------------------

def action_backup(conf: dict):
    clear()
    print("== Backup now ==")
    if not require_tool("mongodump"):
        return
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    archive = BACKUP_DIR / f"{conf['db']}-{timestamp()}.archive.gz"
    cmd = [
        "mongodump",
        f"--uri={conf['uri']}",
        f"--db={conf['db']}",
        f"--archive={str(archive)}",
        "--gzip",
    ]
    ok = run(cmd)
    if ok:
        print(f"\n✅ Backup created: {archive}")
        rotate_backups(conf["db"], conf.get("keep", 0))
    pause()

def action_restore_latest(conf: dict):
    clear()
    print("== Restore (latest) ==")
    if not require_tool("mongorestore"):
        return
    arc = latest_archive(conf["db"])
    if not arc:
        print(f"No backups found in {BACKUP_DIR} for '{conf['db']}'.")
        pause()
        return

    print(f"Latest backup: {arc.name}")
    drop = conf.get("drop_on_restore", True)
    if not prompt_yes_no(f"Proceed restoring into '{conf['db']}' from '{arc.name}'? (--drop={drop})", default=False):
        return

    cmd = [
        "mongorestore",
        f"--uri={conf['uri']}",
        f"--nsFrom={conf['db']}.*",
        f"--nsTo={conf['db']}.*",
        f"--archive={str(arc)}",
        "--gzip",
    ]
    if drop:
        cmd.append("--drop")

    ok = run(cmd)
    if ok:
        print(f"\n✅ Restore complete into database '{conf['db']}' from {arc.name}")
    pause()

def action_restore_choose(conf: dict):
    clear()
    print("== Restore (choose backup) ==")
    if not require_tool("mongorestore"):
        return
    files = list_archives(conf["db"])
    if not files:
        print(f"No backups found in {BACKUP_DIR} for '{conf['db']}'.")
        pause()
        return

    print("Select a backup to restore:")
    for i, p in enumerate(files, start=1):
        print(f"  {i}) {p.name}")
    idx = prompt_int("Enter number: ", 1, len(files))
    arc = files[idx - 1]

    drop = conf.get("drop_on_restore", True)
    if not prompt_yes_no(f"Proceed restoring into '{conf['db']}' from '{arc.name}'? (--drop={drop})", default=False):
        return

    cmd = [
        "mongorestore",
        f"--uri={conf['uri']}",
        f"--nsFrom={conf['db']}.*",
        f"--nsTo={conf['db']}.*",
        f"--archive={str(arc)}",
        "--gzip",
    ]
    if drop:
        cmd.append("--drop")

    ok = run(cmd)
    if ok:
        print(f"\n✅ Restore complete into database '{conf['db']}' from {arc.name}")
    pause()

def action_list(conf: dict):
    clear()
    print("== List backups ==")
    files = list_archives(conf["db"])
    if not files:
        print(f"No backups found in {BACKUP_DIR} for '{conf['db']}'.")
    else:
        print(f"Backups for '{conf['db']}' (newest last):")
        for p in files:
            print(f"  {p.name}")
    pause()

def action_settings(conf: dict):
    while True:
        clear()
        print("== Settings ==")
        print(f"1) Mongo URI .......... {conf['uri']}")
        print(f"2) Database name ...... {conf['db']}")
        print(f"3) Keep last N backups  {conf['keep']} (0 disables rotation)")
        print(f"4) Drop on restore ..... {conf['drop_on_restore']}")
        print("5) Back to main menu")
        choice = input("Select 1-5: ").strip()
        if choice == "1":
            new = input("Enter MongoDB URI: ").strip()
            if new:
                conf["uri"] = new
                save_conf(conf)
        elif choice == "2":
            new = input("Enter database name: ").strip()
            if new:
                conf["db"] = new
                save_conf(conf)
        elif choice == "3":
            n = prompt_int("Keep how many recent backups (0 = no rotation): ", 0, 1000000)
            conf["keep"] = n
            save_conf(conf)
        elif choice == "4":
            conf["drop_on_restore"] = prompt_yes_no("Drop collections before restore?", default=True)
            save_conf(conf)
        elif choice == "5":
            return
        else:
            print("Please choose 1-5.")
            pause("")

# --------------------------- Main Loop ---------------------------

def main():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    conf = load_conf()

    while True:
        clear()
        print("=== WikiWare MongoDB Backup & Restore ===")
        print(f"Target DB: {conf['db']}")
        print(f"Mongo URI: {conf['uri']}")
        print("-----------------------------------------")
        print("1) Backup now")
        print("2) Restore (latest)")
        print("3) Restore (choose backup)")
        print("4) List backups")
        print("5) Settings / Exit")
        choice = input("Select 1-5: ").strip()

        if choice == "1":
            action_backup(conf)
        elif choice == "2":
            action_restore_latest(conf)
        elif choice == "3":
            action_restore_choose(conf)
        elif choice == "4":
            action_list(conf)
        elif choice == "5":
            action_settings(conf)
        elif choice == "6":
            clear()
            print("Bye.")
            return
        else:
            print("Please choose 1-6.")
            pause("")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nAborted by user.")
