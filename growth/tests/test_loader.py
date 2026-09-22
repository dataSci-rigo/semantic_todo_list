from growth import loader, steps


def test_content_validates():
    c = loader.load(force=True)
    assert {"morning", "refocus", "evening", "aim", "end", "avoid", "rest", "surf", "start5", "onboard"} <= set(c.touches)
    assert len(c.observation["weeks"]) == 14
    assert all(len(w["daily_prompts"]) == 7 for w in c.observation["weeks"])


def test_callback_data_fits_64_bytes():
    c = loader.load()
    for kind, touch in c.touches.items():
        for step in touch.get("steps", []):
            if "slot" in step:
                continue
            for opt in step.get("options", []) or []:
                assert len(f"gb:9999999:{step['id']}:{opt['value']}".encode()) <= 64
    for mid, m in c.modules.items():
        for step in m.get("steps", []):
            key = f"{mid}.{step['id']}"
            assert len(f"gb:9999999:{key}:some_value_here".encode()) <= 64


def test_conditions_and_templates():
    scope = {"answers": {"number": "p3", "three_good.good_1": "x"}, "ctx": {"p1_title": "Gate <hinge>", "n": 2}}
    assert steps.evaluate({"path": "answers.number", "in": ["p3", "other"]}, scope)
    assert not steps.evaluate({"path": "answers.number", "eq": "p1"}, scope)
    assert steps.evaluate({"any": [{"path": "answers.rest_kind", "eq": "drifted"}, {"path": "ctx.n", "gt": 1}]}, scope)
    assert steps.evaluate({"path": "answers.three_good.good_1", "truthy": True}, scope)
    assert steps.fill("P1 is {ctx.p1_title}.", scope) == "P1 is Gate &lt;hinge&gt;."
    assert steps.fill("{ctx.missing}|", scope) == "|"
