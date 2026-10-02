# Sector Flow v1 Design

## Goal

Build a cache-first CLI report that answers, for each available trading day in
`2026-09-24` through `2026-10-01`:

- which industry categories had the largest institutional net buying;
- how much of that came from foreign investors, investment trusts, and dealers;
- which stocks contributed most to each category;
- whether the latest weekly large-holder distribution moved toward or away from
  those categories.

The first delivery is JSON plus Markdown. It does not change candidate scores,
the dashboard, daily automation, Telegram, or any Shioaji path.

## Why official-first

FinMind can return a whole market for one date only on paid tiers for the
institutional and holding-distribution datasets, and its industry-chain money
flow dataset is Sponsor-only. A per-stock loop would exceed the project's
quota and reliability expectations.

V1 therefore uses official, no-key market-wide sources for the observations and
the existing FinMind stock-info cache only as taxonomy metadata:

| Data | Primary source | Frequency |
|---|---|---|
| Listed institutional trades | TWSE T86 daily report | Daily after close |
| OTC institutional trades | TPEx institutional daily report | Daily after close |
| Listed close prices | TWSE all-stock daily close | Daily after close |
| OTC close prices | TPEx all-stock daily close | Daily after close |
| Large-holder distribution | TDCC OpenAPI `/v1/opendata/1-5` | Weekly |
| Industry category and name | latest cached `TaiwanStockInfo` row per stock | Metadata |

The optional Sponsor dataset `TaiwanStockIndustryChainMoneyFlow` is not part of
V1. Its `trading_money` is turnover distribution, not institutional net flow.

## Commands

External access and deterministic reporting stay separate:

```bash
PYTHONPATH=src python3 -m tw_day_trading_lab.cli ingest sector-flow \
  --start-date 2026-09-24 \
  --end-date 2026-10-01 \
  --cache-dir data/raw

PYTHONPATH=src python3 -m tw_day_trading_lab.cli report sector-flow \
  --start-date 2026-09-24 \
  --end-date 2026-10-01 \
  --cache-dir data/raw \
  --output reports/2026-09-24_2026-10-01-sector-flow.json \
  --report-output reports/2026-09-24_2026-10-01-sector-flow.md
```

`ingest sector-flow` performs HTTP GET requests and writes raw responses.
`report sector-flow` never uses the network and can be replayed entirely from
cache.

## Raw cache contract

Raw provider payloads are preserved without destructive rewriting:

```text
data/raw/twse/T86/{date}/market.json
data/raw/twse/MI_INDEX/{date}/market.json
data/raw/tpex/institutional/{date}/market.json
data/raw/tpex/daily_close/{date}/market.json
data/raw/tdcc/holding_distribution/{as_of_date}/market.json
```

Each successful fetch first validates the provider status, payload shape, and
embedded date, then writes atomically. An HTTP 200 response with no rows or a
provider-level error does not overwrite an existing successful cache file.
Non-trading dates are recorded in the ingestion summary as `no_data` and do not
produce fabricated empty trading-day files.

The HTTP adapter has a finite timeout, a response-size limit, and no credential
or cookie support. Tests inject a fake client and never reach external hosts.

## Normalized records

Provider-specific parsing ends at immutable normalized records:

```python
@dataclass(frozen=True)
class InstitutionalFlowRow:
    trading_date: str
    market: str
    symbol: str
    name: str
    foreign_net_shares: int
    investment_trust_net_shares: int
    dealer_net_shares: int
    institutional_net_shares: int


@dataclass(frozen=True)
class ClosePriceRow:
    trading_date: str
    market: str
    symbol: str
    close: float


@dataclass(frozen=True)
class HoldingDistributionRow:
    as_of_date: str
    symbol: str
    level: int
    people: int
    shares: int
    percent: float
```

Numeric strings may contain commas, whitespace, or parentheses. Invalid
numeric fields are schema errors, not zero. Provider rows are matched by field
name rather than fixed column position so column reordering cannot silently
corrupt the report.

Only common-stock-looking symbols matching `^[1-9][0-9]{3}$` enter V1. ETFs,
ETNs, warrants, bonds, TDRs, indexes, and unclassified non-common instruments
are excluded.

## Institutional definitions

All quantities are shares, not lots:

- `foreign_net_shares`: foreign and Mainland investors, excluding foreign
  dealer proprietary trading.
- `investment_trust_net_shares`: domestic investment trusts.
- `dealer_net_shares`: the exchange-provided dealer total, including its
  proprietary and hedging components.
- `institutional_net_shares`: foreign + investment trust + dealer.

When a provider supplies both the total and components, the parser verifies the
identity. A mismatch turns that provider/date into `schema_error`; it is never
silently corrected.

The exact metric is net shares. Because the official per-stock institutional
reports do not publish execution value, V1 also calculates:

```text
estimated_net_amount_twd = net_shares * official daily close
```

Every JSON row includes `amount_method="net_shares_times_close"`, and Markdown
labels the result as an estimate. It must not be described as exact cash flow.

## Taxonomy and aggregation

V1 uses `TaiwanStockInfo.industry_category`, choosing for each stock the latest
metadata row whose source date is at or before the requested end date. This
produces one non-overlapping category per stock, avoids double-counting across
multiple industry-chain memberships, and prevents historical reports from
using future classification data. A stock with no eligible metadata row maps
to `未分類`; the report records the taxonomy source dates and coverage.

For each trading date and category, aggregate:

