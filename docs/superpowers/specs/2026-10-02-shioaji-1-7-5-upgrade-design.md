# Shioaji 1.7.5 Upgrade Design

## Goal

Upgrade this repository from the ambient Shioaji 1.3.2 installation to an
isolated, reproducible Shioaji 1.7.5 runtime while preserving every existing
simulation-only safety boundary and all current behavior.

The upgrade is complete only when the original 378 tests plus the new SDK
compatibility regression tests pass from the repository-local virtual
environment.

## Scope

- Create a repository-local `.venv` and install Shioaji 1.7.5 there.
- Pin `shioaji==1.7.5` in project metadata.
- Make the simulation login adapter compatible with the Shioaji 1.7 login
  signature, which removed `fetch_contract`.
- Centralize stock contract lookup behind a small compatibility helper that can
  use the legacy `api.Contracts.Stocks[...]` interface and the newer
  `api.contracts` interface.
- Preserve compatibility with existing fake APIs and older SDK-shaped test
  doubles.
- Update operator documentation to use `.venv/bin/python` for Shioaji-related
  commands.

The following are explicitly out of scope:

- Real Shioaji login, quote subscription, order placement, or cancellation.
- `sj.Shioaji(simulation=False)` under any circumstance.
- The new `IndexComponents` industry-ranking adapter.
- Strategy, order, bar, or paper-trading behavior changes.
- Upgrading the shared user-site Shioaji installation.

## Dependency Isolation

The shared `~/.local` Python environment may be used by other trading projects,
so it will not be modified. A clean `.venv` under this repository is the
authoritative runtime for verification. The exact Shioaji version is declared
in `pyproject.toml`; installation must not silently float to a newer release or
fall back to 1.3.2.

The core package will retain lazy Shioaji imports so pure research, replay, and
unit-test code does not import the SDK merely by importing the project.

## SDK Compatibility Boundary

### Login

The current gateway always passes `fetch_contract`. Shioaji 1.7 removed this
keyword, so the adapter will inspect the callable signature and forward only
supported optional login keywords. Required credentials and the existing
`subscribe_trade` behavior remain unchanged.

Capability detection is preferred over comparing version strings because fake
APIs and future compatible SDKs may expose different subsets of keywords.

### Contract lookup

All Shioaji stock-contract reads will use one helper. It will first support the
legacy `api.Contracts.Stocks[symbol]` interface used by the current code. It
will also support the newer `api.contracts` lookup interface when available.
Missing contracts and provider errors remain fail-closed at the existing
caller boundaries.

### Safety

`ShioajiSdkSimulationGateway`, `ShioajiCallbackStream`,
`ShioajiTickStream`, and kbar backfill continue to reject an API object whose
`simulation` attribute is false. No compatibility fallback may weaken this
check.

## Test-Driven Implementation

Implementation follows red-green-refactor:

1. Add a failing test proving a 1.7-style login callable rejects the old
   `fetch_contract` call.
2. Add the minimal supported-keyword filtering needed to pass it.
3. Add failing contract-lookup tests for both legacy and new API shapes.
4. Add the minimal shared lookup helper and migrate Shioaji adapter callers.
5. Run targeted simulation, market-data, and RVOL tests after each change.

Tests must not load credentials, connect to Shioaji, subscribe to quotes, or
send orders. SDK installation and import/version inspection are allowed.

## Verification

All verification runs through `.venv/bin/python`:

```bash
.venv/bin/python -c 'import shioaji; assert shioaji.__version__ == "1.7.5"'
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests
PYTHONPATH=src .venv/bin/python -m compileall -q src tests
git diff --check
```

The test count must be at least 378. Any added compatibility test increases the
expected count; completion is not defined as preserving the literal old count.

## Failure Handling

- If Shioaji 1.7.5 cannot install or import, stop with the installer/import
  evidence. Do not substitute another version.
- If an existing test changes behavior, treat it as a regression and repair the
  compatibility boundary rather than rewriting the expectation.
- If an SDK operation would require credentials or external side effects, leave
  it unexecuted and report the remaining simulation smoke separately.

## Deliverables

- Exact Shioaji 1.7.5 dependency declaration.
- Repository-local `.venv` with the verified SDK version.
- Tested login and contract compatibility layer.
- Updated operating documentation.
- Development-work record with verification evidence and residual risk.
- An implementation commit pushed to `origin/master` after the full close-out
  checks and review required by this repository.
