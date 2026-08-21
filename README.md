# School Menu Board

Digital signage showing today's breakfast and lunch for two Clarkston Community
Schools buildings, pulled from the public Nutrislice API.

Runs on a DakBoard-driven Raspberry Pi at 1080x1920 portrait, always on and
non-interactive.

## Layout

| File | Role |
|---|---|
| `index.html` | The board. Self-contained -- inline CSS and JS, no build step. |
| `collect.py` | Fetches menus and writes `data/latest/menus.json`. |
| `data/latest/menus.json` | Normalized menu data the board reads. |
| `.github/workflows/collect.yml` | Runs the collector and commits the result. |
| `tools/discover.py` | One-off probe that confirmed the API slugs. |
| `docs/discovery-findings.md` | What the API actually returns, and why. |

## Board URLs

One deployed file serves three DakBoard blocks:

```
index.html                            both schools
index.html?view=springfield-plains    Springfield Plains alone
index.html?view=sashabaw-middle       Sashabaw Middle alone
```

Extra query parameters, all optional: `?breakfast=0` hides the breakfast block,
`?autofit=1` scales the fixed canvas to the window for desk testing, and
`?date=YYYY-MM-DD` simulates a calendar day (real clock time is kept) so the
weekend and roll-forward states can be checked without waiting for a weekend.

Behaviour worth knowing: past 13:30 local the board rolls forward to the next
serving day, because a lunch that has already been served is not useful on an
evening display. The same forward walk covers weekends, breaks and summer. A
failed fetch never clears the screen -- the last good render stays up and the
footer is flagged.

## Tuning

Everything adjustable sits in the `CONFIG` block at the top of the `<script>` in
`index.html`: which schools appear, their accent colours and tags, the
roll-forward hour, refresh interval, staleness threshold, and how many entrees
get hero treatment.

`collect.py` has its own constants near the top: the API host, the school and
menu-type slugs, the noise pattern that routes milk and condiments to the
staples line, and the station pattern that keeps salad-bar components out of the
entree bucket.

## Collector

Eight requests per run: 2 schools x 2 meals x 2 weeks. Clarkston publishes about
two weeks ahead, so that covers everything available. It runs once a day --
menus change rarely and the community guidance for this API asks that access be
kept light.

The run aborts without writing if any of the eight requests fail, or if zero
days parse. A stale `menus.json` is better than a half-written one.

Expect one small commit per day even when the menu has not changed:
`menus.json` carries a `generated_utc` stamp, and the board reads it to notice
that collection has stopped (the `STALE_HOURS` footer warning). Keeping the
timestamp moving is what makes that warning meaningful. If the daily commit
noise is not worth it, drop `generated_utc` from `collect.py` and the runs go
quiet -- along with the staleness warning.

`collect.yml` runs on a GitHub `schedule:` at 08:30 UTC -- 04:30 America/Detroit
in summer, 03:30 in winter -- so the board is fresh before the school day. The
`:30` offset avoids the top-of-the-hour queue, which is the most contended and
therefore the most delayed slot.

GitHub schedules runs best-effort, so a run can be late or occasionally skipped.
That is tolerable here: each run fetches the current *and* next week, so the
committed file carries about a week of runway, and the board keeps its last good
render regardless. Two consecutive misses trip the `STALE_HOURS` footer warning,
which is the signal worth acting on.

One caveat: GitHub disables scheduled workflows after 60 days of repository
inactivity, emailing the owner. Re-enable from the Actions tab. If the built-in
cron ever proves too unreliable, `workflow_dispatch` is still enabled, so an
external scheduler (cron-job.org and the like) can POST to the workflow's
`dispatches` endpoint instead with no change to the workflow.

## Setup

1. Enable GitHub Pages for this repo on `main` (Settings -> Pages -> Deploy from
   branch -> `main` / root). The board and its JSON are then served from the
   same origin, which is what the relative fetch in `index.html` expects.
2. Nothing to configure for scheduling -- `collect.yml` carries its own daily
   `schedule:`. Dispatch it once by hand from the Actions tab first, to confirm
   the fetch, the commit and the Pages redeploy all work end to end.
3. Add the board URLs above as DakBoard blocks.
