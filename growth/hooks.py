"""Callables the host bot installs at startup. Keeps growth free of imports
from stm_bot (which imports growth)."""
from __future__ import annotations

from typing import Callable

# sync_now(max_age_seconds) -> bool: run STM's TaskBus sync if the last one is older than max_age.
sync_now: Callable[[int], bool] | None = None
