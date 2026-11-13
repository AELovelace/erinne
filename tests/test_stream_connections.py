"""Live streams must not hold a database connection for as long as they stay open (deps.release_db).

Every open connection costs three open files (the database, -wal, -shm). Rooms, party games, TV screens and
radio keep their streams open for hours, so when each one pinned its request's connection the web app ran
out of open files: "sqlite3.OperationalError: unable to open database file", and nginx answered 502."""

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from mnemerynne import db
from mnemerynne.web.deps import get_db, release_db


def _app(tmp_path, release: bool):
    """A tiny app shaped like Media's: a router-wide guard and the route both use get_db, then stream."""
    def guard(conn=Depends(get_db)):                       # like _media_on / _rooms_on: shares the request's conn
        conn.execute("SELECT 1")

    router = APIRouter(dependencies=[Depends(guard)])
    seen: dict[str, int] = {}                              # what the stream saw while it was running

    @router.get("/stream")
    def stream(request: Request, conn=Depends(get_db)):
        conn.execute("SELECT 1")                           # the route's own last use, before streaming
        pool = request.app.state.db_pool

        def events():
            seen["idle"] = len(pool._idle)                 # spare connections while the stream is open
            yield b"data: hi\n\n"

        if release:
            release_db(request)                            # the fix: give the connection back now
        return StreamingResponse(events(), media_type="text/event-stream")

    app = FastAPI()
    app.state.db_pool = db.Pool(tmp_path / "x.db")
    app.include_router(router)
    return app, seen


def test_a_stream_that_releases_holds_no_connection_while_open(tmp_path):
    app, seen = _app(tmp_path, release=True)
    assert TestClient(app).get("/stream").status_code == 200
    assert seen["idle"] == 1                               # the one connection (guard + route shared it) is spare again
    assert len(app.state.db_pool._idle) == 1               # and the request's own cleanup didn't put it back twice


def test_without_release_the_stream_pins_its_connection(tmp_path):
    app, seen = _app(tmp_path, release=False)              # what every stream did before: proves the test can tell
    assert TestClient(app).get("/stream").status_code == 200
    assert seen["idle"] == 0                               # still checked out for the whole stream
    assert len(app.state.db_pool._idle) == 1               # back only once the response finished


def test_a_normal_request_still_returns_its_connection_once(tmp_path):
    app = FastAPI()
    app.state.db_pool = db.Pool(tmp_path / "x.db")

    @app.get("/plain")
    def plain(conn=Depends(get_db)):
        return {"one": conn.execute("SELECT 1").fetchone()[0]}

    client = TestClient(app)
    for _ in range(3):                                     # reused each time, never duplicated in the pool
        assert client.get("/plain").json() == {"one": 1}
    assert len(app.state.db_pool._idle) == 1
