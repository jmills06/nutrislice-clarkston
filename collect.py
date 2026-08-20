#!/usr/bin/env python3
"""Fetch Clarkston breakfast/lunch menus from Nutrislice and write menus.json.

Runs in GitHub Actions once per day. Eight requests per run: 2 schools x 2 meals
x 2 weeks. The community guidance for this public API asks that access be kept
light, so there is no polling loop and no retry storm -- a failed run simply
leaves the previous file in place and exits non-zero.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("America/Detroit")
except Exception:  # pragma: no cover - zoneinfo ships with 3.9+
    LOCAL_TZ = None

# --- CONFIG -----------------------------------------------------------------
# Slugs confirmed 2026-08-20 by tools/discover.py against the live API:
# GET https://clarkston.api.nutrislice.com/menu/api/schools/?format=json returns
# 12 schools, each with an inline menu_types[] list. Both target buildings
# expose plain 'breakfast' and 'lunch'; every district-flavored variant
# (elementary-lunch, ms-lunch, secondary-*, k-5-*, 6-8-*) returns HTTP 404.
# See docs/discovery-findings.md for the full probe results.
API_HOST = "https://clarkston.api.nutrislice.com"
DISTRICT = "clarkston"

SCHOOLS = {
    "springfield-plains": "Springfield Plains",
    "sashabaw-middle": "Sashabaw Middle",
}
MEALS = {
    "breakfast": "breakfast",
    "lunch": "lunch",
}

OUT_PATH = os.path.join("data", "latest", "menus.json")

# Nutrislice's public API is unauthenticated but rejects some default agents.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# Space the eight requests out rather than firing them back to back.
POLITE_DELAY_SEC = 0.5

# Items that appear on every tray and carry no information on a menu board.
# Standalone dipping sauces are in here because Clarkston lists them as their
# own menu items ("BBQ Sauce", "Honey Buffalo Sauce") alongside the entree they
# accompany; anything the API actually categorizes as an entree is classified as
# one before this pattern is consulted, so a real dish is never swallowed by it.
NOISE_RE = re.compile(
    r"milk|juice|ketchup|mustard|mayonnaise|ranch|dressing|condiment"
    r"|craisin|graham cracker|water|\bsauce\b",
    re.IGNORECASE,
)

# Stations whose contents are never a hero entree, whatever food_category says.
# Nutrislice tags salad-bar components as food_category 'entree' because they
# count toward the USDA meal pattern, so Springfield Plains' Salad station
# yields "Croutons" and "Dinner Roll" as entrees unless the station is consulted.
SIDE_STATION_RE = re.compile(
    r"salad|fruit|vegetable|veggie|side|dessert|beverage|milk|condiment|extra",
    re.IGNORECASE,
)

# Only used if food_category turns out to be missing across a whole payload.
FALLBACK_ENTREES_PER_SECTION = 2


def log(msg):
    print(msg, flush=True)


def fetch_week(school, menu_type, monday):
    """Return the parsed week payload, or None if the request failed."""
    url = "%s/menu/api/weeks/school/%s/menu-type/%s/%d/%02d/%02d/" % (
        API_HOST, school, menu_type, monday.year, monday.month, monday.day,
    )
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        log("  FETCH FAILED %s -> HTTP %d" % (url, exc.code))
    except Exception as exc:
        log("  FETCH FAILED %s -> %s: %s" % (url, type(exc).__name__, exc))
    finally:
        time.sleep(POLITE_DELAY_SEC)
    return None


def normalize(name):
    """Collapse case and whitespace so near-duplicate names dedupe together."""
    return re.sub(r"\s+", " ", (name or "").strip()).lower()


def classify(food, name, station):
    """Bucket one food into entree / side / staple.

    food_category is authoritative when present -- Clarkston populates it with
    'entree' -- so it is checked before the noise pattern, which means a dish
    whose name happens to contain a noise word is still treated as an entree.
    The station overrides it in one direction only: a food sitting under a salad
    or fruit station is demoted to a side no matter how it is categorized, which
    keeps croutons off the board as a headline entree.
    """
    if (food.get("food_category") or "").strip().lower() == "entree":
        if not (station and SIDE_STATION_RE.search(station)):
            return "entree"
    if NOISE_RE.search(name):
        return "staple"
    return "side"


def parse_day(day, use_fallback, stations_seen=None):
    """Turn one day's menu_items[] into {entrees, sides, staples}.

    Items are walked in `position` order so the board shows dishes the way the
    kitchen listed them. Section-title rows carry no food and exist only to
    group what follows, which the fallback path uses when food_category is
    missing.
    """
    items = sorted(
        day.get("menu_items") or [],
        key=lambda i: i.get("position") if isinstance(i.get("position"), int) else 0,
    )

    buckets = {"entree": [], "side": [], "staple": []}
    seen = set()
    section_count = 0
    station = ""

    for item in items:
        if item.get("is_section_title"):
            station = (item.get("text") or "").strip()
            section_count = 0
            if stations_seen is not None and station:
                stations_seen[station] = stations_seen.get(station, 0) + 1
            continue

        food = item.get("food")
        # A row with no food is a section header, a spacer, or a holiday marker.
        # Holiday days end up with no food at all and are dropped by the caller;
        # the board renders a missing day as NO SCHOOL, which is the same thing.
        if not isinstance(food, dict):
            continue

        name = (food.get("name") or "").strip()
        if not name:
            continue

        key = normalize(name)
        if key in seen:
            continue
        seen.add(key)

        if use_fallback:
            bucket = "staple" if NOISE_RE.search(name) else (
                "entree" if section_count < FALLBACK_ENTREES_PER_SECTION else "side"
            )
            if bucket != "staple":
                section_count += 1
        else:
            bucket = classify(food, name, station)

        buckets[bucket].append(name)

    return {
        "entrees": buckets["entree"],
        "sides": buckets["side"],
        "staples": buckets["staple"],
    }


def payload_has_categories(payloads):
    """True if any food anywhere carries a food_category."""
    for payload in payloads:
        for day in payload.get("days") or []:
            for item in day.get("menu_items") or []:
                food = item.get("food")
                if isinstance(food, dict) and (food.get("food_category") or "").strip():
                    return True
    return False


def local_today():
    if LOCAL_TZ is not None:
        return datetime.now(LOCAL_TZ).date()
    return date.today()


def main():
    today = local_today()
    this_monday = today - timedelta(days=today.weekday())
    # Clarkston publishes roughly two weeks ahead, so the current and next week
    # together cover everything available.
    weeks = [this_monday, this_monday + timedelta(days=7)]

    log("collect: local date %s, weeks %s and %s" % (
        today.isoformat(), weeks[0].isoformat(), weeks[1].isoformat()))

    # Fetch everything first. A partial menu file is worse than a stale one, so
    # nothing is written unless all eight requests succeeded.
    fetched = {}
    failures = 0
    for school in SCHOOLS:
        for meal in MEALS:
            for monday in weeks:
                payload = fetch_week(school, MEALS[meal], monday)
                if payload is None:
                    failures += 1
                else:
                    fetched[(school, meal, monday)] = payload

    if failures:
        log("ABORT: %d of 8 fetches failed; leaving existing %s untouched"
            % (failures, OUT_PATH))
        return 1

    use_fallback = not payload_has_categories(fetched.values())
    if use_fallback:
        log("WARNING: no food_category anywhere in the payload; falling back to "
            "position-within-section classification (first %d per section are "
            "entrees)" % FALLBACK_ENTREES_PER_SECTION)

    schools_out = {}
    total_days = 0
    stations = {}

    for school, label in SCHOOLS.items():
        days_out = {}
        for meal in MEALS:
            meal_days = 0
            meal_items = 0
            dates_seen = []
            for monday in weeks:
                payload = fetched[(school, meal, monday)]
                for day in payload.get("days") or []:
                    iso = day.get("date")
                    if not iso:
                        continue
                    parsed = parse_day(day, use_fallback, stations)
                    if not any(parsed.values()):
                        continue  # unpublished or no-school day: omit entirely
                    days_out.setdefault(iso, {})[meal] = parsed
                    meal_days += 1
                    meal_items += sum(len(v) for v in parsed.values())
                    dates_seen.append(iso)
            span = "%s..%s" % (min(dates_seen), max(dates_seen)) if dates_seen else "none"
            log("  %-20s %-10s days=%-3d items=%-4d range=%s"
                % (school, meal, meal_days, meal_items, span))

        total_days += len(days_out)
        schools_out[school] = {
            "label": label,
            "days": dict(sorted(days_out.items())),
        }

    # Surfaced so SIDE_STATION_RE can be re-tuned if the district renames a
    # station or adds one the pattern does not cover.
    log("  stations seen: %s" % ", ".join(
        "%s x%d" % (k, v) for k, v in sorted(stations.items())) or "none")

    if total_days == 0:
        log("ABORT: parsed zero days across both schools; leaving existing %s "
            "untouched" % OUT_PATH)
        return 1

    out = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "district": DISTRICT,
        "schools": schools_out,
    }

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=2, ensure_ascii=False, sort_keys=False)
        handle.write("\n")

    log("wrote %s (%d school-days total)" % (OUT_PATH, total_days))
    return 0


if __name__ == "__main__":
    sys.exit(main())
