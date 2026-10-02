# Shioaji 1.7.5 Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run this repository in an isolated Shioaji 1.7.5 environment while preserving existing simulation-only behavior and safety gates.

**Architecture:** Put SDK-version differences behind a small `shioaji_compat` module, keeping core code free of direct SDK imports. Pin the SDK in project metadata and verify it in a repository-local `.venv`; no login, subscription, or order side effect is part of this upgrade.

**Tech Stack:** Python 3.10, unittest, setuptools/pyproject.toml, Shioaji 1.7.5, stdlib inspect

---

## File Map

- Create `src/tw_day_trading_lab/shioaji_compat.py`: login keyword filtering and stock-contract lookup.
- Create `tests/test_shioaji_compat.py`: legacy and 1.7-shaped compatibility tests.
- Modify `simulation.py`, `market_data.py`, `backfill.py`, and `cli.py`: use the compatibility boundary.
- Modify `pyproject.toml`: pin `shioaji==1.7.5`.
- Modify `README.md` and `docs/development-work.md`: operating instructions and evidence.

### Task 1: Login signature compatibility

**Files:**
- Create: `src/tw_day_trading_lab/shioaji_compat.py`
- Create: `tests/test_shioaji_compat.py`
- Modify: `src/tw_day_trading_lab/simulation.py:625-631`

- [ ] **Step 1: Write the failing 1.7-style test**

```python
import unittest

from tw_day_trading_lab.shioaji_compat import login_simulation_api


class ShioajiCompatibilityTest(unittest.TestCase):
    def test_login_omits_removed_fetch_contract_keyword(self):
        calls = []

        def login(*, api_key, secret_key, subscribe_trade):
            calls.append(locals())
            return ["stock-account"]

        accounts = login_simulation_api(
            login,
            api_key="key",
            secret_key="secret",
            fetch_contract=False,
            subscribe_trade=False,
        )

        self.assertEqual(accounts, ["stock-account"])
        self.assertNotIn("fetch_contract", calls[0])
```

- [ ] **Step 2: Run and verify RED**

```bash
PYTHONPATH=src python3 -m unittest tests.test_shioaji_compat
```

Expected: import error because `shioaji_compat` does not exist.

- [ ] **Step 3: Implement the minimum helper**

```python
"""Compatibility helpers at the Shioaji SDK boundary."""

import inspect
from collections.abc import Callable, Mapping
from typing import Any


def supported_kwargs(callable_: Callable[..., Any], values: Mapping[str, Any]) -> dict[str, Any]:
    parameters = inspect.signature(callable_).parameters.values()
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters):
        return dict(values)
    accepted = {
        parameter.name
        for parameter in parameters
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    }
    return {name: value for name, value in values.items() if name in accepted}


def login_simulation_api(login, *, api_key, secret_key, fetch_contract, subscribe_trade):
    values = {
        "api_key": api_key,
        "secret_key": secret_key,
        "fetch_contract": fetch_contract,
        "subscribe_trade": subscribe_trade,
    }
    kwargs = supported_kwargs(login, values)
    missing = {"api_key", "secret_key"} - kwargs.keys()
    if missing:
        raise TypeError(f"Shioaji login does not accept required credentials: {sorted(missing)}")
    return login(**kwargs)
```

Replace the direct `self._api.login(...)` call with `login_simulation_api(self._api.login, ...)` and import the helper.

- [ ] **Step 4: Add legacy `**kwargs` coverage and verify GREEN**

```python
    def test_login_preserves_optional_keywords_for_legacy_api(self):
        calls = []

        def login(**kwargs):
            calls.append(kwargs)
            return []

        login_simulation_api(
            login,
            api_key="key",
            secret_key="secret",
            fetch_contract=True,
            subscribe_trade=True,
        )
        self.assertTrue(calls[0]["fetch_contract"])
        self.assertTrue(calls[0]["subscribe_trade"])

    def test_login_never_silently_drops_required_credentials(self):
        def login(*, subscribe_trade):
            return []

        with self.assertRaisesRegex(TypeError, "required credentials"):
            login_simulation_api(
                login,
                api_key="key",
                secret_key="secret",
                fetch_contract=False,
                subscribe_trade=False,
            )
```

```bash
PYTHONPATH=src python3 -m unittest tests.test_shioaji_compat tests.test_simulation
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/tw_day_trading_lab/shioaji_compat.py src/tw_day_trading_lab/simulation.py tests/test_shioaji_compat.py
git commit -m "fix: support Shioaji 1.7 login signature"
```

### Task 2: Contract lookup compatibility

**Files:**
- Modify: `src/tw_day_trading_lab/shioaji_compat.py`
- Modify: `src/tw_day_trading_lab/simulation.py`
- Modify: `src/tw_day_trading_lab/market_data.py`
- Modify: `src/tw_day_trading_lab/backfill.py`
- Modify: `src/tw_day_trading_lab/cli.py`
- Modify: `tests/test_shioaji_compat.py`

- [ ] **Step 1: Write failing lookup tests**

```python
from types import SimpleNamespace
from tw_day_trading_lab.shioaji_compat import stock_contract


    def test_stock_contract_supports_legacy_mapping(self):
        api = SimpleNamespace(Contracts=SimpleNamespace(Stocks={"2330": "legacy"}))
        self.assertEqual(stock_contract(api, "2330"), "legacy")

    def test_stock_contract_prefers_new_service(self):
        stocks = SimpleNamespace(get=lambda symbol: f"new-{symbol}")
        api = SimpleNamespace(
            contracts=SimpleNamespace(stocks=stocks),
            Contracts=SimpleNamespace(Stocks={"2330": "legacy"}),
        )
        self.assertEqual(stock_contract(api, "2330"), "new-2330")
```

