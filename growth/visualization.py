"""Visualization challenges: prompt generation and storage (playback: Stage 2)."""
from __future__ import annotations

import random

from . import gdb, loader


def _roles(user) -> list[str]:
    core = gdb.get_core(user["user_id"])
    roles = core.get("roles") or []
    return [str(r).strip() for r in roles if str(r).strip()]


def prompt_for(user, day: str, state: dict) -> str:
    """Pick 1–2 roles and a horizon (avoiding recent pairs); remember them in state.meta."""
    c = loader.content().visualization
    meta = state.setdefault("meta", {})
    if meta.get("vis"):
        return meta["vis"]["prompt"]
    roles = _roles(user)
    if not roles:
        return "Add your roles with /why roles father, husband, … to get visualization prompts."
    recent = {tuple(r) for r in gdb.recent_visualization_roles(user["user_id"], int(c.get("avoid_repeat_days", 14)), day)}
    rng = random.Random()
    tries = 0
    while True:
        k = 2 if len(roles) >= 2 and rng.random() < 0.6 else 1
        pick = rng.sample(roles, k)
        tries += 1
        if tuple(sorted(pick)) not in recent or tries > 10:
            break
    horizon = rng.choice(c["horizons"])
    template = rng.choice(c["templates"])
    role_a = pick[0]
    role_b = pick[1] if len(pick) > 1 else pick[0]
    if len(pick) == 1 and "{role_b}" in template:
        template = c["templates"][1]
    text = template.format(role_a=role_a, role_b=role_b, horizon=horizon["label"],
                           horizon_cap=horizon["label"][0].upper() + horizon["label"][1:])
    meta["vis"] = {"roles": pick, "horizon": horizon["id"], "prompt": text}
    return text


def save(user, day: str, state: dict, text: str) -> int:
    vis = state.get("meta", {}).get("vis") or {}
    vid = gdb.add_visualization(user["user_id"], day, vis.get("roles", []), vis.get("horizon"), text)
    try:
        from . import gai
        tags = gai.tag_visualization(text, vis.get("roles", []))
        if tags:
            gdb.update_visualization(vid, tags=tags)
    except Exception as e:
        print(f"  growth visualization tag error: {e}")
    return vid
