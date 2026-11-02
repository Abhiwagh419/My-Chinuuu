from __future__ import annotations

import os
import platform
import shutil
import time
from datetime import datetime
from pathlib import Path

import psutil


def local_time_report() -> str:
    now = datetime.now().astimezone()
    return (
        f"Local date: {now.strftime('%A, %d %B %Y')}\n"
        f"Local time: {now.strftime('%H:%M:%S %Z')}\n"
        f"UTC offset: {now.strftime('%z')}"
    )


def _disk_root() -> str:
    if os.name == "nt":
        return (os.environ.get("SystemDrive") or "C:") + "\\"
    return "/"


def system_report() -> str:
    """Read-only system telemetry. Metrics are sampled, never modified."""
    root = _disk_root()
    total, used, free = shutil.disk_usage(root)
    gib = 1024 ** 3
    vm = psutil.virtual_memory()
    try:
        cpu_percent = psutil.cpu_percent(interval=0.15)
    except (OSError, RuntimeError):
        cpu_percent = 0.0
    boot_epoch = psutil.boot_time()
    uptime_seconds = max(0, int(time.time() - boot_epoch))
    uptime_hours, remainder = divmod(uptime_seconds, 3600)
    uptime_days, uptime_hours = divmod(uptime_hours, 24)
    try:
        battery = psutil.sensors_battery()
    except (OSError, RuntimeError):
        battery = None
    battery_line = "Battery: not reported by this device"
    if battery is not None:
        state = "charging" if battery.power_plugged else "discharging"
        battery_line = f"Battery: {battery.percent:.0f}% ({state})"

    return "\n".join([
        f"OS: {platform.system()} {platform.release()} ({platform.version()})",
        f"Machine: {platform.machine()}",
        f"Processor: {platform.processor() or 'not reported'}",
        f"Python: {platform.python_version()}",
        f"Logical CPU cores: {os.cpu_count() or 'unknown'}",
        f"CPU utilization: {cpu_percent:.1f}% (short sample)",
        f"RAM: {vm.used / gib:.1f} / {vm.total / gib:.1f} GiB ({vm.percent:.1f}% used)",
        f"Disk ({root}): {used / gib:.1f} / {total / gib:.1f} GiB ({free / gib:.1f} GiB free)",
        battery_line,
        f"Uptime: {uptime_days}d {uptime_hours}h {remainder // 60}m",
        f"Boot time: {datetime.fromtimestamp(boot_epoch).astimezone().isoformat(timespec='minutes')}",
    ])


def process_report(limit: int = 12) -> str:
    """List process names, PIDs and memory only; omit command lines and user data."""
    if type(limit) is not int or not 1 <= limit <= 30:
        raise ValueError("Process report limit must be between 1 and 30.")
    rows: list[tuple[float, int, str]] = []
    for proc in psutil.process_iter(attrs=["pid", "name", "memory_info"]):
        try:
            info = proc.info
            memory = float(info["memory_info"].rss if info.get("memory_info") else 0)
            rows.append((memory, int(info["pid"]), str(info.get("name") or "unknown")))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            continue
    rows.sort(reverse=True)
    lines = ["Top processes by resident memory (read-only):"]
    for memory, pid, name in rows[:limit]:
        lines.append(f"PID {pid:<7} {memory / (1024 ** 2):>8.1f} MiB  {name[:48]}")
    lines.append(f"Processes inspected: {len(rows)}")
    return "\n".join(lines)
