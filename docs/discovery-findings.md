# Nutrislice discovery findings

Run 2026-08-20 via `tools/discover.py`. This project's Claude Code session has
`*.nutrislice.com` blocked by its network egress proxy, so discovery ran on a
GitHub Actions runner instead.

## Confirmed constants

```
HOST        https://clarkston.api.nutrislice.com
SCHOOLS     springfield-plains   "Springfield Plains Elementary"
            sashabaw-middle      "Sashabaw Middle"
MENU TYPES  breakfast, lunch
```

`GET /menu/api/schools/?format=json` returns 12 schools, each with an inline
`menu_types[]` carrying `slug` + `name`. Both target schools serve
`breakfast` and `lunch`. Note that the advertised `menu_types[]` is not a
reliable guide to what is actually published: on 2026-08-20 `sashabaw-middle`
listed only `lunch` and its breakfast weeks were empty, and by 2026-08-25 it was
publishing full breakfast menus. Treat an empty meal as "not published yet",
never as "this school does not serve it".

Ruled out, all HTTP 404: `elementary-lunch`, `elementary-breakfast`, `ms-lunch`,
`ms-breakfast`, `middle-school-*`, `secondary-*`, `k-5-*`, `6-8-*`, every
per-school menu-type endpoint, and `digest/school/.../date/...`.

## Week endpoint contract

`GET /menu/api/weeks/school/{school}/menu-type/{type}/{YYYY}/{MM}/{DD}/`

Root holds `days[]` plus `bold_all_entrees_enabled`. Each day is
`{date, has_unpublished_menus, menu_items[]}`. Three kinds of row appear in
`menu_items[]`:

* **Station header** -- `is_section_title: true`, `is_station_header: true`,
  `text: "Main Entrees"`, a `station_id`, `food: null`.
* **Food** -- `is_section_title: false`, `text: ""`, and a populated `food`
  object with `name`, `description`, `food_category`, `image_url`, and
  `rounded_nutrition_info`.
* **Holiday marker** -- `is_holiday: true`, `bold: true`, `text: "No School"`,
  `food: null`.

Station names observed (2026-08-25, both weeks):

| School | Meal | Stations |
|---|---|---|
| springfield-plains | breakfast | Main Entrees, Alternate Entrees, Sides for All Meals |
| springfield-plains | lunch | Main Entrees, Alternate Entrees, Salad, Fruit & Vegetable Bar, Sides for All Meals, Milk & Condiments |
| sashabaw-middle | breakfast | Breakfast, Alternate Entrees, Sides for All Meals |
| sashabaw-middle | lunch | Create, Grill, 2Mato, Fruit & Vegetable Bar |

`SIDE_STATION_RE` in `collect.py` decides which of these are hero stations; the
collector logs the full station vocabulary on every run so the pattern can be
retuned if the district renames one.

## Publication window

Clarkston publishes roughly two weeks ahead and nothing further out. Scanning
every Monday from 2026-08-03 to 2027-05-24:

```
springfield-plains     ...FF......................................
sashabaw-middle        ...FF......................................
   F = week has food items   . = empty
```

| Week | springfield-plains | sashabaw-middle |
|---|---|---|
| 2026-08-24 | 86 items | 126 items |
| 2026-08-31 | 52 items | 68 items |

Weeks outside that window return HTTP 200 with seven days and empty
`menu_items[]`. That is the normal steady state, not an error -- the collector's
current-week + next-week fetch is sized exactly to this window.

### Correction

An earlier pass concluded the district published no menu content at all. That
was wrong: the scan window ran 2025-08-25 to 2026-06-15, which stopped before
the 2026-27 school year and so never touched a published week. The prior year's
data has rolled off (only `No School` holiday markers remain in 2025-26), which
made an empty result look like a permanent one.

## Notes that shaped the collector

* `food_category` is populated, so the position-within-section fallback in
  `collect.py` is dormant. It stays in as a guard against a future payload that
  drops the field.
* Nutrislice tags salad-bar components as `food_category: "entree"` because they
  count toward the USDA meal pattern. At Springfield Plains that put `Croutons`,
  `Dinner Roll` and `Mixed Greens Salad with Cheese` in the entree bucket, which
  would have headlined "Croutons" on the board. `collect.py` consults the
  station header and demotes anything under a salad/fruit/side station.
* A school can publish lunch without breakfast, and that changes between terms
  (`sashabaw-middle` had no breakfast on 2026-08-20 and full breakfast menus by
  2026-08-25). The board omits an absent meal rather than rendering a
  permanently empty section, so either state renders correctly.
* Station names are preserved in `menus.json`. The kitchen's own grouping is
  more meaningful than a flat entree list -- Sashabaw serves `Create`, `Grill`
  and `2Mato`, Springfield `Main Entrees` and `Alternate Entrees` -- and it is
  what the board labels its hero lines with.
* Some accompaniments are tagged `food_category: "entree"` and filed under an
  entree station, so neither the noise pattern nor the station check catches
  them: `Cream Cheese` beside the bagel, `Sour Cream` beside the pierogies.
  These are matched by whole name and routed to the staples line.
* Holiday markers carry no food, so those days end up empty and are omitted from
  `menus.json`. The board renders a missing day as NO SCHOOL, which is the same
  outcome.
