#!/usr/bin/env python3
"""Preview/install a configurable per-user Windows Task Scheduler job."""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

from updater import default_home, run, write_json

NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
ET.register_namespace("", NS)
DAYS = dict(mon="Monday", tue="Tuesday", wed="Wednesday", thu="Thursday",
            fri="Friday", sat="Saturday", sun="Sunday")


def make_xml(*, frequency, at, weekdays, day, user, executable, script, home,
             start=None, plugins=True):
    hour, minute = (int(x) for x in at.split(":"))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("Time must be HH:MM, from 00:00 to 23:59")
    if frequency not in ("daily", "weekly", "monthly"):
        raise ValueError("Choose daily, weekly or monthly")
    if frequency == "weekly" and (not weekdays or any(x not in DAYS for x in weekdays)):
        raise ValueError("Weekly schedules require --weekday mon..sun")
    if frequency == "monthly" and (day is None or not 1 <= day <= 28):
        raise ValueError("Monthly schedules require --day 1..28 (valid in every month)")
    task = ET.Element(f"{{{NS}}}Task", version="1.2")

    def child(parent, name, value=None, **attributes):
        node = ET.SubElement(parent, f"{{{NS}}}{name}", attributes)
        if value is not None:
            node.text = str(value)
        return node

    info = child(task, "RegistrationInfo")
    child(info, "Description", "Codex skill/plugin updates with source verification and skill backups")
    triggers = child(task, "Triggers")
    calendar = child(triggers, "CalendarTrigger")
    date = start or dt.date.today()
    child(calendar, "StartBoundary", f"{date.isoformat()}T{hour:02d}:{minute:02d}:00")
    child(calendar, "Enabled", "true")
    if frequency == "daily":
        child(child(calendar, "ScheduleByDay"), "DaysInterval", "1")
    elif frequency == "weekly":
        weekly = child(calendar, "ScheduleByWeek")
        child(weekly, "WeeksInterval", "1")
        days = child(weekly, "DaysOfWeek")
        for weekday in dict.fromkeys(weekdays):
            child(days, DAYS[weekday])
    else:
        monthly = child(calendar, "ScheduleByMonth")
        child(child(monthly, "DaysOfMonth"), "Day", day)
        months = child(monthly, "Months")
        for month in ("January", "February", "March", "April", "May", "June", "July", "August",
                      "September", "October", "November", "December"):
            child(months, month)
    principal = child(child(task, "Principals"), "Principal", id="Author")
    child(principal, "UserId", user)
    child(principal, "LogonType", "InteractiveToken")
    child(principal, "RunLevel", "LeastPrivilege")
    settings = child(task, "Settings")
    child(settings, "MultipleInstancesPolicy", "IgnoreNew")
    child(settings, "DisallowStartIfOnBatteries", "false")
    child(settings, "StopIfGoingOnBatteries", "false")
    child(settings, "StartWhenAvailable", "true")
    child(settings, "RunOnlyIfNetworkAvailable", "true")
    child(settings, "Enabled", "true")
    child(settings, "Hidden", "true")
    # Do not kill the updater halfway through an atomic directory replacement.
    child(settings, "ExecutionTimeLimit", "PT0S")
    action = child(child(task, "Actions", Context="Author"), "Exec")
    child(action, "Command", executable)
    arguments = ["-X", "utf8", str(script), "--home", str(home), "update"]
    if plugins:
        arguments.append("--plugins")
    child(action, "Arguments", subprocess.list2cmdline(arguments))
    child(action, "WorkingDirectory", str(home))
    return ET.tostring(task, encoding="utf-16", xml_declaration=True).decode("utf-16")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--home", type=Path, default=default_home())
    p.add_argument("--task-name", default="Codex-Skill-Updater")
    sub = p.add_subparsers(dest="command", required=True)
    config = sub.add_parser("configure")
    config.add_argument("--frequency", required=True, choices=["daily", "weekly", "monthly"])
    config.add_argument("--at", required=True, help="HH:MM in the Windows user's local time zone")
    config.add_argument("--weekday", action="append", choices=list(DAYS))
    config.add_argument("--day", type=int)
    config.add_argument("--skills-only", action="store_true")
    config.add_argument("--install", action="store_true", help="Without this flag, print XML only")
    sub.add_parser("status")
    sub.add_parser("remove")
    args = p.parse_args(argv)
    home = args.home.expanduser().absolute()
    try:
        if args.command == "configure":
            script = Path(__file__).with_name("updater.py").resolve()
            executable = Path(sys.executable)
            pythonw = executable.with_name("pythonw.exe")
            if pythonw.exists():
                executable = pythonw
            user = ((os.environ.get("USERDOMAIN", "") + "\\") if os.environ.get("USERDOMAIN") else "") + getpass.getuser()
            xml = make_xml(frequency=args.frequency, at=args.at, weekdays=args.weekday,
                           day=args.day, user=user, executable=str(executable), script=script,
                           home=home, plugins=not args.skills_only)
            if not args.install:
                print(xml)
                return 0
            if os.name != "nt":
                raise ValueError("Automatic installation currently supports Windows; use cron/launchd elsewhere")
            if not (home / "registry.json").is_file():
                raise ValueError("Register sources before scheduling updates")
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "task.xml"
                path.write_text(xml, encoding="utf-16")
                run(["schtasks", "/Create", "/TN", args.task_name, "/XML", str(path), "/F"], timeout=30)
            verified = run(["schtasks", "/Query", "/TN", args.task_name, "/XML"], timeout=30)
            if not verified:
                raise RuntimeError("Task was not returned after installation")
            write_json(home / "schedule.json", {"task": args.task_name, "frequency": args.frequency,
                       "at": args.at, "weekdays": args.weekday, "day": args.day,
                       "timezone": "Windows local time", "plugins": not args.skills_only,
                       "user": user, "executable": str(executable), "script": str(script)})
            print(f"Installed: {args.task_name}; {args.frequency} at {args.at} (Windows local time)")
        elif args.command == "status":
            print(run(["schtasks", "/Query", "/TN", args.task_name, "/V", "/FO", "LIST"], timeout=30).decode(errors="replace"))
        else:
            run(["schtasks", "/Delete", "/TN", args.task_name, "/F"], timeout=30)
            (home / "schedule.json").unlink(missing_ok=True)
            print(f"Removed: {args.task_name}")
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