- [ ] **Step 2: Run and verify RED**

```bash
PYTHONPATH=src python3 -m unittest tests.test_shioaji_compat
```

Expected: import error for `stock_contract`.

- [ ] **Step 3: Implement the helper**

```python
def stock_contract(api: Any, symbol: str) -> Any:
    contracts = getattr(api, "contracts", None)
    stocks = getattr(contracts, "stocks", None)
    get_contract = getattr(stocks, "get", None)
    if callable(get_contract):
        return get_contract(symbol)
    return api.Contracts.Stocks[symbol]
```

- [ ] **Step 4: Replace all five direct stock lookups**

Import `stock_contract` in the four caller modules and replace direct `api.Contracts.Stocks[symbol]` or `self._api.Contracts.Stocks[...]` expressions with:

```python
stock_contract(api, symbol)
stock_contract(self._api, symbol)
stock_contract(self._api, request.symbol)
```

Verify no direct lookup remains:

```bash
rg -n 'Contracts\.Stocks' src/tw_day_trading_lab --glob '!shioaji_compat.py'
```

Expected: no output; the legacy expression remains only inside the compatibility helper.

- [ ] **Step 5: Run boundary-focused tests**

```bash
PYTHONPATH=src python3 -m unittest tests.test_shioaji_compat tests.test_simulation tests.test_market_data tests.test_rvol
```

Expected: all selected tests pass without external connections.

- [ ] **Step 6: Commit**

```bash
git add src/tw_day_trading_lab/shioaji_compat.py src/tw_day_trading_lab/simulation.py src/tw_day_trading_lab/market_data.py src/tw_day_trading_lab/backfill.py src/tw_day_trading_lab/cli.py tests/test_shioaji_compat.py
git commit -m "refactor: isolate Shioaji contract lookup"
```

### Task 3: Pin and install Shioaji 1.7.5

**Files:**
- Modify: `pyproject.toml`
- Runtime only: `.venv/`

- [ ] **Step 1: Pin the exact dependency**

```toml
dependencies = [
    "shioaji==1.7.5",
]
```

- [ ] **Step 2: Create the isolated environment and install**

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
```

Expected: `.venv/` stays ignored and pip installs exactly Shioaji 1.7.5.

- [ ] **Step 3: Verify version and real login signature without login**

```bash
.venv/bin/python -c 'import inspect, shioaji; print(shioaji.__version__); print(inspect.signature(shioaji.Shioaji(simulation=True).login))'
```

Expected: version `1.7.5`; signature lacks `fetch_contract`. Constructing the API object does not call login.

- [ ] **Step 4: Run targeted and full suites in `.venv`**

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_shioaji_compat tests.test_simulation tests.test_market_data tests.test_rvol
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests
```

Expected: targeted tests pass; full count exceeds 378 and all pass.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml
git commit -m "build: pin Shioaji 1.7.5"
```

### Task 4: Documentation and status

**Files:**
- Modify: `README.md`
- Modify: `docs/development-work.md`

- [ ] **Step 1: Document the isolated runtime**

Add to README Quick Start:

````markdown
### Python / Shioaji runtime

Shioaji is pinned to `1.7.5` and verified from the repository-local `.venv`:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -c 'import shioaji; print(shioaji.__version__)'
```

Use `.venv/bin/python` for Shioaji commands. Installation does not enable login or orders.
````

- [ ] **Step 2: Record evidence and residual risk**

Append a `2026-10-02 Shioaji 1.7.5 isolated upgrade` section to `docs/development-work.md` recording:

```text
- ambient 1.3.2 replaced by repository-local 1.7.5 runtime
- capability-based login keywords and dual contract lookup
- exact targeted/full test counts actually observed
- no real login, subscription, order, cancellation, Telegram, or GitHub side effect
- residual: gated simulation provider smoke still required for live SDK behavior
```

- [ ] **Step 3: Verify and commit docs**

```bash
git diff --check
git add README.md docs/development-work.md
git commit -m "docs: record Shioaji 1.7.5 runtime"
```

### Task 5: Review, verification, and delivery

**Files:**
- Review all changes since `c6b0f2a`
- Modify `docs/development-work.md` only for concrete review findings

- [ ] **Step 1: Run the required grill-me, code, and security reviews**

Check each item and record concrete findings:

```text
- required credentials can never be filtered out silently
- inspect.signature works on the real 1.7.5 login callable
- contract lookup cannot select a non-stock product
- simulation=False rejection remains unchanged
- shared user-site package remains 1.3.2 and unmodified
- no test triggered an external side effect
- no secret, credential, or environment value is in the diff
```

- [ ] **Step 2: Run fresh final verification**

```bash
.venv/bin/python -c 'import shioaji; assert shioaji.__version__ == "1.7.5"'
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests
PYTHONPATH=src .venv/bin/python -m compileall -q src tests
git diff --check
git status --short --branch
```

Expected: version assertion exits 0; all tests pass with a count above 378; compile and diff checks exit 0; `.venv` is absent from Git status.

- [ ] **Step 3: Commit review corrections only when needed**

```bash
git add docs/development-work.md src tests
git commit -m "fix: harden Shioaji 1.7 compatibility"
```

Do not create this commit when review produces no tracked changes.

- [ ] **Step 4: Push**

```bash
git push origin master
```

Expected: `origin/master` advances through the design and implementation commits. Report commit IDs, push result, exact test count, and that no provider smoke was run.
