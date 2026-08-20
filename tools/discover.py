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


# --- Section [6] ------------------------------------------------------------
# The current-week probe can return 7 days with zero items simply because school
# is not in session yet. Confirming a slug really carries menu data, and learning
# the payload's field shape (does food_category exist? how are sections marked?),
# requires probing weeks that fall inside a school year.
# Clarkston publishes only a short window ahead, so the useful probes are the
# next few weeks, not arbitrary dates deep in the school year.
PROBE_WEEKS = [
    "2026-08-24",
    "2026-08-31",
    "2026-09-07",
    "2026-09-14",
]


def census(payload, cats, food_keys, section_titles, icon_names):
    """Accumulate field-shape facts across every probed payload."""
    for day in payload.get("days") or []:
        for item in day.get("menu_items") or []:
            if item.get("is_section_title"):
                title = (item.get("text") or "").strip()
                if title:
                    section_titles[title] = section_titles.get(title, 0) + 1
            food = item.get("food")
            if not isinstance(food, dict):
                continue
            food_keys.update(food.keys())
            cat = food.get("food_category")
            key = repr(cat)
            cats[key] = cats.get(key, 0) + 1
            for icon in food.get("icons") or []:
                name = icon.get("name") if isinstance(icon, dict) else str(icon)
                if name:
                    icon_names[name] = icon_names.get(name, 0) + 1


def deep_probe(host):
    cats, food_keys, section_titles, icon_names = {}, set(), {}, {}
    sample = None
    print("\n[6] in-session week probe")
    print("    week        school                  menu-type   status  days  fooditems")
    for iso in PROBE_WEEKS:
        y, m, d = (int(x) for x in iso.split("-"))
        for school in TARGET_SCHOOLS:
            for menu_type in ("breakfast", "lunch"):
                url = "%s/menu/api/weeks/school/%s/menu-type/%s/%d/%02d/%02d/" % (
                    host, school, menu_type, y, m, d)
                status, payload, err = fetch_json(url)
                n_days = n_food = 0
                if payload is not None:
                    days = payload.get("days") or []
                    n_days = len(days)
                    n_food = sum(
                        1 for day in days
                        for item in (day.get("menu_items") or [])
                        if isinstance(item.get("food"), dict)
                    )
                    census(payload, cats, food_keys, section_titles, icon_names)
                    if sample is None and n_food:
                        for day in days:
                            if any(isinstance(i.get("food"), dict)
                                   for i in (day.get("menu_items") or [])):
                                sample = (school, menu_type, day)
                                break
                print("    %-10s  %-22s  %-10s  %-6s  %-4d  %-4d%s" % (
                    iso, school, menu_type, err or status, n_days, n_food,
                    "  <== DATA" if n_food else ""))

    print("\n[7] field-shape census across all probed weeks")
    print("    food_category values: %s" % (
        ", ".join("%s x%d" % (k, v) for k, v in sorted(cats.items())) or "NONE SEEN"))
    print("    food object keys: %s" % ", ".join(sorted(food_keys)) or "NONE")
    print("    section titles: %s" % (
        ", ".join("%s x%d" % (k, v) for k, v in sorted(section_titles.items()))
        or "NONE SEEN"))
    print("    icons: %s" % (
        ", ".join("%s x%d" % (k, v) for k, v in sorted(icon_names.items())) or "NONE"))

    print("\n[8] sample day (first day found containing food items)")
    if sample is None:
        print("    No probed week contained any food items.")
        return
    school, menu_type, day = sample
    print("    %s / %s / %s" % (school, menu_type, day.get("date")))
    trimmed = []
    for item in (day.get("menu_items") or [])[:60]:
        food = item.get("food")
        trimmed.append({
            "position": item.get("position"),
            "is_section_title": item.get("is_section_title"),
            "text": item.get("text"),
            "station_id": item.get("station_id"),
            "food": None if not isinstance(food, dict) else {
                "name": food.get("name"),
                "food_category": food.get("food_category"),
                "description": (food.get("description") or "")[:60],
            },
        })
    print(json.dumps(trimmed, indent=2))


def raw_dump(host):
    """Dump one week payload verbatim.

    Every probed week returned days with no food items, which is either a
    genuinely unpublished menu or a wrong assumption about the response shape.
    Only the raw JSON can tell the two apart.
    """
    print("\n[9] raw week payload")
    url = "%s/menu/api/weeks/school/springfield-plains/menu-type/lunch/2026/03/09/" % host
    status, payload, err = fetch_json(url)
    print("    GET %s -> %s" % (url, err or "HTTP %d" % status))
    if payload is None:
        return
    print("    top-level keys: %s" % sorted(payload.keys()))
    blob = json.dumps(payload, indent=2)
    print("    payload size: %d chars" % len(blob))
    print(blob[:6000])
    if len(blob) > 6000:
        print("    ... truncated ...")

    # The digest endpoint is Nutrislice's other public read path; if the week
    # endpoint is empty but digest is not, the collector should use digest.
    print("\n[10] digest endpoint probe")
    for date_str in ("2026-03-09", "2025-09-15", "2026-09-08"):
        d_url = "%s/menu/api/digest/school/springfield-plains/menu-type/lunch/date/%s/" % (
            host, date_str)
        status, data, err = fetch_json(d_url)
        note = err or "HTTP %d" % status
        size = len(json.dumps(data)) if data is not None else 0
        print("    %s -> %s (%d chars)" % (d_url, note, size))
        if data is not None and size > 2:
            print(json.dumps(data, indent=2)[:2500])


