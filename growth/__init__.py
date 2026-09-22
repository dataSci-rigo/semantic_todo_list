"""Growth Bot — a coaching layer inside the Semantic Task Manager bot.

Spec: ~/Documents/gbot/growth-bot-spec-v0.4.md. Public surface used by
stm_bot.py: init, tick, poll_timeout, busy, handle_command, handle_callback,
wants_reply, claim_text, cancel, COMMANDS, MENU_COMMANDS, help_block.
Everything here runs inside STM's single-threaded poll loop; there are no
threads and no scheduler library. Content lives in growth/content/*.yaml.
"""
from __future__ import annotations

from .commands import COMMANDS, MENU_COMMANDS, handle_command, help_block, needs_onboarding  # noqa: F401
from .session import cancel, claim_text, handle_callback, handle_text, wants_reply  # noqa: F401
from .sched import busy, poll_timeout, tick  # noqa: F401
from . import gdb, hooks, settings  # noqa: F401


def init() -> None:
    """Create growth tables and seed content-derived rows. Call after db.init()."""
    from . import excuses, loader
    gdb.init()
    loader.load()          # validates every YAML file at startup, fails loudly
    excuses.seed_all_users()
