from __future__ import annotations

from typing import Any

# Fields that are routing/control metadata, never forwarded upstream as-is
# by the param layer (model is set from the deployment; stream handled by caller).
_CONTROL_FIELDS = {"model"}


def apply_param_rules(
    client_params: dict[str, Any],
    *,
    drop_params: list[str] | None = None,
    default_params: dict[str, Any] | None = None,
    pinned_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply the deployment's param policy in order:

    1. drop   — remove params the upstream does not support / we forbid
    2. default— fill in only when the client did not provide the field
    3. pinned — force-override the client's value (the "写死" hard-coded params)

    Returns a new dict; does not mutate the input.
    """
    drop_params = drop_params or []
    default_params = default_params or {}
    pinned_params = pinned_params or {}

    params = {k: v for k, v in client_params.items() if k not in _CONTROL_FIELDS}

    # 1. drop
    for key in drop_params:
        params.pop(key, None)

    # 2. default (only if absent or None)
    for key, value in default_params.items():
        if params.get(key) is None:
            params[key] = value

    # 3. pinned (always wins)
    for key, value in pinned_params.items():
        params[key] = value

    return params
