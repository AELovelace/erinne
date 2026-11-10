"""The dungeon's words (mnemerynne/lumitales.py): the library, the Claude generator and live mode. No test here
talks to a real model: Claude is a stand-in that records what it was asked and answers as scripted."""

import asyncio
import json
import random
from types import SimpleNamespace

import pytest

from mnemerynne import claude_chat, lumidungeon as D, lumipet, lumitales as T, users
from mnemerynne.db import transaction
from test_media import (_login, _no_network, _pair, _screens_for, _turn_on, checker, client, conn,  # noqa: F401
                        directory, root, settings)

T0 = 1_800_000_000_000


class FakeClaude:
    """Stands in for AsyncAnthropic: `.messages.create` and `.beta.messages.create` record the request and return
    what `reply(request)` says (a string of text, or a ready-made response)."""

    def __init__(self, reply):
        self.asked, self._reply = [], reply
        self.messages = self.beta = SimpleNamespace(create=self._create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _create(self, **request):
        self.asked.append(request)
        out = self._reply(request)
        if isinstance(out, Exception):
            raise out
        return out if not isinstance(out, str) else SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="thinking", thinking=""),
                                                                                                    SimpleNamespace(type="text", text=out)])


def _refusal(_request):
    return SimpleNamespace(stop_reason="refusal", content=[])


def _moment(scenario="treasure", env="toy-attic", **event):
    return {"id": 5, "env": env, "depth": 2, "at": "1,2", "event": {"scenario": scenario, "n": 1, **event}}


@pytest.fixture
def alice(conn):
    return users.create_user(conn, "alice", "password123")


# --------------------------------------------------------------------------- the check every line passes
@pytest.mark.parametrize("text, scenario, ok", [
    ("Lumi finds {item} under a cushion.", "treasure", True),
    ("  Lumi   pads into\n{place}.  ", "enter", True),                        # tidied to one line
    ("A wild {enemy} appears!", "enemy", True),
    ("A wild {enemy} appears!", "empty", False),                                # this scenario has no enemy to name
    ("Lumi finds {item}.", "key", False),
    ("Lumi finds {coins} coins.", "treasure", False),                           # not a placeholder the game fills
    ("Lumi finds {item.", "treasure", False),
    ("Lumi finds item}.", "treasure", False),
    ("<b>Lumi</b> wins.", "victory", False),
    ("See https://example.test for more.", "empty", False),
    ("`rm -rf` says the door.", "empty", False),
    ("x" * 201, "empty", False),
    ("Hi", "empty", False),
    (None, "empty", False), (["a line"], "empty", False), (7, "empty", False)])
def test_a_line_is_plain_short_and_uses_only_its_scenarios_placeholders(text, scenario, ok):
    out = T.clean(text, scenario)
    assert (out is not None) is ok
    if ok:
        assert "\n" not in out and "  " not in out and out == out.strip()


def test_a_live_line_may_have_no_placeholders_left():
    assert T.clean("Lumi finds 12 coins.", filled=True) == "Lumi finds 12 coins."
    assert T.clean("Lumi finds {item}.", filled=True) is None and T.clean("Welcome to {place}.", filled=True) is None


# --------------------------------------------------------------------------- the library
def test_the_shipped_library_covers_every_scenario_and_every_environment_has_its_own_voice():
    assert set(T.LIBRARY["default"]) == set(T.SCENARIOS) and all(len(lines) >= 3 for lines in T.LIBRARY["default"].values())
    assert set(T.LIBRARY) - {"default"} == set(D._ENVS)
    assert all(T.LIBRARY[env]["enter"] and T.LIBRARY[env]["empty"] for env in D._ENVS)


