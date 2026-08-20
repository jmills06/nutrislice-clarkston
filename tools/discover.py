#!/usr/bin/env python3
"""Throwaway discovery script: dump Clarkston's Nutrislice school slugs and
menu-type slugs so they can be hard-coded as constants in collect.py.

Nutrislice menu-type slugs vary by district and usually differ between
elementary and secondary buildings ('lunch' vs 'elementary-lunch' vs 'ms-lunch'),
so they must be confirmed against the live API rather than guessed.

Run:  python3 tools/discover.py
Then copy the confirmed slugs into the CONFIG block of collect.py.
"""

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta

# Both hostnames are tried: some districts serve the API from the bare domain,
# others from the api.* subdomain.
HOSTS = [
    "https://clarkston.api.nutrislice.com",
    "https://clarkston.nutrislice.com",
]

TARGET_SCHOOLS = ["springfield-plains", "sashabaw-middle"]

# Probed only if the schools payload does not expose menu types directly.
CANDIDATE_MENU_TYPES = [
    "lunch",
    "breakfast",
    "elementary-lunch",
    "elementary-breakfast",
    "ms-lunch",
    "ms-breakfast",
    "middle-school-lunch",
    "middle-school-breakfast",
    "secondary-lunch",
    "secondary-breakfast",
    "k-5-lunch",
    "k-5-breakfast",
    "6-8-lunch",
    "6-8-breakfast",
]

# Nutrislice's public API is unauthenticated but rejects some default agents.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# The community guidance for this API asks that access be kept light; discovery
# fires a lot of probes, so space them out.
POLITE_DELAY_SEC = 0.4


def fetch_json(url, timeout=20):
    """Return (status, parsed_json_or_None, error_string_or_None)."""
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(body), None
            except json.JSONDecodeError:
                return resp.status, None, "non-JSON response (%d bytes)" % len(body)
    except urllib.error.HTTPError as exc:
        return exc.code, None, "HTTP %d" % exc.code
    except Exception as exc:  # network/DNS/TLS
        return 0, None, "%s: %s" % (type(exc).__name__, exc)
    finally:
        time.sleep(POLITE_DELAY_SEC)


def monday_of_this_week():
    today = date.today()
    return today - timedelta(days=today.weekday())


def find_schools_payload():
    """Try each host's schools endpoint; return (host, payload) for the first hit."""
    for host in HOSTS:
        url = "%s/menu/api/schools/?format=json" % host
        status, payload, err = fetch_json(url)
        print("  GET %s -> %s" % (url, err or "HTTP %d OK" % status))
        if payload is not None:
            return host, payload
    return None, None


def iter_school_objects(payload):
    """The schools endpoint returns either a bare list or a paginated object."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("results", "schools", "data"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def menu_types_from_school(obj):
    """Pull menu-type slugs out of a school object if the payload carries them."""
    found = []
    for key in ("menu_types", "active_menu_types", "menus", "menu_type_list"):
        val = obj.get(key)
        if isinstance(val, list):
            for entry in val:
                if isinstance(entry, dict):
                    slug = entry.get("slug") or entry.get("menu_type")
                    name = entry.get("name") or entry.get("display_name") or ""
                    if slug:
                        found.append((slug, name))
                elif isinstance(entry, str):
                    found.append((entry, ""))
    return found


def per_school_menu_type_endpoints(host, slug):
    """Endpoints observed across Nutrislice deployments for per-school menu types."""
    return [
        "%s/menu/api/schools/%s/menu-type/?format=json" % (host, slug),
        "%s/menu/api/schools/school/%s/?format=json" % (host, slug),
        "%s/menu/api/schools/%s/?format=json" % (host, slug),
        "%s/menu/api/menu-type/school/%s/?format=json" % (host, slug),
    ]


def probe_week(host, school, menu_type, monday):
    """Hit the real week endpoint and report how much menu data comes back.

    This is the authoritative check: a slug is only confirmed if the week
    endpoint returns days that actually contain menu items.
    """
    url = "%s/menu/api/weeks/school/%s/menu-type/%s/%s/%02d/%02d/" % (
        host, school, menu_type, monday.year, monday.month, monday.day,
    )
    status, payload, err = fetch_json(url)
    if payload is None:
        return url, status, 0, 0, err
    days = payload.get("days") or []
    items = sum(len(d.get("menu_items") or []) for d in days)
    with_food = sum(
        1 for d in days
        for i in (d.get("menu_items") or [])
        if i.get("food")
    )
    return url, status, len(days), with_food or items, None


def main():
    monday = monday_of_this_week()
    print("Nutrislice discovery for district 'clarkston'")
    print("Week probe anchored on Monday %s\n" % monday.isoformat())

    print("[1] schools endpoint")
    host, payload = find_schools_payload()
    if host is None:
        print("\nNo schools endpoint responded. Not falling back to HTML scraping.")
        print("Check network egress to *.nutrislice.com, then re-run.")
        return 1

    schools = iter_school_objects(payload)
    print("\n[2] schools found: %d" % len(schools))
    for obj in schools:
        if not isinstance(obj, dict):
            continue
        slug = obj.get("slug") or obj.get("school_slug") or "?"
        name = obj.get("name") or obj.get("display_name") or "?"
        marker = "  <-- TARGET" if slug in TARGET_SCHOOLS else ""
        print("    %-32s %s%s" % (slug, name, marker))
        for mt_slug, mt_name in menu_types_from_school(obj):
            print("        menu-type: %-28s %s" % (mt_slug, mt_name))

    print("\n[3] per-school menu-type endpoints")
    for school in TARGET_SCHOOLS:
        print("  %s" % school)
        for url in per_school_menu_type_endpoints(host, school):
            status, data, err = fetch_json(url)
            if data is None:
                print("    %s -> %s" % (url, err or "HTTP %d" % status))
                continue
            print("    %s -> HTTP %d" % (url, status))
            blob = json.dumps(data)
            if len(blob) < 4000:
                print("      %s" % blob)
            else:
                # Large payloads: surface just the slug-bearing entries.
                entries = data if isinstance(data, list) else iter_school_objects(data)
                for entry in entries:
                    if isinstance(entry, dict) and entry.get("slug"):
                        print("      slug=%-28s name=%s" % (
                            entry.get("slug"), entry.get("name", "")))

    print("\n[4] week-endpoint probe (authoritative confirmation)")
    print("    school                  menu-type                 status  days  items")
    confirmed = {}
    for school in TARGET_SCHOOLS:
        for menu_type in CANDIDATE_MENU_TYPES:
            url, status, days, items, err = probe_week(host, school, menu_type, monday)
            if status == 200 and days:
                confirmed.setdefault(school, []).append((menu_type, days, items))
            flag = "  <== DATA" if (status == 200 and items) else ""
            print("    %-22s  %-24s  %-6s  %-4d  %-4d%s" % (
                school, menu_type, err or status, days, items, flag))

    print("\n[5] summary")
    if not confirmed:
        print("    No menu-type slug returned data. Widen CANDIDATE_MENU_TYPES")
        print("    using the slugs printed in sections [2]/[3] and re-run.")
        return 1
    for school, hits in confirmed.items():
        print("    %s:" % school)
        for menu_type, days, items in hits:
            print("        %-28s days=%d items=%d" % (menu_type, days, items))
    print("\n    Host that answered: %s" % host)
    print("    Put the confirmed slugs into the CONFIG block of collect.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
