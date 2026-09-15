# Product & Business Decisions

Record of decisions made on 2026-08-30. Source of truth for `spec.md` and any
future work that touches trust/stats display, pricing, payments, or quota
messaging. Update this file when a decision changes — don't let it drift out
of sync with what's actually live.

## Regulatory & compliance

- **Market**: Cameroon, launched.
- **Status**: reviewed and confirmed by a lawyer. Not independently verified
  as part of this document — if the legal basis changes (new regulation,
  market expansion outside Cameroon), re-confirm before assuming this still
  holds.
- Public policies (Terms, Privacy) are already published — see
  `webapp/templates/terms.html`, `privacy_policy.html`.

## Payments

- **Rails**: MTN Mobile Money and Orange Money, via a payment aggregator.
  Cards are not yet processed (`_landing_pricing.html` already shows this as
  "coming soon").
- Aggregator was onboarded with the business accurately disclosed as a
  sports-tipster subscription service — no category-mismatch freeze risk.
- **Known, accepted risk**: single payment channel, no backup rail. On an
  aggregator outage, revenue generation pauses and previously-collected
  funds are delayed (not lost) until resolved. No fallback channel exists
  today. Accepted for now; revisit before this is load-bearing at real
  scale.
- Refunds for user-side disputes are handled manually via the admin
  dashboard (`webapp/templates/admin/payments.html` / `finance.html`).

## Prediction model — validated

Production model (`modeling/rating_engine.py` + `modeling/simulate.py`) is
Elo + attack/defense strength, Monte Carlo-simulated. The optional
ppg/xppg/momentum/shot-diff nudges exist in code but are **deliberately not
wired into the live model** — backtesting showed they make log loss/Brier
worse, not better (see `simulate.py` header comment).

Walk-forward, no-leakage backtest run 2026-08-30 against 4 seasons of data
(2022-08-01 to present, 29,600 scored matches, `modeling/backtest.py --since
2022-08-01 --features ""`):

| Metric | Model | Baseline (historical H/D/A rate) |
|---|---|---|
| 1X2 accuracy | 49.2% | 43.9% |
| Log loss (lower better) | 1.043 | 1.074 |
| Brier score (lower better) | 0.621 | 0.650 |
| Exact scoreline | 11.8% | — |

**Conclusion: the model beats the naive baseline by a real, consistent
margin across a large sample — not noise.** The live 2-week correct-score
figure (~11%) lines up closely with the 11.8% large-sample backtest.

Caveats to carry forward into any public-facing claim:

- The baseline is "always predict the historically most common outcome,"
  not "always bet the market favorite" (no odds data available for that
  comparison).
- **Per-league performance is uneven.** Big five leagues are solid
  (La Liga 53.2%, Serie A 53.1%, Premier League 52.7%). K League 1 is a
  known weak spot at 31.9% accuracy over 796 matches — worse than the
  baseline, not a small-sample fluke. Serie B (41.2%) and Ligue 2 (42.5%)
  are also below the pack.
- **BTTS and Over/Under markets are not backtested.** Decision made
  2026-08-30: leave this gap for now rather than build the scoring
  extension immediately. Do not imply a validated track record for these
  two markets anywhere in the product until this changes.

## Free-tier abuse

- No anti-abuse mechanism exists. A free account can be re-created with a
  new email indefinitely, bypassing the monthly quota.
- **Decision**: keep email-only signup as-is. Deliberate trade-off —
  low-friction growth over enforcement, at this stage. Not an oversight;
  revisit if abuse becomes measurable.
- IP-based limiting was considered and rejected: most users are on
  MTN/Orange mobile data behind carrier-grade NAT, so IP limits would
  collateral-block legitimate users sharing a carrier IP while barely
  slowing down an actual abuser.

## Quota mechanic (as implemented — `webapp/plans.py`, `webapp/quota.py`)

Quota counts **distinct matchups per rolling cycle**, not total
predictions — repeat views of an already-used matchup don't count again.
Warning fires at 85% of the cycle's limit.

| Plan | Price (FCFA) | Quota/cycle |
|---|---|---|
| Free | 0 | 20 |
| Monthly | 10,000 | 150 |
| 6 Months | 48,000 | 1,200 (200/mo) |
| Yearly | 84,000 | 3,000 (250/mo) |