def test_bad_libraries_are_refused(tmp_path):
    good = json.dumps(T.LIBRARY["default"])

    def broken(name, text, also_default=True):
        for old in tmp_path.glob("*.json"):
            old.unlink()
        if also_default:
            (tmp_path / "default.json").write_text(good, encoding="utf-8")
        (tmp_path / name).write_text(text, encoding="utf-8")
        with pytest.raises(ValueError):
            T.load_library(tmp_path)

    broken("the-moon.json", '{"empty": ["A fine empty room."]}')                # not an environment
    broken("toy-attic.json", '{"dancing": ["Lumi dances."]}')                   # not a scenario
    broken("toy-attic.json", '{"empty": ["A {enemy} is here."]}')
    broken("toy-attic.json", '{"empty": ["<i>spooky</i>"]}')
    broken("toy-attic.json", '{"empty": "one line, not a list"}')
    broken("toy-attic.json", '["a list, not scenarios"]')
    short = dict(T.LIBRARY["default"], potion=[])
    broken("default.json", json.dumps(short), also_default=False)               # a scenario with nothing to say
    (tmp_path / "default.json").write_text(good, encoding="utf-8")
    (tmp_path / "toy-attic.json").write_text('{"empty": ["Dust and quiet."]}', encoding="utf-8")
    assert T.load_library(tmp_path)["toy-attic"] == {"empty": ["Dust and quiet."]}


def test_a_line_fills_in_names_prefers_the_environments_own_and_always_says_what_was_found():
    rng = random.Random(2)
    own = T.line(_moment("empty"), rng)
    assert own in T.LIBRARY["toy-attic"]["empty"]                               # the attic's own empty-room lines come first
    fight = T.line(_moment("enemy", enemy="Gloom Bat"), rng)
    assert "Gloom Bat" in fight and "{" not in fight
    found = T.line(_moment("vault", loot={"coins": 90, "potions": 1, "trophy": "trophy-ghost-teddy", "mod": "glitch-fang"}), rng)
    assert "90 coins, a potion, the Ghost teddy and a Glitch Fang mod" in found
    assert "Haunted Toy Attic" in T.line(_moment("home"), random.Random(0)) or "Home!" in T.line(_moment("home"), random.Random(0))
    assert T.line(_moment("runes_try", exact=2, near=1)) == "2 in the right place (lit green), 1 right but in the wrong place (ringed gold)."
    for scenario in T.SCENARIOS:                                                # every scenario reads cleanly in every environment
        for env in D._ENVS:
            told = T.line(_moment(scenario, env, enemy="Dust Bunny", loot={"coins": 5}), rng)
            assert told and "{" not in told and "}" not in told


# --------------------------------------------------------------------------- the generator
def _written(extra=None):
    def reply(request):
        lines = {name: [f"Lumi does the {name.replace('_', ' ')} thing, take {n}." for n in (1, 2)] for name in T.SCENARIOS}
        lines.update(extra or {})
        return json.dumps(lines)
    return reply


def test_the_generator_asks_once_per_environment_and_keeps_only_good_lines(tmp_path):
    claude = FakeClaude(_written({"empty": ["A quiet room.", "A wild {enemy} is here!", "<b>bold</b> room", "x" * 300, "A quiet room."],
                                  "treasure": ["Lumi finds {item} in a toy box."]}))
    gained = asyncio.run(T.generate(claude, ["toy-attic", "data-vault"], 2, tmp_path, "claude-opus-5-5"))
    assert len(claude.asked) == 2 and set(gained) == {"toy-attic", "data-vault"}
    asked = claude.asked[0]
    assert asked["model"] == "claude-opus-5-5" and asked["system"] == T.VOICE and asked["max_tokens"] == 16000
    assert asked["betas"] == ["server-side-fallback-2026-07-01"] and asked["fallbacks"] == "default"       # a decline is retried by the API
    schema = asked["output_config"]["format"]["schema"]
    assert asked["output_config"]["format"]["type"] == "json_schema" and schema["required"] == list(T.SCENARIOS) and schema["additionalProperties"] is False
    brief = asked["messages"][0]["content"]
    assert "Haunted Toy Attic" in brief or "Data Vault" in brief
    assert all(name in brief for name in T.SCENARIOS) and "{enemy}" in brief and "thinking" not in asked and "temperature" not in asked
    attic = json.loads((tmp_path / "toy-attic.json").read_text(encoding="utf-8"))
    assert attic["empty"] == ["A quiet room."] and attic["treasure"] == ["Lumi finds {item} in a toy box."]
    assert list(attic) == [name for name in T.SCENARIOS if name in attic]
    (tmp_path / "default.json").write_text(json.dumps(T.LIBRARY["default"]), encoding="utf-8")
    assert "toy-attic" in T.load_library(tmp_path)                             # what it wrote is a valid library file