# Every target week came back with menu_items == [] and
# has_unpublished_menus == false, and the raw payload shape matches the spec.
# So the parser is not wrong -- the menus are genuinely empty. The open question
# is whether this district instance carries data for ANY school on ANY date, or
# is dormant. Sweep every school and a spread of dates to settle it.
SWEEP_DATES = [
    "2025-09-08", "2025-10-06", "2025-11-10", "2025-12-08",
    "2026-01-12", "2026-02-09", "2026-04-13", "2026-05-11",
]


def sweep_district(host):
    print("\n[11] district-wide sweep: any school, any date, any items?")
    status, payload, err = fetch_json("%s/menu/api/schools/?format=json" % host)
    if payload is None:
        print("    schools endpoint failed: %s" % err)
        return
    slugs = [
        obj.get("slug") for obj in iter_school_objects(payload)
        if isinstance(obj, dict) and obj.get("slug")
    ]
    hits = []
    for school in slugs:
        best = 0
        for iso in SWEEP_DATES:
            y, m, d = (int(x) for x in iso.split("-"))
            url = "%s/menu/api/weeks/school/%s/menu-type/lunch/%d/%02d/%02d/" % (
                host, school, y, m, d)
            st, data, e = fetch_json(url)
            if data is None:
                continue
            n = sum(
                1 for day in (data.get("days") or [])
                for item in (day.get("menu_items") or [])
            )
            if n:
                hits.append((school, iso, n))
                best = n
                break  # one data-bearing week is enough to prove the school is live
        print("    %-32s %s" % (school, "items on a probed week" if best else "empty on all probed weeks"))
    print("\n    schools with any menu data: %d of %d" % (len(hits), len(slugs)))
    for school, iso, n in hits:
        print("        %-32s week %s  %d items" % (school, iso, n))
    if not hits:
        print("        NONE. This district instance publishes no menu items at")
        print("        any probed school/date, so the board has no data source yet.")


def full_year_scan(host):
    """Walk every Monday of a school year looking for a week with real food items.

    The district sweep found exactly one menu_item in the week of 2025-10-06 at
    every school and zero items carrying a food object, which looks like a single
    district-wide note rather than a menu. Scanning every week distinguishes
    "this district never publishes to the API" from "we happened to probe the
    wrong weeks".
    """
    print("\n[12] raw dump of the one week that had an item")
    url = "%s/menu/api/weeks/school/springfield-plains/menu-type/lunch/2026/08/24/" % host
    status, payload, err = fetch_json(url)
    print("    GET %s -> %s" % (url, err or "HTTP %d" % status))
    if payload is not None:
        for day in payload.get("days") or []:
            items = day.get("menu_items") or []
            if items:
                print("    %s: %s" % (day.get("date"), json.dumps(items, indent=2)[:2500]))

    print("\n[13] every-Monday scan, springfield-plains + sashabaw-middle lunch")
    start = date(2026, 8, 3)
    weeks = [start + timedelta(days=7 * i) for i in range(43)]
    any_food = []
    for school in TARGET_SCHOOLS:
        line = []
        for monday in weeks:
            url = "%s/menu/api/weeks/school/%s/menu-type/lunch/%d/%02d/%02d/" % (
                host, school, monday.year, monday.month, monday.day)
            st, data, e = fetch_json(url)
            if data is None:
                line.append("?")
                continue
            n_items = sum(len(d.get("menu_items") or []) for d in (data.get("days") or []))
            n_food = sum(
                1 for d in (data.get("days") or [])
                for i in (d.get("menu_items") or [])
                if isinstance(i.get("food"), dict)
            )
            if n_food:
                any_food.append((school, monday.isoformat(), n_food))
                line.append("F")
            elif n_items:
                line.append("i")
            else:
                line.append(".")
        print("    %-22s %s" % (school, "".join(line)))
    print("    legend: F=week has food items, i=items but no food, .=empty, ?=fetch failed")
    print("    first Monday scanned: %s   last: %s" % (
        weeks[0].isoformat(), weeks[-1].isoformat()))
    if any_food:
        print("\n    weeks with real food items:")
        for school, iso, n in any_food:
            print("        %-22s %s  %d" % (school, iso, n))
    else:
        print("\n    No week in the scanned range carries a single food item for")
        print("    either target school. The API is reachable and the slugs are")
        print("    correct, but Clarkston has published no menu content to it.")


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

    deep_probe(host)
    raw_dump(host)
    sweep_district(host)
    full_year_scan(host)
    return 0


if __name__ == "__main__":
    sys.exit(main())
