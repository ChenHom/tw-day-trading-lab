"""Compatibility helpers at the Shioaji SDK boundary."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from typing import Any


def supported_kwargs(
    callable_: Callable[..., Any],
    values: Mapping[str, Any],
) -> dict[str, Any]:
    parameters = inspect.signature(callable_).parameters.values()
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters):
        return dict(values)
    accepted = {
        parameter.name
        for parameter in parameters
        if parameter.kind
        in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    }
    return {name: value for name, value in values.items() if name in accepted}


def login_simulation_api(
    login: Callable[..., Any],
    *,
    api_key: str,
    secret_key: str,
    fetch_contract: bool,
    subscribe_trade: bool,
) -> Any:
    values = {
        "api_key": api_key,
        "secret_key": secret_key,
        "fetch_contract": fetch_contract,
        "subscribe_trade": subscribe_trade,
    }
    kwargs = supported_kwargs(login, values)
    missing = {"api_key", "secret_key"} - kwargs.keys()
    if missing:
        raise TypeError(
            f"Shioaji login does not accept required credentials: {sorted(missing)}"
        )
    return login(**kwargs)