def test_running_the_generator_again_adds_without_repeating_or_losing_edits(tmp_path):
    (tmp_path / "toy-attic.json").write_text(json.dumps({"empty": ["A line someone wrote by hand."]}), encoding="utf-8")
    claude = FakeClaude(_written({"empty": ["A quiet room.", "A line someone wrote by hand."]}))
    first = asyncio.run(T.generate(claude, ["toy-attic"], 2, tmp_path, "claude-haiku-5-5"))
    again = asyncio.run(T.generate(claude, ["toy-attic"], 2, tmp_path, "claude-haiku-5-5"))
    attic = json.loads((tmp_path / "toy-attic.json").read_text(encoding="utf-8"))
    assert attic["empty"] == ["A line someone wrote by hand.", "A quiet room."] and first["toy-attic"] > 0 and again["toy-attic"] == 0
    assert "fallbacks" not in claude.asked[0], "Haiku has no fallback to name"


def test_a_declined_environment_is_skipped_and_a_cut_off_reply_is_reported(tmp_path):
    gained = asyncio.run(T.generate(FakeClaude(_refusal), ["toy-attic"], 2, tmp_path, "claude-opus-5-5"))
    assert gained == {"toy-attic": 0} and json.loads((tmp_path / "toy-attic.json").read_text(encoding="utf-8")) == {}
    cut = FakeClaude(lambda request: SimpleNamespace(stop_reason="max_tokens", content=[SimpleNamespace(type="text", text='{"empty": ["A qui')]))
    with pytest.raises(T.TaleError, match="cut short"):
        asyncio.run(T.generate(cut, ["toy-attic"], 2, tmp_path, "claude-opus-5-5"))


def test_the_command_line_knows_the_generator():
    from mnemerynne import cli

    args = cli.build_parser().parse_args(["lumi-tales", "--env", "toy-attic", "--lines", "4"])
    assert (args.func.__name__, args.env, args.lines, args.model, args.out) == ("cmd_lumi_tales", "toy-attic", 4, "claude-opus-5-5", "mnemerynne/lumipet_data/tales")
    args.env = "the-moon"
    with pytest.raises(ValueError, match="No such environment"):
        args.func(args)


# --------------------------------------------------------------------------- live mode
def _live(reply, **moment):
    T.TRANSPORT = claude = FakeClaude(reply)
    try:
        return asyncio.run(T.live_line(_moment(**moment), claude=("sk-ant-test", "claude-sonnet-5-5"))), claude
    finally:
        T.TRANSPORT = None


def test_a_live_line_is_asked_for_with_only_the_games_facts():
    told, claude = _live(lambda r: "Lumi tips over a toy chest and out rolls a potion, still fizzing.", scenario="treasure", loot={"potions": 1})
    assert told == "Lumi tips over a toy chest and out rolls a potion, still fizzing."
    asked = claude.asked[0]
    assert asked["model"] == "claude-sonnet-5-5" and asked["system"] == T.VOICE and asked["output_config"] == {"effort": "low"}
    prompt = asked["messages"][0]["content"]
    assert "Haunted Toy Attic" in prompt and "Floor: 2" in prompt and "Lumi finds a potion lying in the room." in prompt
    assert "alice" not in json.dumps(asked) and "sk-ant" not in json.dumps(asked)


@pytest.mark.parametrize("reply", [
    "<script>alert(1)</script>", "Visit http://evil.example now", "x" * 400, "Lumi finds {item}.", "", "ok",
    _refusal, lambda r: RuntimeError("the network is down"), lambda r: SimpleNamespace(stop_reason="end_turn", content=[])])
def test_a_live_line_that_isnt_fit_to_show_is_dropped(reply):
    told, _ = _live(reply if callable(reply) else (lambda r, text=reply: text))
    assert told is None


def test_puzzle_reports_are_never_sent_to_a_model_and_no_storyteller_means_no_line():
    told, claude = _live(lambda r: "Lumi tries some runes.", scenario="runes_try", exact=1, near=1)
    assert told is None and claude.asked == []
    assert asyncio.run(T.live_line(_moment())) is None


def test_bfgguf_can_tell_the_story_too():
    class Local:
        def __init__(self):
            self.asked = []

        async def chat(self, messages, **kwargs):
            self.asked.append((messages, kwargs))
            return '"Lumi finds a marble that rolls uphill."'

    local = Local()
    assert asyncio.run(T.live_line(_moment("empty"), bfgguf=local)) == "Lumi finds a marble that rolls uphill."
    messages, kwargs = local.asked[0]
    assert messages[0] == {"role": "system", "content": T.VOICE} and kwargs["max_tokens"] == 120


