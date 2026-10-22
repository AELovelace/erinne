"""Questions run as background jobs: the answer survives the page going away."""

import json

from test_chat import _final, _login, _wait_for, chat_client, llm  # noqa: F401  (fixtures)


def _events(response):
    return [json.loads(line[5:]) for line in response.iter_lines() if line.startswith("data:")]


def test_events_can_be_picked_up_again(chat_client):
    _login(chat_client, "alice")
    with chat_client.stream("POST", "/api/ask", json={"question": "How do I run the night audit?"}) as r:
        events = _events(r)
    run_id = events[0]["run_id"]
    with chat_client.stream("GET", f"/api/runs/{run_id}/events?after=1") as r:
        again = _events(r)
    assert again == events[1:] and _final(again)["citations"] == [1]
    conversation = chat_client.get(f"/api/conversations/{events[0]['conversation_id']}").json()
    assert conversation["running"] is None

    chat_client.post("/logout")
    _login(chat_client, "bob")
    assert chat_client.get(f"/api/runs/{run_id}/events").status_code == 404     # not his


def test_the_answer_is_saved_when_the_page_stops_listening(chat_client, conn):
    _login(chat_client, "alice")
    with chat_client.stream("POST", "/api/ask", json={"question": "How do I run the night audit?"}) as r:
        first = next(line for line in r.iter_lines() if line.startswith("data:"))
    cid = json.loads(first[5:])["conversation_id"]
    assert _wait_for(lambda: conn.execute("SELECT count(*) FROM messages WHERE conversation_id = ? AND "
                                          "role = 'assistant'", (cid,)).fetchone()[0] == 1)
    answer = chat_client.get(f"/api/conversations/{cid}").json()["messages"][-1]
    assert answer["status"] == "answered" and "[1]" in answer["content"]
