"""Which documents the teacher model may see.

A local teacher (llama-server on this machine) may read everything. A remote
teacher (e.g. the OpenAI API) only receives document text from libraries an
admin explicitly allowed, so confidential material (e.g. BWH documents marked
"not for release to any third party") never leaves the machine by accident.
"""

from __future__ import annotations

import sqlite3

from mnemerynne.config import Settings


class TeacherScopeError(ValueError):
    pass


def allowed_collections(conn: sqlite3.Connection, settings: Settings, teacher) -> set[int]:
    """Collection ids whose text this teacher may receive."""
    if not getattr(teacher, "is_remote", False):
        return {r[0] for r in conn.execute("SELECT id FROM collections")}
    names = [n.strip() for n in settings.teacher_allowed_libraries.split(",") if n.strip()]
    if names == ["*"]:
        # Explicit opt-in to send everything (e.g. test documents with no confidentiality needs).
        return {r[0] for r in conn.execute("SELECT id FROM collections")}
    if not names:
        raise TeacherScopeError(
            "A remote teacher needs MNEMERYNNE_TEACHER_ALLOWED_LIBRARIES (library names whose text may be "
            "sent to it, e.g. Oracle). Nothing is sent by default.")
    ids = set()
    for name in names:
        row = conn.execute("SELECT id FROM collections WHERE kind = 'library' AND name = ? COLLATE NOCASE",
                           (name,)).fetchone()
        if row is None:
            raise TeacherScopeError(f"MNEMERYNNE_TEACHER_ALLOWED_LIBRARIES names unknown library '{name}'.")
        ids.add(row[0])
    return ids
