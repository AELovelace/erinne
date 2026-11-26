using Microsoft.Data.Sqlite;

namespace Mnemerynne.Core.Drive;

/// <summary>One file or folder the drive has put on this computer, and where.</summary>
public sealed record LocalItem(char Kind, long Id, long CollectionId, long? ParentId, string RelPath, string? Sha256, bool Populated)
{
    public DriveId DriveId => new(Kind, Id);
}

/// <summary>
/// The drive's bookkeeping (drive.db in the profile's folder): which server item each placeholder is, where it is
/// (relative to the drive's folder), the content version the app last had, whether a folder has been listed yet,
/// and each library's change-feed cursor. The placeholders also carry their identity themselves, so this can be
/// rebuilt; it's what lets a change from the server find its file without walking the folders.
/// </summary>
public sealed class DriveState : IDisposable
{
    private readonly SqliteConnection _db;
    private readonly Lock _gate = new();          // Explorer's callbacks and the sync loop both write here

    public DriveState(string path)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(path))!);
        _db = new SqliteConnection($"Data Source={path}");
        _db.Open();
        Exec("PRAGMA journal_mode = WAL");
        Exec("""
            CREATE TABLE IF NOT EXISTS items (
                kind          TEXT NOT NULL,              -- c (library), d (folder), f (file)
                id            INTEGER NOT NULL,
                collection_id INTEGER NOT NULL,
                parent_id     INTEGER,                    -- the folder it's in (NULL: the library's top)
                rel_path      TEXT NOT NULL,              -- e.g. "File box\Reports\q1.pdf"
                sha256        TEXT,                       -- files: the version the app last had
                populated     INTEGER NOT NULL DEFAULT 0, -- folders: listed already (children are placeholders)
                PRIMARY KEY (kind, id)
            )
            """);
        Exec("CREATE UNIQUE INDEX IF NOT EXISTS items_path ON items(rel_path COLLATE NOCASE)");
        Exec("CREATE TABLE IF NOT EXISTS cursors (collection_id INTEGER PRIMARY KEY, cursor INTEGER NOT NULL)");
    }

    private void Exec(string sql, params (string, object?)[] args)
    {
        using var cmd = _db.CreateCommand();
        cmd.CommandText = sql;
        foreach (var (name, value) in args)
            cmd.Parameters.AddWithValue(name, value ?? DBNull.Value);
        cmd.ExecuteNonQuery();
    }

    private List<LocalItem> Query(string sql, params (string, object?)[] args)
    {
        using var cmd = _db.CreateCommand();
        cmd.CommandText = sql;
        foreach (var (name, value) in args)
            cmd.Parameters.AddWithValue(name, value ?? DBNull.Value);
        using var r = cmd.ExecuteReader();
        var found = new List<LocalItem>();
        while (r.Read())
            found.Add(new LocalItem(r.GetString(0)[0], r.GetInt64(1), r.GetInt64(2), r.IsDBNull(3) ? null : r.GetInt64(3),
                                    r.GetString(4), r.IsDBNull(5) ? null : r.GetString(5), r.GetInt64(6) != 0));
        return found;
    }

    private const string Columns = "kind, id, collection_id, parent_id, rel_path, sha256, populated";

    /// <summary>Add or update one item (by kind and id). Anything else at its path is dropped first.</summary>
    public void Put(LocalItem item)
    {
        lock (_gate)
        {
            Exec("DELETE FROM items WHERE rel_path = $p COLLATE NOCASE AND NOT (kind = $k AND id = $i)",
                 ("$p", item.RelPath), ("$k", item.Kind.ToString()), ("$i", item.Id));
            Exec($"INSERT INTO items({Columns}) VALUES ($k, $i, $c, $parent, $p, $s, $pop) " +
                 "ON CONFLICT(kind, id) DO UPDATE SET collection_id = $c, parent_id = $parent, rel_path = $p, " +
                 "sha256 = $s, populated = $pop",
                 ("$k", item.Kind.ToString()), ("$i", item.Id), ("$c", item.CollectionId), ("$parent", item.ParentId),
                 ("$p", item.RelPath), ("$s", item.Sha256), ("$pop", item.Populated ? 1 : 0));
        }
    }

    public LocalItem? Find(char kind, long id)
    {
        lock (_gate)
            return Query($"SELECT {Columns} FROM items WHERE kind = $k AND id = $i", ("$k", kind.ToString()), ("$i", id))
                .FirstOrDefault();
    }

    public LocalItem? FindPath(string relPath)
    {
        lock (_gate)
            return Query($"SELECT {Columns} FROM items WHERE rel_path = $p COLLATE NOCASE", ("$p", relPath)).FirstOrDefault();
    }

    /// <summary>The folder a server item sits in: its parent folder, or the library's top when the parent is null.</summary>
    public LocalItem? Container(long collectionId, long? parentId) =>
        parentId is { } p ? Find(DriveId.Folder, p) : Find(DriveId.Library, collectionId);

    /// <summary>
    /// A folder moved or was renamed: its own path and everything under it change. Called after the move on disk.
    /// </summary>
    public void MovePath(string from, string to)
    {
        lock (_gate)
        {
            Exec("UPDATE items SET rel_path = $to WHERE rel_path = $from COLLATE NOCASE", ("$to", to), ("$from", from));
            // children: replace the prefix (substr is 1-based in SQLite)
            Exec("UPDATE items SET rel_path = $to || substr(rel_path, length($from) + 1) " +
                 "WHERE rel_path LIKE $like ESCAPE '^'",
                 ("$to", to), ("$from", from), ("$like", Like(from) + "\\%"));
        }
    }

    /// <summary>Forget an item, and everything under it when it's a folder.</summary>
    public void RemoveTree(string relPath)
    {
        lock (_gate)
        {
            Exec("DELETE FROM items WHERE rel_path = $p COLLATE NOCASE", ("$p", relPath));
            Exec("DELETE FROM items WHERE rel_path LIKE $like ESCAPE '^'", ("$like", Like(relPath) + "\\%"));
        }
    }

    private static string Like(string text) => text.Replace("^", "^^").Replace("%", "^%").Replace("_", "^_");

    public void SetSha(char kind, long id, string sha)
    {
        lock (_gate)
            Exec("UPDATE items SET sha256 = $s WHERE kind = $k AND id = $i", ("$s", sha), ("$k", kind.ToString()), ("$i", id));
    }

    public void SetPopulated(string relPath)
    {
        lock (_gate)
            Exec("UPDATE items SET populated = 1 WHERE rel_path = $p COLLATE NOCASE", ("$p", relPath));
    }

    public List<LocalItem> Libraries()
    {
        lock (_gate)
            return Query($"SELECT {Columns} FROM items WHERE kind = 'c' ORDER BY rel_path");
    }

    public long Cursor(long collectionId)
    {
        lock (_gate)
        {
            using var cmd = _db.CreateCommand();
            cmd.CommandText = "SELECT cursor FROM cursors WHERE collection_id = $c";
            cmd.Parameters.AddWithValue("$c", collectionId);
            return cmd.ExecuteScalar() is long v ? v : 0;
        }
    }

    public void SetCursor(long collectionId, long cursor)
    {
        lock (_gate)
            Exec("INSERT INTO cursors(collection_id, cursor) VALUES ($c, $v) ON CONFLICT(collection_id) DO UPDATE SET cursor = $v",
                 ("$c", collectionId), ("$v", cursor));
    }

    /// <summary>A library taken off the drive: its items and cursor go.</summary>
    public void DropLibrary(long collectionId)
    {
        lock (_gate)
        {
            Exec("DELETE FROM items WHERE collection_id = $c", ("$c", collectionId));
            Exec("DELETE FROM cursors WHERE collection_id = $c", ("$c", collectionId));
        }
    }

    public void Dispose() => _db.Dispose();
}
