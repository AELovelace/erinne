"""Prism Drop's rules (mnemerynne/prism.py), with a repeatable draw."""

from mnemerynne import prism


def seeded(seed):
    """A repeatable draw(n), so the same seed gives the same drop."""
    state = [seed & 0xffffffff]

    def draw(n):
        state[0] = (state[0] * 1664525 + 1013904223) & 0xffffffff
        return state[0] % n
    return draw


def test_a_field_has_28_special_pegs_in_unique_cells_none_on_the_entry_row():
    field = prism.random_obstacles(seeded(1))
    assert len(field) == 28
    assert {k: sum(p["type"] == k for p in field) for k in ("block", "bomb", "coin")} == {"block": 11, "bomb": 9, "coin": 8}
    assert len({(p["row"], p["column"]) for p in field}) == 28
    assert all(1 <= p["row"] < prism.HEIGHT and 1 <= p["column"] <= prism.WIDTH for p in field)


def test_every_drop_enters_at_pin_4_to_7_stays_inside_and_lands():
    for seed in range(500):
        result = prism.roll(5, seeded(seed))
        path = result["trajectory"]
        assert path[0]["row"] == 0 and 4 <= path[0]["column"] <= 7
        assert all(1 <= p["column"] <= prism.WIDTH and 0 <= p["row"] <= prism.HEIGHT for p in path)
        assert path[-1]["row"] == prism.HEIGHT and 1 <= result["landing"] <= prism.WIDTH
        assert len(path) < 200                             # single-use bombs: gravity always wins


def test_landing_points():
    assert [prism.landing_points(5, n) for n in (5, 4, 7)] == [20, 15, 10]
    assert prism.landing_points(1, 4) == 0 and prism.landing_points(10, 1) == 0


def test_a_drops_points_are_its_landing_plus_each_coin_peg_once():
    for seed in range(300):
        result = prism.roll(3, seeded(seed))
        coins = [p for p in result["trajectory"] if p.get("hit") == "coin"]
        assert len({(p["row"], p["column"]) for p in coins}) == len(coins)
        assert all(1 <= p["coins"] <= 5 for p in coins)
        assert result["bonus"] == sum(p["coins"] for p in coins)
        assert result["points"] == prism.landing_points(3, result["landing"]) + result["bonus"]
        assert result["landingPoints"] + result["bonus"] == result["points"] and result["pick"] == 3


def test_a_bomb_fires_once_per_drop_and_a_block_shoves_two():
    round_ = prism.drop(seeded(7), [{"row": 1, "column": 5, "type": "block"}])
    path = round_["trajectory"]
    for i, point in enumerate(path):
        if point.get("hit") == "block":
            assert abs(path[i + 1]["column"] - point["column"]) in (0, 2)     # two away, or bounced off a wall
    for seed in range(300):
        bombs = [(p["row"], p["column"]) for p in prism.roll(5, seeded(seed))["trajectory"] if p.get("hit") == "bomb"]
        assert len(set(bombs)) == len(bombs)


# ------------------------------------------------------------------- platforms
def _platform(row=3, left=4, direction=1):
    return {"row": row, "left": left, "dir": direction}


def _falling(column):
    """A draw that enters at `column` (4-7) and then always steps the same way, so a test knows where the ball goes."""
    answers = iter([column - 4])
    return lambda n: next(answers, 1 if n == 2 else 0)


def test_every_drop_has_two_platforms_on_rows_with_no_pegs():
    for seed in range(200):
        result = prism.roll(5, seeded(seed))
        rows = [p["row"] for p in result["platforms"]]
        assert len(rows) == 2 and 5 <= rows[0] <= 9 and 12 <= rows[1] <= 16
        assert not any(o["row"] in rows for o in result["obstacles"]) and len(result["obstacles"]) == 28
        assert all(1 <= p["left"] <= prism.WIDTH - prism.PLATFORM_SPAN + 1 and p["dir"] in (-1, 1) for p in result["platforms"])


def test_a_platform_slides_one_column_a_step_and_turns_at_the_walls():
    going = _platform(left=7, direction=1)
    assert [prism.platform_left(going, t) for t in range(5)] == [7, 8, 7, 6, 5]
    back = _platform(left=2, direction=-1)
    assert [prism.platform_left(back, t) for t in range(4)] == [2, 1, 2, 3]
    far = _platform(left=1, direction=1)
    assert all(1 <= prism.platform_left(far, t) <= 8 for t in range(500))
    assert all(abs(prism.platform_left(far, t + 1) - prism.platform_left(far, t)) == 1 for t in range(500))


def test_a_bounce_sends_the_ball_up_toward_the_edge_it_struck_with_the_platforms_motion():
    moving_right = _platform(row=6, left=3, direction=1)          # at tick 2 it covers columns 5, 6 and 7
    assert prism._bounce({"row": 6, "column": 5}, moving_right, 2) == (4, 2)   # left edge: two left, one along with it
    assert prism._bounce({"row": 6, "column": 6}, moving_right, 2) == (7, 2)   # middle: only the platform's motion
    assert prism._bounce({"row": 6, "column": 7}, moving_right, 2) == (10, 2)  # right edge: two right, one along
    moving_left = _platform(row=6, left=7, direction=-1)
    assert prism._bounce({"row": 6, "column": 6}, moving_left, 2) == (5, 2)
    assert prism._bounce({"row": 2, "column": 5}, _platform(row=2, left=4), 0)[1] == 0   # never above the top
    assert prism._bounce({"row": 6, "column": 1}, _platform(row=6, left=1, direction=-1), 0)[0] == 2   # 1 - 2 + 1 = 0: off the wall to 2


def test_a_falling_ball_bounces_only_where_the_platform_is_at_that_moment():
    path = prism.drop(_falling(4), [])["trajectory"]              # enters at 4 and steps right each row
    assert path[3] == {"row": 3, "column": 7}
    catching = _platform(row=3, left=3, direction=1)              # after three steps it covers columns 6, 7 and 8
    caught = prism.drop(_falling(4), [], [catching])["trajectory"]
    assert caught[3]["hit"] == "platform" and caught[3]["platform"] == 0 and caught[4]["row"] == 0
    missing = _platform(row=3, left=1, direction=1)               # after three steps it covers 4, 5 and 6: the ball is past it
    assert prism.drop(_falling(4), [], [missing])["trajectory"] == path


def test_a_ball_rising_past_a_platform_isnt_caught():
    bomb_up = [{"row": 8, "column": 6, "type": "bomb"}]
    for seed in range(400):
        result = prism.drop(seeded(seed), bomb_up, [_platform(row=6, left=4)])
        for i, point in enumerate(result["trajectory"]):
            if point.get("hit") == "platform":
                assert result["trajectory"][i - 1]["row"] < point["row"]


def test_each_platform_gives_two_bounces_and_every_ball_still_lands():
    for seed in range(1000):
        result = prism.roll(5, seeded(seed))
        hits = [p["platform"] for p in result["trajectory"] if p.get("hit") == "platform"]
        assert hits.count(0) <= prism.PLATFORM_BOUNCES and hits.count(1) <= prism.PLATFORM_BOUNCES
        assert result["trajectory"][-1]["row"] == prism.HEIGHT and len(result["trajectory"]) < 200


def test_platforms_bounce_a_fair_share_of_balls():
    bounced = sum(any(p.get("hit") == "platform" for p in prism.roll(5, seeded(seed))["trajectory"]) for seed in range(1000))
    assert 200 < bounced < 900                                    # common enough to notice, not on every drop
