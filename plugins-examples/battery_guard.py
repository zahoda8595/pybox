"""
battery_guard.py — PyBox plugin: automation-aware battery protection.

Runs as a scheduled job (every 60s) that reads the phone's actual
battery level and charging state directly from the kernel
(/sys/class/power_supply/battery/), no special permission needed. If
the battery drops below a threshold AND the phone isn't charging, it
flips config["automation_enabled"] off - which scheduler.py already
checks before running any job, so EVERY scheduled job across the whole
app (including LLM-heavy ones from other plugins) pauses automatically.
Once charging resumes or the battery recovers above the threshold, it
turns automation back on by itself.

Why this is hard to find elsewhere: this isn't a generic "battery
saver" toggle - it's specifically aware of PyBox's own automation
system and gates it as a unit, which only makes sense for a phone
running background LLM inference and scheduled jobs in the first
place. A general-purpose automation platform running on a phone,
built by someone who has spent real time thinking about low-resource
hardware constraints, needing this is exactly the kind of detail most
automation tools (built for servers, not phones) never consider.

SETUP:
  Copy to /sdcard/PyBox/plugins/battery_guard.py, reload plugins. It
  registers its own scheduled job automatically - nothing else to do.
  Adjust LOW_BATTERY_THRESHOLD / RESUME_THRESHOLD below if you want
  different cutoffs.

USE:
  GET /plugins/battery_guard/status  - current battery %, charging
                                        state, and whether automation
                                        is currently paused by this
                                        plugin
"""

import logging

LOW_BATTERY_THRESHOLD = 20   # pause automation at or below this %
RESUME_THRESHOLD = 35        # resume once battery climbs back above this
BATTERY_PATH = "/sys/class/power_supply/battery/capacity"
STATUS_PATH = "/sys/class/power_supply/battery/status"

_config = None
_paused_by_us = False


def _read_battery():
    try:
        with open(BATTERY_PATH) as f:
            capacity = int(f.read().strip())
        with open(STATUS_PATH) as f:
            status = f.read().strip()  # "Charging", "Discharging", "Full", etc.
        return capacity, status
    except Exception as e:
        return None, f"unreadable: {e}"


def _check_battery(params):
    global _paused_by_us
    capacity, status = _read_battery()
    if capacity is None:
        logging.warning("battery_guard: could not read battery state (%s)", status)
        return

    charging = status in ("Charging", "Full")

    if capacity <= LOW_BATTERY_THRESHOLD and not charging:
        if _config.get("automation_enabled", True):
            _config.set("automation_enabled", False)
            _paused_by_us = True
            logging.warning(
                "battery_guard: battery at %d%%, not charging - "
                "automation paused", capacity,
            )
    elif _paused_by_us and (capacity >= RESUME_THRESHOLD or charging):
        _config.set("automation_enabled", True)
        _paused_by_us = False
        logging.info(
            "battery_guard: battery at %d%% (charging=%s) - "
            "automation resumed", capacity, charging,
        )


def status_route():
    capacity, status = _read_battery()
    return {
        "battery_percent": capacity,
        "status": status,
        "automation_enabled": _config.get("automation_enabled", True),
        "paused_by_battery_guard": _paused_by_us,
        "low_threshold": LOW_BATTERY_THRESHOLD,
        "resume_threshold": RESUME_THRESHOLD,
    }


def register(ctx):
    global _config
    _config = ctx["config"]
    ctx["scheduler"].JOB_HANDLERS["battery_guard_check"] = _check_battery

    already_exists = any(
        j["handler"] == "battery_guard_check" for j in ctx["scheduler"].list_jobs()
    )
    if not already_exists:
        ctx["scheduler"].create_job(
            name="Battery Guard", handler="battery_guard_check", interval_seconds=60,
        )

    ctx["plugin_routes"]["battery_guard/status"] = status_route
    logging.info(
        "battery_guard plugin loaded (pause<=%d%%, resume>=%d%%)",
        LOW_BATTERY_THRESHOLD, RESUME_THRESHOLD,
    )