# --------------------------------------------------------------------------- live mode through the page
@pytest.fixture
def on(client, conn, directory):
    _login(client, conn, directory)
    _turn_on(client)
    return client


def _uid(conn):
    return conn.execute("SELECT id FROM users WHERE username = 'boss'").fetchone()[0]


def test_the_storyteller_setting_flips_and_live_needs_someone_to_tell_it(on, conn, settings):
    assert on.get("/api/media/games/lumi-pet/state").json()["pet"]["storyteller"] == "library"
    assert on.post("/api/media/games/lumi-pet/act", json={"action": "storyteller"}).json()["pet"]["storyteller"] == "live"
    assert on.get("/api/media/games/lumi-pet/dungeon/tale").json() == {"live": None}
    assert on.post("/api/media/games/lumi-pet/dungeon/tale").json() == {"line": None}           # no trip, and nobody to tell it
    claude_chat.save_key(conn, settings, _uid(conn), "sk-ant-api03-" + "a" * 40, "claude-haiku-5-5")
    assert on.get("/api/media/games/lumi-pet/dungeon/tale").json() == {"live": "claude"}


def test_a_live_line_replaces_the_librarys_once_per_moment(on, conn, settings):
    claude_chat.save_key(conn, settings, _uid(conn), "sk-ant-api03-" + "a" * 40, "claude-haiku-5-5")
    library = on.post("/api/media/games/lumi-pet/dungeon", json={"env": "crystal-cavern"}).json()["dungeon"]["run"]["say"]
    T.TRANSPORT = claude = FakeClaude(lambda r: "Lumi steps into the cavern and every crystal says hello at once.")
    try:
        first = on.post("/api/media/games/lumi-pet/dungeon/tale").json()
        again = on.post("/api/media/games/lumi-pet/dungeon/tale").json()
    finally:
        T.TRANSPORT = None
    assert first == {"line": "Lumi steps into the cavern and every crystal says hello at once."} and again == {"line": None}
    assert len(claude.asked) == 1 and claude.asked[0]["model"] == "claude-haiku-5-5"
    kept = on.get("/api/media/games/lumi-pet/state").json()["dungeon"]["run"]["say"]
    assert kept == first["line"] != library                                     # a reload reads the live line, without asking again
    home = on.post("/api/media/games/lumi-pet/dungeon/act", json={"action": "leave"}).json()["dungeon"]["run"]
    assert home["say"] != first["line"] and home["event"]["scenario"] == "home"  # a new moment starts from the library again


def test_live_lines_are_rate_limited_per_person(on, conn, settings):
    from mnemerynne.web import games as web_games

    claude_chat.save_key(conn, settings, _uid(conn), "sk-ant-api03-" + "a" * 40, "claude-haiku-5-5")
    on.post("/api/media/games/lumi-pet/dungeon", json={"env": "crystal-cavern"})
    web_games._tale_times.clear()
    T.TRANSPORT = claude = FakeClaude(lambda r: "<nope>")                       # never fit to show, so every ask is a real ask
    try:
        for _ in range(web_games.TALES_A_MINUTE + 5):
            assert on.post("/api/media/games/lumi-pet/dungeon/tale").json() == {"line": None}
    finally:
        T.TRANSPORT = None
        web_games._tale_times.clear()
    assert len(claude.asked) == web_games.TALES_A_MINUTE


def test_a_room_keeps_its_own_detail_and_each_look_notices_another():
    here, there = _moment("look"), {**_moment("look"), "at": "3,0"}
    looks = T.LIBRARY["toy-attic"]["look"]
    assert T.scene(here) == T.scene(here) and T.scene(here) in looks            # the same room is always the same place
    assert len({T.scene({**here, "at": f"{x},{y}"}) for x in range(5) for y in range(4)}) > 3      # and rooms differ
    first = T.line(here)
    second = T.line({**here, "event": {"scenario": "look", "n": 2}})
    assert first in looks and second in looks and len({T.scene(here), first, second}) == 3         # a new detail each look
    assert T.scene(there) in looks
    assert all(len(T.LIBRARY[env]["look"]) >= 6 for env in D._ENVS)            # every environment has things to notice
