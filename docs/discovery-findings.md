# Nutrislice discovery findings

Run: 2026-08-20, via `tools/discover.py` on a GitHub runner
(this project's Claude Code session has `*.nutrislice.com` blocked by its
network egress proxy, so discovery ran in CI instead).

## Confirmed constants

```
HOST        https://clarkston.api.nutrislice.com
SCHOOLS     springfield-plains   "Springfield Plains Elementary"
            sashabaw-middle      "Sashabaw Middle"
MENU TYPES  breakfast, lunch     (both schools, both return HTTP 200)
```

Source: `GET /menu/api/schools/?format=json` returns 12 schools, each with an
inline `menu_types[]` carrying `slug` + `name`. `springfield-plains` advertises
`breakfast` and `lunch`; `sashabaw-middle` advertises only `lunch`, but its
`breakfast` week endpoint also returns 200.

Ruled out (all HTTP 404): `elementary-lunch`, `elementary-breakfast`, `ms-lunch`,
`ms-breakfast`, `middle-school-*`, `secondary-*`, `k-5-*`, `6-8-*`, every
per-school menu-type endpoint, and the `digest/school/.../date/...` endpoint.

## Week endpoint contract (verified against a real response)

`GET /menu/api/weeks/school/{school}/menu-type/{type}/{YYYY}/{MM}/{DD}/`

Root object holds `days[]` and `bold_all_entrees_enabled`. Each day:

```json
{ "date": "2026-03-09", "has_unpublished_menus": false, "menu_items": [] }
```

Menu-item keys observed: `position`, `bold`, `text`, `id`, `date`,
`is_section_title`, `no_line_break`, `blank_line`, `menu_type_id`, `food`,
`menu_id`, `is_holiday`, `food_list`, `station_id`, `is_station_header`,
`category`, `image`, `image_thumbnail`.

## Blocking finding: no menu content is published

A scan of 43 consecutive Mondays (2025-08-25 through 2026-06-15), both schools,
lunch, found **zero** items carrying a `food` object:

```
springfield-plains     ii....iii....i..iii.ii.i.iii.i.i......iii..
sashabaw-middle        ii....iii....i..iii.ii.i.i.i.i.i......iii..
   F = week has food items   i = items but no food   . = empty
```

A district-wide sweep of all 12 schools across 8 dates spanning the same year
found the same: every school's only content is calendar closures, e.g.

```json
{ "position": 0, "bold": true, "text": "No School",
  "is_section_title": false, "food": null, "is_holiday": true }
```

`has_unpublished_menus` is `false` throughout, so there are no hidden drafts.
Weeks in September and October 2026 are empty as well.

The school list matches Clarkston Community Schools' actual buildings, so this
is the correct district instance -- it is being used for the closure calendar
only, not for menus.

## Consequence for the collector spec

The planned rule "skip items where `food` is null and `is_section_title` is
false" would discard the `is_holiday: true` / `"No School"` markers, which are
the only real signal this API currently carries and are exactly what the board's
no-school state needs. Keep them as a per-day holiday flag instead.

## Open question

Where Clarkston actually publishes menus (PDFs, another vendor, or a different
Nutrislice subdomain) is unresolved. The board cannot be built against this API
until that is answered.
