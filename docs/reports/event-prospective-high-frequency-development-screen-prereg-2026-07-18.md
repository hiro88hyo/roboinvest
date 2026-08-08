# Prospective High-Frequency Event Development Screen Preregistration

Date: 2026-07-18

Status: Registered before implementation and execution.

## Purpose And Evidence Boundary

The existing low-frequency cluster-v1 forward lane cannot reasonably produce
enough closed trades before the unchanged 2026-09-30 project deadline. There
are 48 TSE sessions from 2026-07-21 through 2026-09-30. A fixed-2 candidate has
46 possible entry sessions whose registered exit is available by the deadline.

This screen may use all point-in-time observations ending on 2026-06-23 as
contaminated development data. Historical train, validation, and locked-OOS
labels have already been inspected in the broader research cycle and are not
clean OOS evidence here. The only future promotion evidence for the selected
hypothesis, if any, is the untouched prospective interval beginning
2026-07-21.

The user explicitly authorized continuing the previously proposed plan to use
all existing data as development and to increase preregistered opportunity
frequency. This does not authorize paper/live publication or weaken the
project kill switch.

## Shared Frozen Cohort

Group observations by `trade_group_id`, falling back to `event_cluster_id` and
then `observation_id`. A group is eligible when at least one member, using only
point-in-time features, passes both existing rules:

- fundamental pass:
  - dividend revision subtype `increase`; or
  - positive profit revision, operating-profit revision, or absolute forecast
    EPS revision;
- technical pass on that same member:
  - 20-session average turnover at least 200,000,000 JPY;
  - 14-session ATR from 0.5% through 8%, inclusive;
  - 20-session return missing or below 30%;
  - per-symbol regime is not `broad_downtrend`.

Select one deterministic representative per group. Entry is the next-session
official open and exit is the second trading-session close. Round-trip cost is
0.298%, capital is 2,000,000 JPY, maximum positions is five, maximum position
notional is 20% of capital, lot size is 100 shares, same-symbol overlap is
prohibited, and same-day exit cash cannot fund entries.

## Bounded Development Variants

Exactly these three variants may be inspected:

1. `broad_feature_time_fixed2`: order same-day candidates by frozen feature
   cutoff, symbol, and event ID.
2. `broad_quality_priority_fixed2`: use the same cohort but order crowded days
   by the deterministic quality tiers below, then feature cutoff, symbol, and
   event ID.
3. `quality_tiers_0_2_fixed2`: use the same quality order and exclude tier 3.

Quality tiers, lower number first:

- tier 0: forecast revision that passes both the existing fair-value quality
  rule and the existing core-profit quality rule;
- tier 1: forecast revision that passes either quality rule;
- tier 2: dividend increase with valid forecast dividend yield at least 3%;
- tier 3: every other member of the shared eligible cohort.

No threshold, horizon, stop, quality tier, cohort, or fourth variant may be
added after results are inspected.

## Development Decision Contract

A variant is eligible to become the one prospective primary only when all of
these development diagnostics pass:

- full-development opened trades at least 500;
- full-development cost-adjusted PF greater than 1.3;
- full-development maximum drawdown below 10% of 2M capital;
- 10 bps entry plus 25 bps exit stress PF greater than 1.2 and drawdown below
  10%;
- at least 75% of calendar-year blocks have positive net PnL;
- median opened trades across historical July-21-through-September-30 windows
  is at least 30, counting only trades whose fixed-2 exit is available by the
  window end;
- true same-symbol random-date percentile is at least p75 with 300 seeds, zero
  unmatched candidates, and zero fallback.

If multiple variants pass, select the highest stressed PF, breaking ties by the
listed order. If none passes, record `NO_CANDIDATE`; do not weaken the gate.

## Prospective Contract If One Variant Passes

- prospective OOS start: 2026-07-21;
- deadline: 2026-09-30;
- economic gate: PF greater than 1.2 and maximum drawdown below 10% of 2M;
- minimum closed trades: 30;
- operational feasibility checkpoint: after 20 TSE sessions, at least 12
  closed trades;
- no parameter change, replacement variant, or retrospective inclusion after
  prospective collection starts;
- cluster v1 remains a separate secondary shadow lane and its trades are not
  pooled with this candidate.

Passing this development screen only permits implementation of causal shadow
collection. It does not permit paper/live activation.
