#!/usr/bin/env python3
"""
Hyrox Dashboard — Data Fetcher
Pulls from Garmin Connect + Strava API, writes data.json to repo root.
Runs inside GitHub Actions — see .github/workflows/update-data.yml
"""

import json
import os
import sys
from datetime import date, timedelta

import requests

# ── Install garminconnect if missing ─────────────────────────────────────────
try:
    import garminconnect
except ImportError:
    os.system("pip install garminconnect --quiet")
    import garminconnect

# ── Config from GitHub Secrets ────────────────────────────────────────────────
GARMIN_EMAIL         = os.environ["GARMIN_EMAIL"]
GARMIN_PASSWORD      = os.environ["GARMIN_PASSWORD"]
STRAVA_CLIENT_ID     = os.environ["STRAVA_CLIENT_ID"]
STRAVA_CLIENT_SECRET = os.environ["STRAVA_CLIENT_SECRET"]
STRAVA_REFRESH_TOKEN = os.environ["STRAVA_REFRESH_TOKEN"]

TODAY     = date.today()
TODAY_STR = TODAY.isoformat()

# ─────────────────────────────────────────────────────────────────────────────
# STRAVA
# ─────────────────────────────────────────────────────────────────────────────

def get_strava_token():
    r = requests.post("https://www.strava.com/oauth/token", data={
        "client_id":     STRAVA_CLIENT_ID,
        "client_secret": STRAVA_CLIENT_SECRET,
        "refresh_token": STRAVA_REFRESH_TOKEN,
        "grant_type":    "refresh_token",
    })
    r.raise_for_status()
    return r.json()["access_token"]


def get_strava_activities(token, n=14):
    r = requests.get(
        "https://www.strava.com/api/v3/athlete/activities",
        headers={"Authorization": f"Bearer {token}"},
        params={"per_page": n},
    )
    r.raise_for_status()
    out = []
    for a in r.json():
        spd  = a.get("average_speed") or 0
        pace = round(1000 / spd / 60, 2) if spd else 0
        out.append({
            "name":       a["name"],
            "date":       a["start_date_local"][:10],
            "distanceKm": round(a["distance"] / 1000, 2),
            "movingSec":  a["moving_time"],
            "paceMinKm":  pace,
            "avgHR":      a.get("average_heartrate"),
            "maxHR":      a.get("max_heartrate"),
            "type":       a["type"],
        })
    return out


# ─────────────────────────────────────────────────────────────────────────────
# GARMIN — helpers
# ─────────────────────────────────────────────────────────────────────────────

def parse_hrv(raw):
    """Handles different garminconnect versions returning different key names."""
    if not raw:
        return {}
    # New versions nest under hrvSummary
    s = raw.get("hrvSummary", raw)
    baseline = s.get("baseline") or {}
    return {
        "lastNight":   s.get("lastNight")   or s.get("last_night_avg_hrv_ms")   or 0,
        "weeklyAvg":   s.get("weeklyAvg")   or s.get("weekly_avg_hrv_ms")       or 0,
        "baselineLow": baseline.get("balancedLow")   or s.get("baseline_balanced_low_ms")   or 56,
        "baselineHigh":baseline.get("balancedUpper") or s.get("baseline_balanced_upper_ms") or 82,
        "status":      s.get("status") or s.get("hrvStatus") or "BALANCED",
    }


def parse_readiness(raw):
    if not raw:
        return {}
    item = raw[0] if isinstance(raw, list) else raw
    return {
        "score": item.get("score", 0),
        "level": item.get("level", ""),
    }


def parse_stats(raw):
    if not raw:
        return {}
    return {
        "rhr":     raw.get("restingHeartRate") or raw.get("resting_heart_rate_bpm") or 0,
        "battery": raw.get("bodyBatteryMostRecentValue") or raw.get("body_battery_current") or 0,
        "stress":  raw.get("stressQualifier") or raw.get("stress_qualifier") or "CALM",
    }


def parse_sleep(raw):
    if not raw:
        return 0
    dto = raw.get("dailySleepDTO") or {}
    scores = dto.get("sleepScores") or {}
    overall = scores.get("overall") or {}
    return overall.get("value") or dto.get("sleepScore") or 0


# ─────────────────────────────────────────────────────────────────────────────
# GARMIN — main fetch
# ─────────────────────────────────────────────────────────────────────────────

def get_garmin_data(api):
    # ── Today's HRV ──────────────────────────────────────────────────────────
    try:
        hrv_raw = api.get_hrv_data(TODAY_STR)
        hrv     = parse_hrv(hrv_raw)
    except Exception as e:
        print(f"  [warn] HRV today: {e}")
        hrv = {}

    # ── 7-day HRV sparkline ──────────────────────────────────────────────────
    day_labels = ["M","T","W","T","F","S","S"]
    sparkline  = []
    for i in range(6, -1, -1):
        d      = (TODAY - timedelta(days=i)).isoformat()
        label  = day_labels[(TODAY.weekday() - i) % 7]
        try:
            raw = api.get_hrv_data(d)
            val = parse_hrv(raw).get("lastNight", 0)
        except Exception:
            val = 0
        sparkline.append({"d": label, "v": val})

    # ── Daily stats ──────────────────────────────────────────────────────────
    try:
        stats = parse_stats(api.get_stats(TODAY_STR))
    except Exception as e:
        print(f"  [warn] Stats: {e}")
        stats = {}

    # ── Training readiness ───────────────────────────────────────────────────
    try:
        readiness = parse_readiness(api.get_training_readiness(TODAY_STR))
    except Exception as e:
        print(f"  [warn] Readiness: {e}")
        readiness = {}

    # ── Sleep ────────────────────────────────────────────────────────────────
    try:
        sleep_score = parse_sleep(api.get_sleep_data(TODAY_STR))
    except Exception as e:
        print(f"  [warn] Sleep: {e}")
        sleep_score = 0

    # ── VO2 max (latest) ─────────────────────────────────────────────────────
    try:
        ts  = api.get_training_status(TODAY_STR) or {}
        vo2 = (ts.get("mostRecentVO2Max") or {}).get("generic") or 0
    except Exception as e:
        print(f"  [warn] VO2: {e}")
        vo2 = 0

    return {
        "hrv": {
            "lastNight":    hrv.get("lastNight", 0),
            "weeklyAvg":    hrv.get("weeklyAvg", 0),
            "baselineLow":  hrv.get("baselineLow", 56),
            "baselineHigh": hrv.get("baselineHigh", 82),
            "status":       hrv.get("status", "BALANCED"),
            "sparkline":    sparkline,
        },
        "readiness":  readiness,
        "rhr":        stats.get("rhr", 0),
        "battery":    stats.get("battery", 0),
        "sleep":      sleep_score,
        "stress":     stats.get("stress", "CALM"),
        "vo2":        vo2,
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("→ Fetching Strava...")
    token      = get_strava_token()
    activities = get_strava_activities(token)
    print(f"  {len(activities)} activities fetched")

    print("→ Logging into Garmin...")
    api = garminconnect.Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    api.login()
    print("  Logged in")

    print("→ Fetching Garmin data...")
    recovery = get_garmin_data(api)
    print("  Done")

    data = {
        "lastUpdated": TODAY_STR,
        "recovery":    recovery,
        "activities":  activities,
    }

    # Write to repo root (script lives in scripts/, so go up one level)
    out = os.path.join(os.path.dirname(__file__), "..", "data.json")
    with open(out, "w") as f:
        json.dump(data, f, indent=2)

    print(f"✓ data.json written for {TODAY_STR}")


if __name__ == "__main__":
    main()
