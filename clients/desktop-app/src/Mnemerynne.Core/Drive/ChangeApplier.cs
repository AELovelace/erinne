namespace Mnemerynne.Core.Drive;

/// <summary>
/// What the drive does to the files on this computer. The Windows version (Mnemerynne.Drive) makes Cloud Files
/// placeholders; the tests use a fake. Paths are relative to the drive's folder ("File box\Reports\q1.pdf").
/// </summary>
public interface IPlaceholders
{
    bool Exists(string rel);
    /// <summary>A file or folder placeholder (not downloaded) for a server item, inside <paramref name="parentRel"/>.</summary>
    void Create(string parentRel, string name, RemoteItem item, DriveId id);
    /// <summary>Move or rename an existing placeholder (never over anything).</summary>
    void Move(string fromRel, string toRel);
    /// <summary>The server has new content: new size and time, and the old local bytes dropped (fetched again on open).</summary>
    void Refresh(string rel, RemoteItem item);
    /// <summary>True when the local file has changes the server hasn't got yet (Windows: "not in sync").</summary>
    bool HasLocalChanges(string rel);
    /// <summary>Delete from this computer only (the server already did); a folder with everything in it.</summary>
    void Delete(string rel);
    /// <summary>Rename a local file out of the way and stop tracking it as the server's (it will go up as a new file).</summary>
    void Detach(string fromRel, string toRel);
}

/// <summary>
/// Applies the server's change feed (drive.py changes) to this computer. Each entry is an item's state now, so
/// applying one twice changes nothing. The rules:
/// - only places the drive has listed already are kept up to date; anything else arrives when Explorer opens it;
/// - local work is never thrown away: a file changed here and on the server keeps both (the local one is renamed
///   "… (PC conflict date)" and uploaded as a new file);
/// - names Windows can't hold are made safe (DriveNames.Safe).
/// </summary>
public sealed class ChangeApplier(DriveState state, IPlaceholders disk, Func<DateTime>? clock = null)
{
    private readonly Func<DateTime> _now = clock ?? (() => DateTime.Now);

    /// <summary>What happened, for the log and the tests.</summary>
    public List<string> Log { get; } = [];

    public void Apply(long collectionId, IEnumerable<RemoteItem> changes)
    {
        foreach (var item in changes)
        {
            try
            {
                ApplyOne(collectionId, item);
            }
            catch (IOException e)
            {
                Log.Add($"skipped {item.Type} {item.Id}: {e.Message}");   // a locked file: the next sync tries again
            }
        }
    }

    private void ApplyOne(long collectionId, RemoteItem item)
    {
        var kind = item.IsFolder ? DriveId.Folder : DriveId.File;
        var known = state.Find(kind, item.Id);

        if (item.Deleted)
        {
            if (known is not null)
                Remove(known);
            return;
        }

        var container = state.Container(collectionId, item.Parent);
        if (container is null || !container.Populated)
        {
            // It's (now) somewhere the drive hasn't listed: it shows up when Explorer opens that folder.
            if (known is not null)
                Remove(known);
            return;
        }

        var name = DriveNames.Safe(item.Name);
        var target = DriveNames.Join(container.RelPath, name);

        if (known is null)
        {
            if (disk.Exists(target))
            {
                // Something local already has that name: keep it, under another name, and put the server's one here.
                var aside = DriveNames.Join(container.RelPath, DriveNames.Conflict(name, _now(), n => disk.Exists(DriveNames.Join(container.RelPath, n))));
                disk.Detach(target, aside);
                state.RemoveTree(target);
                Log.Add($"kept local {target} as {aside}");
            }
            disk.Create(container.RelPath, name, item, new DriveId(kind, item.Id));
            state.Put(new LocalItem(kind, item.Id, collectionId, item.Parent, target, item.Sha256, Populated: false));
            Log.Add($"added {target}");
            return;
        }

        if (!known.RelPath.Equals(target, StringComparison.Ordinal))   // Ordinal: a change of case is a rename too
        {
            disk.Move(known.RelPath, target);                      // renamed or moved on the server
            if (item.IsFolder)
                state.MovePath(known.RelPath, target);             // and everything under it
            Log.Add($"moved {known.RelPath} -> {target}");
        }

        if (!item.IsFolder && item.Sha256 is not null && item.Sha256 != known.Sha256)
        {
            if (disk.HasLocalChanges(target))
            {
                // Changed in both places: the local copy steps aside (and goes up as a new file); the server's lands.
                var aside = DriveNames.Join(container.RelPath, DriveNames.Conflict(name, _now(), n => disk.Exists(DriveNames.Join(container.RelPath, n))));
                disk.Detach(target, aside);
                disk.Create(container.RelPath, name, item, new DriveId(kind, item.Id));
                Log.Add($"conflict: kept local {target} as {aside}");
            }
            else
            {
                disk.Refresh(target, item);                        // fetched again next time something opens it
                Log.Add($"updated {target}");
            }
        }
        state.Put(known with { CollectionId = collectionId, ParentId = item.Parent, RelPath = target, Sha256 = item.Sha256 ?? known.Sha256 });
    }

    private void Remove(LocalItem known)
    {
        if (known.Kind == DriveId.File && disk.Exists(known.RelPath) && disk.HasLocalChanges(known.RelPath))
        {
            // Gone from the server, but changed here: keep the local work as a new file instead of deleting it.
            var parent = DriveNames.ParentOf(known.RelPath);
            var aside = DriveNames.Join(parent, DriveNames.Conflict(DriveNames.NameOf(known.RelPath), _now(),
                                                                    n => disk.Exists(DriveNames.Join(parent, n))));
            disk.Detach(known.RelPath, aside);
            Log.Add($"kept local {known.RelPath} as {aside} (gone from the server)");
        }
        else if (disk.Exists(known.RelPath))
        {
            disk.Delete(known.RelPath);
            Log.Add($"removed {known.RelPath}");
        }
        state.RemoveTree(known.RelPath);
    }
}