- exact foreign, investment-trust, dealer, and total institutional net shares;
- estimated TWD amounts for those four metrics;
- covered symbol count and missing-price count;
- top five positive and negative stock contributors.

The period summary sums only the available daily rows. Rankings use estimated
institutional net amount when price coverage is at least 90%; otherwise the
report is `degraded` and ranks by exact net shares with a visible reason.

Both inflow and outflow top-ten tables are emitted. A positive value means net
buy; a negative value means net sell.

## Large-holder proxy

TDCC holding distribution is a weekly ownership snapshot, not an order-flow
dataset. V1 defines the large-holder bucket as levels 12 through 15, equivalent
to holdings above 400 lots. Levels 16 and 17 are not included in the bucket;
level 17 is the total row.

For two consecutive cached snapshots, V1 calculates per stock:

```text
large_holder_share_delta = latest_large_holder_shares - prior_large_holder_shares
large_holder_percent_delta = latest_large_holder_percent - prior_large_holder_percent
```

The category estimate multiplies share delta by the latest available close and
is labelled `holding_change_proxy`, never `net_inflow`. If there are fewer than
two snapshots, or no snapshot at or before the requested end date, the large
holder section returns `status="insufficient_data"` with no numeric delta.

For the requested first-run range, this fail-closed result is acceptable and
must be shown rather than backfilled from a later snapshot.

## Output contract

The JSON artifact has this top-level shape:

```json
{
  "schema_version": 1,
  "requested_period": {
    "start_date": "2026-09-24",
    "end_date": "2026-10-01"
  },
  "observed_trading_dates": [],
  "status": "ok",
  "source_status": {},
  "taxonomy": {},
  "daily": [],
  "period_summary": [],
  "large_holder": {},
  "exclusions": {},
  "warnings": []
}
```

`status` is:

- `ok`: both markets loaded and price coverage is at least 90% for every
  observed day;
- `degraded`: at least one market, price set, or taxonomy mapping is incomplete;
- `blocked`: there are no valid institutional rows in the requested range.

Every provider status contains requested date, embedded/as-of date, row count,
cache path, and `ok`, `no_data`, `missing`, or `schema_error` state.

## Markdown report

The report contains:

1. requested range, observed trading dates, and overall data quality;
2. period institutional inflow and outflow top ten;
3. period foreign inflow and outflow top ten;
4. period investment-trust and dealer rankings;
5. per-day category rankings;
6. top contributing stocks per category;
7. weekly large-holder proxy or an explicit insufficient-data message;
8. source dates, coverage, exclusions, and warnings.

Values display shares and estimated TWD separately. The report never labels
turnover, ownership change, or an estimated amount as an exact net cash flow.

## Error handling and safety

- Requested dates are parsed as ISO dates and the start must not exceed the end.
- The range is capped at 31 calendar days in V1.
- HTTP 200 is not sufficient: provider status and embedded dates must validate.
- A provider schema change fails closed for that provider/date.
- Missing one market produces `degraded`, not a false whole-market result.
- Missing taxonomy maps a row to `未分類` and lowers taxonomy coverage.
- Missing close preserves exact share metrics but suppresses the amount for that
  stock rather than substituting zero.
- No Shioaji login, quote subscription, order, cancellation, Telegram send, or
  GitHub report publishing is part of this feature.

## Components and files

- `src/tw_day_trading_lab/sector_flow_sources.py`: HTTP client protocol,
  official endpoints, raw cache, and provider-specific parsers.
- `src/tw_day_trading_lab/sector_flow.py`: normalized dataclasses, taxonomy,
  aggregation, data-quality decisions, and JSON payload construction.
- `src/tw_day_trading_lab/sector_flow_report.py`: Markdown rendering only.
- `src/tw_day_trading_lab/cli.py`: `ingest sector-flow` and
  `report sector-flow` composition.
- `tests/test_sector_flow_sources.py`: fixtures, schema drift, dates, units, and
  cache behavior.
- `tests/test_sector_flow.py`: category aggregation, institutional identities,
  coverage, period summary, exclusions, and large-holder proxy.
- `tests/test_sector_flow_report.py`: stable Markdown sections and labels.
- `fixtures/sector-flow/`: small official-payload-shaped fixtures with no
  credentials.
- `docs/data-contracts.md`, `README.md`, and `docs/development-work.md`: contract,
  operator commands, evidence, and residual risks.

## Acceptance criteria

1. Fixture-driven tests prove TWSE, TPEx, TDCC, and close-price parsing without
   network access.
2. The original test suite plus new sector-flow tests passes in the repository
   `.venv`.
3. A gated real read-only ingestion for `2026-09-24` through `2026-10-01`
   writes raw caches and reports actual provider availability.
4. Re-running report generation with networking disabled produces identical
   JSON and Markdown.
5. The output identifies institutional and foreign net-buy categories for each
   available trading day and for the full range.
6. Large-holder output is either a correctly dated weekly delta or explicit
   `insufficient_data`; no daily large-holder claim is allowed.
7. Grill-me, code, security, and data-semantics reviews find no unresolved
   Critical or Important issue.
8. Documentation records exact test counts, provider dates, missing coverage,
   and that estimated amount is not an exact cash-flow figure.

## Deferred work

- Dashboard integration.
- Candidate-score integration.
- Daily scheduler, Telegram, or GitHub report publication.
- Sponsor-only `TaiwanStockIndustryChainMoneyFlow` comparison.
- Multi-membership industry-chain taxonomy.
- Broker-branch concentration as a separate large-player proxy.
- Historical TDCC archive acquisition beyond snapshots captured by this repo.
