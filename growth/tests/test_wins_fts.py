from growth import bank, gdb
from growth.tests.conftest import USER_ID


def test_dual_write_search_and_remind(env):
    a = bank.add(USER_ID, "Talked to the neighbours I had been avoiding for a month", "win", "2026-09-01")
    b = bank.add(USER_ID, "Finished the tax letter in one sitting", "win", "2026-09-02")
    gdb.update_win(a, tags=["social_courage"], difficulty=4)
    gdb.update_win(b, tags=["finished_hard_thing"], difficulty=3)
    rows = gdb.search_wins(USER_ID, bank.fts_query_from("when did I talk to neighbours"))
    assert rows and rows[0]["id"] == a
    # malformed query falls back to a phrase
    assert gdb.search_wins(USER_ID, 'tax "letter') is not None
    sys_win = bank.add_system(USER_ID, "Aim C2 reached", "2026-09-03")
    seen = {bank.remind(USER_ID)["id"] for _ in range(30)}
    assert sys_win not in seen and seen <= {a, b}
    assert gdb.get_win(a)["shown_count"] + gdb.get_win(b)["shown_count"] == 30


def test_split_dated():
    assert bank.split_dated("2019: got the job") == ("2019", "got the job")
    assert bank.split_dated("2024-03-01 - ran 10k") == ("2024-03-01", "ran 10k")
    assert bank.split_dated("no date here") == (None, "no date here")
