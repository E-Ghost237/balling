# UI/UX Spec — Trust, Pricing & Quota Surfaces

Derived from `decisions.md` (2026-08-30). Scope is deliberately limited to
what those decisions actually justify changing — trust/stats display,
pricing clarity, quota messaging. Not a general visual redesign; no brand,
layout, or color-system changes are specified here.

Applies to: `webapp/templates/accuracy.html`, `webapp/templates/landing.html`
(accuracy block, appears twice — verify both instances stay in sync),
`webapp/accuracy.py`, `webapp/templates/_landing_pricing.html`,
`webapp/plans.py`-derived pricing display.

## 1. Separate "live" stats from "backtested" stats — do not let them blend

**Problem this fixes**: the current `/accuracy` page and landing-page block
show only the live, rolling in-season numbers (currently a ~2-week sample).
Nothing on the page distinguishes a 2-week live number from a
statistically-meaningful validated one, which is exactly the ambiguity this
project's own decisions.md had to work through by hand.

**Requirement**:
- Keep the existing live section (`accuracy.outcome_pct`, `score_pct`,
  won/lost, per-match log) as-is — it's honest and already shows real
  wins/losses, not a curated highlight reel. Don't touch its logic.
- Add a second, visually distinct block labeled as **validated backtest**,
  separate from the live block, showing:
  - Sample size and date range (e.g. "29,600 matches, Aug 2022–present")
  - Model accuracy vs. baseline accuracy, side by side (not just the model
    number alone — the baseline comparison is the whole point)
  - One line of methodology: "walk-forward, no lookahead — each match
    predicted using only data available before it happened"
- Never merge the two numbers into one headline stat. A reader must be able
  to tell, without hunting, whether they're looking at "how we're doing
  this season" or "how well-validated the model is overall."
- Source the backtest numbers from a checked-in results artifact (e.g. a
  JSON/CSV committed alongside a periodic `modeling/backtest.py` run), not
  a live query — this is a validation snapshot, refreshed deliberately when
  the model changes, not a per-request computation.

## 2. Show market coverage explicitly — including what's NOT graded

**Problem this fixes**: BTTS and Over/Under predictions are surfaced to
users (see `simulate.py`'s `compute_markets`) but have zero accuracy
tracking. Silence on this reads as "presumably fine," which decisions.md
explicitly says not to imply.

**Requirement**:
- On `/accuracy`, list all four markets the product predicts (1X2,
  correct score, BTTS, Over/Under) with a status per market:
  - 1X2: tracked (existing outcome stat)
  - Correct score: tracked (existing score stat)
  - BTTS: **not yet tracked** — label directly, don't omit
  - Over/Under: **not yet tracked** — label directly, don't omit
- Use a neutral, factual tag (e.g. "Tracking coming soon"), not an
  apologetic or promotional one — this is a disclosure, not a marketing
  moment.

## 3. Add a per-league / methodology disclaimer

**Problem this fixes**: backtest results show real, large variance by
league (Premier League/La Liga/Serie A all >52% accuracy; K League 1 at
31.9%, below the no-skill baseline, over 796 matches — not noise). A
blanket accuracy number currently implies uniform reliability across every
competition offered.

**Requirement**:
- Add one short disclaimer line near the headline stats, on both
  `/accuracy` and the landing block: performance varies by league/
  competition; headline figures are an aggregate, not a per-competition
  guarantee.
- Do not build a full per-league table on the public page from this spec
  alone — that's a bigger design decision (how many leagues, sortable?,
  etc.) than tonight's conversation settled. Flag as a follow-up, not an
  immediate requirement.

## 4. Quota messaging — confirm accuracy, tighten wording

**Problem this fixes**: `plans.py` already describes quota correctly
("20 distinct matchups / month"), which is good — this section is mostly a
verification pass, not a rewrite.

**Requirement**:
- Confirm the word "distinct" stays in every plan's feature list — it's
  load-bearing: quota counts unique matchups per cycle
  (`webapp/quota.py::distinct_matchup_count`), and re-viewing an
  already-used matchup is free. Dropping "distinct" from copy would make
  the limit read as stricter than it is.
- Verify the 85%-of-quota warning (`quota.py::QUOTA_WARNING_RATIO`) is
  actually surfaced to the user somewhere in the simulate flow
  (`_simulate_content.html` / `_result_card.html`) — if it isn't rendered
  anywhere yet, add a small inline notice ("17/20 used this cycle") when
  `warn=True` comes back from `check_quota`. If it's already there, no
  change needed.

## 5. Payment section — no changes required

`_landing_pricing.html` already correctly shows MTN Mobile Money and Orange
Money as live and cards as "coming soon," matching the actual payment
setup in decisions.md. Leave as-is.

## Explicitly out of scope for this spec

- Free-tier signup friction (email vs. phone verification) — decided to
  leave as-is; no UI change follows from that.
- Any new anti-abuse UI (rate-limit banners, CAPTCHA, etc.) — not decided.
- BTTS/Over-Under backtest implementation itself — decided to defer; this
  spec only covers disclosing that it's not done yet (§2), not building it.
- Visual/brand redesign, layout, color system — untouched by tonight's
  decisions, so untouched by this spec.
