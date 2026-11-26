using System.Collections.Concurrent;
using System.Net;
using System.Runtime.InteropServices;
using Mnemerynne.Core;
using Mnemerynne.Core.Drive;

namespace Mnemerynne.Drive;

/// <summary>
/// One profile's drive, running: the folder registered with Windows, Explorer's requests answered (list a folder,
/// download a file, allow a delete or rename after the server did it), local edits and new files sent up, and the
/// server's changes brought down once a minute.
///
/// The app's own changes to the folder (applying the server's changes, taking a library off) come back through the
/// same callbacks; they're recognised by the process id and allowed without asking the server (FromSelf).
/// </summary>
public sealed class DriveService : IAsyncDisposable
{
    private readonly ServerApi _api;
    private readonly DriveState _state;
    private readonly Placeholders _disk;
    private readonly ChangeApplier _applier;
    private readonly Action<string> _log;
    private readonly string _profileId;
    private readonly CancellationTokenSource _stop = new();
    private readonly int _self = Environment.ProcessId;

    // Kept alive for as long as Windows may call them (the GC must never move or free these).
    private readonly Cf.Callback _onFetchData, _onFetchPlaceholders, _onDelete, _onRename, _onCancel;
    private GCHandle _table;
    private long _connection;

    private FileSystemWatcher? _watcher;
    private readonly ConcurrentDictionary<string, DateTime> _dirty = new(StringComparer.OrdinalIgnoreCase);   // path -> seen at
    private readonly SemaphoreSlim _uploading = new(1, 1);   // one local change at a time, in order
    private Task? _loop;

    public string Folder => _disk.Root;
    public IReadOnlyList<long> Libraries { get; private set; } = [];
    public bool Paused { get; set; }
    public string Status { get; private set; } = "Starting";
    public event Action<string>? StatusChanged;

    public unsafe DriveService(ServerApi api, string folder, string stateFile, string profileId, Action<string> log)
    {
        _api = api;
        _profileId = profileId;
        _log = log;
        _disk = new Placeholders(folder);
        _state = new DriveState(stateFile);
        _applier = new ChangeApplier(_state, _disk);
        _onFetchData = OnFetchData;
        _onFetchPlaceholders = OnFetchPlaceholders;
        _onDelete = OnDelete;
        _onRename = OnRename;
        _onCancel = (_, _) => { };                           // a cancelled download just stops being useful
    }

    private void SetStatus(string text)
    {
        Status = text;
        StatusChanged?.Invoke(text);
    }

    // ===================================================================================== start / stop
    /// <summary>Register the folder, connect the callbacks, put the picked libraries in it, start watching.</summary>
    public async Task StartAsync(string displayName, string iconPath, Uri server, IEnumerable<long> libraries)
    {
        _log("drive: " + await SyncRoot.RegisterAsync(Folder, _profileId, displayName, iconPath, AppVersion.Current,
                                                       new Uri(server, "/files")));
        var table = new Cf.CallbackRegistration[]
        {
            new() { Type = Cf.CallbackType.FetchData, Callback = Marshal.GetFunctionPointerForDelegate(_onFetchData) },
            new() { Type = Cf.CallbackType.CancelFetchData, Callback = Marshal.GetFunctionPointerForDelegate(_onCancel) },
            new() { Type = Cf.CallbackType.FetchPlaceholders, Callback = Marshal.GetFunctionPointerForDelegate(_onFetchPlaceholders) },
            new() { Type = Cf.CallbackType.NotifyDelete, Callback = Marshal.GetFunctionPointerForDelegate(_onDelete) },
            new() { Type = Cf.CallbackType.NotifyRename, Callback = Marshal.GetFunctionPointerForDelegate(_onRename) },
            new() { Type = Cf.CallbackType.None, Callback = 0 },
        };
        _table = GCHandle.Alloc(table, GCHandleType.Pinned);    // Windows keeps a pointer to it while connected
        Connect();
        await SetLibrariesAsync(libraries);
        _watcher = new FileSystemWatcher(Folder)
        {
            IncludeSubdirectories = true,
            NotifyFilter = NotifyFilters.FileName | NotifyFilters.DirectoryName | NotifyFilters.LastWrite | NotifyFilters.Size,
            InternalBufferSize = 64 * 1024,
        };
        _watcher.Created += (_, e) => Touch(e.FullPath);
        _watcher.Changed += (_, e) => Touch(e.FullPath);
        _watcher.Renamed += (_, e) => Touch(e.FullPath);
        _watcher.Error += (_, e) => _log("drive: watcher overflowed, rescanning soon: " + e.GetException().Message);
        _watcher.EnableRaisingEvents = true;
        _loop = Task.Run(() => LoopAsync(_stop.Token));
        SetStatus("Up to date");
    }

    private unsafe void Connect() =>
        Cf.Check(Cf.CfConnectSyncRoot(Folder, (Cf.CallbackRegistration*)_table.AddrOfPinnedObject(), 0,
                                      Cf.ConnectFlagRequireProcessInfo | Cf.ConnectFlagRequireFullFilePath, out _connection),
                 "Connect sync root");

    public async ValueTask DisposeAsync()
    {
        _stop.Cancel();
        _watcher?.Dispose();
        if (_loop is not null)
            await _loop.ContinueWith(_ => { });                  // finish the current step, never throw on the way out
        if (_connection != 0)
            Cf.CfDisconnectSyncRoot(_connection);
        if (_table.IsAllocated)
            _table.Free();
        _state.Dispose();
    }

    /// <summary>Sign-out or uninstall: disconnect and unregister. Files already downloaded stay in the folder.</summary>
    public static void Unregister(string folder, string profileId) => SyncRoot.Unregister(folder, profileId);

    /// <summary>
    /// The libraries ticked in Settings. New ones get a top folder (listed when opened); unticked ones are removed
    /// from this computer only (the app's own delete: the server is never told).
    /// </summary>
    public async Task SetLibrariesAsync(IEnumerable<long> wanted)
    {
        var want = wanted.ToHashSet();
        var all = (await _api.LibrariesAsync(_stop.Token)).Libraries;
        foreach (var gone in _state.Libraries().Where(l => !want.Contains(l.Id)))
        {
            try { _disk.Delete(gone.RelPath); } catch (IOException e) { _log($"drive: couldn't remove {gone.RelPath}: {e.Message}"); }
            _state.DropLibrary(gone.Id);
            _log($"drive: took {gone.RelPath} off this computer");
        }
        foreach (var lib in all.Where(l => want.Contains(l.Id)))
        {
            if (_state.Find(DriveId.Library, lib.Id) is { } known && _disk.Exists(known.RelPath))
                continue;
            var name = DriveNames.Safe(lib.Name);
            if (_disk.Exists(name))
                name = DriveNames.Safe($"{lib.Name} ({lib.Id})");        // two libraries with one name
            var top = new RemoteItem { Type = "folder", Id = lib.Id, Name = name, Modified = null };
            Placeholders.CreateMany(Folder, [(name, top, new DriveId(DriveId.Library, lib.Id))]);
            _state.Put(new LocalItem(DriveId.Library, lib.Id, lib.Id, null, name, null, Populated: false));
            _log($"drive: added {name}");
        }
        Libraries = all.Where(l => want.Contains(l.Id)).Select(l => l.Id).ToList();
    }

    // ===================================================================================== callbacks
    private static unsafe string FullPath(Cf.CallbackInfo* info) =>
        Marshal.PtrToStringUni(info->VolumeDosName) + Marshal.PtrToStringUni(info->NormalizedPath);

    private static unsafe DriveId? IdentityOf(Cf.CallbackInfo* info) =>
        info->FileIdentityLength is > 0 and <= 200
            ? DriveId.Parse(new ReadOnlySpan<byte>((byte*)info->FileIdentity, (int)info->FileIdentityLength))
            : null;

    /// <summary>True when the request came from this app (the drive's own change): allowed without asking the server.</summary>
    private unsafe bool FromSelf(Cf.CallbackInfo* info) =>
        info->ProcessInfo != 0 && *(uint*)((byte*)info->ProcessInfo + Cf.ProcessInfoProcessIdOffset) == _self;

    private string Rel(string full) => Path.GetRelativePath(Folder, full) is var r && r == "." ? "" : r;

    /// <summary>A copy of what a callback needs after it returns (Windows' structs are only valid during the call).</summary>
    private readonly record struct Request(long Connection, long Transfer, long RequestKey, string Path, DriveId? Id, bool Self);

    private unsafe Request Capture(Cf.CallbackInfo* info) =>
        new(info->ConnectionKey, info->TransferKey, info->RequestKey, FullPath(info), IdentityOf(info), FromSelf(info));

    private static unsafe void Execute(Request r, Cf.OperationType type, ref Cf.OperationParameters p)
    {
        var op = new Cf.OperationInfo
        {
            StructSize = (uint)sizeof(Cf.OperationInfo), Type = type, ConnectionKey = r.Connection,
            TransferKey = r.Transfer, RequestKey = r.RequestKey,
        };
        Cf.CfExecute(op, ref p);
    }

    private static int StatusFor(Exception e) => e switch
    {
        ServerError { Status: HttpStatusCode.Forbidden or HttpStatusCode.NotFound } => Cf.StatusCloudFileAccessDenied,
        HttpRequestException or SignInNeeded or TaskCanceledException => Cf.StatusCloudFileNetworkUnavailable,
        _ => Cf.StatusCloudFileUnsuccessful,
    };

    // ----------------------------------------------------------------------- a folder opened: list it
    private unsafe void OnFetchPlaceholders(Cf.CallbackInfo* info, byte* parameters)
    {
        var r = Capture(info);                                        // Windows' structs are gone once this returns
        _ = Task.Run(() => ListFolderAsync(r));
    }

    private async Task ListFolderAsync(Request r)
    {
        {
            var p = new Cf.OperationParameters { ParamSize = Cf.SizeTransferPlaceholders };
            try
            {
                var rel = Rel(r.Path);
                if (rel.Length == 0)
                {
                    // The drive's own top folder: it only holds the libraries, which SetLibrariesAsync makes itself.
                    p.TransferFlags = Cf.TransferPlaceholdersFlagDisableOnDemandPopulation;
                    p.CompletionStatus = Cf.StatusSuccess;
                    Execute(r, Cf.OperationType.TransferPlaceholders, ref p);
                    return;
                }
                var here = _state.FindPath(rel) ?? throw new IOException($"{rel} isn't known to the drive");
                long? folderId = here.Kind == DriveId.Library ? null : here.Id;
                var listing = await _api.ListAsync(here.CollectionId, folderId, _stop.Token);
                var children = new List<(string, RemoteItem, DriveId)>();
                foreach (var item in listing.Folders.Concat(listing.Files))
                {
                    var name = DriveNames.Safe(item.Name);
                    var childRel = DriveNames.Join(rel, name);
                    var kind = item.IsFolder ? DriveId.Folder : DriveId.File;
                    _state.Put(new LocalItem(kind, item.Id, here.CollectionId, item.Parent, childRel, item.Sha256, Populated: false));
                    if (!_disk.Exists(childRel))
                        children.Add((name, item, new DriveId(kind, item.Id)));
                }
                // The placeholders are made directly; the reply then says "that's the whole folder".
                Placeholders.CreateMany(r.Path, children);
                _state.SetPopulated(rel);
                if (here.Kind == DriveId.Library && _state.Cursor(here.CollectionId) == 0)
                    _state.SetCursor(here.CollectionId, listing.Cursor);     // changes after this listing come by the feed
                p.TransferFlags = Cf.TransferPlaceholdersFlagDisableOnDemandPopulation;
                p.CompletionStatus = Cf.StatusSuccess;
                _log($"drive: listed {rel} ({children.Count} new)");
            }
            catch (Exception e)
            {
                p.CompletionStatus = StatusFor(e);
                _log($"drive: couldn't list {r.Path}: {e.Message}");
            }
            Execute(r, Cf.OperationType.TransferPlaceholders, ref p);
        }
    }

    // --------------------------------------------------------------- a file opened: download its bytes
    private unsafe void OnFetchData(Cf.CallbackInfo* info, byte* parameters)
    {
        var r = Capture(info);
        var offset = *(long*)(parameters + 16);                       // RequiredFileOffset (4 KB aligned)
        var length = *(long*)(parameters + 24);                       // RequiredLength
        var size = info->FileSize;
        _ = Task.Run(() => DownloadAsync(r, offset, length, size));
    }

    private async Task DownloadAsync(Request r, long offset, long length, long size)
    {
        {
            if (r.Id is not { IsFile: true } id)
            {
                Fail(r, offset, length, Cf.StatusCloudFileUnsuccessful);
                return;
            }
            // Whole 4 KB pages up to the end of the file: what Windows accepts.
            var end = Math.Min(size, (offset + length + 4095) / 4096 * 4096);
            var pending = new MemoryStream();
            var at = offset;
            try
            {
                SetStatus("Downloading " + Path.GetFileName(r.Path));
                await _api.DownloadRangeAsync(id.Id, offset, end - offset, (buffer, n) =>
                {
                    pending.Write(buffer, 0, n);
                    // Hand over whole 1 MB pieces as they arrive; the rest waits for more (or the end).
                    while (pending.Length >= 1024 * 1024)
                        at = Transfer(r, pending, at, 1024 * 1024);
                    return Task.CompletedTask;
                }, _stop.Token);
                if (pending.Length > 0)
                    at = Transfer(r, pending, at, (int)pending.Length);     // the last piece, ending at the end of the file
                SetStatus("Up to date");
            }
            catch (Exception e)
            {
                _log($"drive: download of {r.Path} failed: {e.Message}");
                Fail(r, at, end - at, StatusFor(e));
                SetStatus("Couldn't download " + Path.GetFileName(r.Path));
            }
        }
    }

    /// <summary>Hand Windows <paramref name="count"/> bytes from the front of <paramref name="pending"/> at <paramref name="at"/>.</summary>
    private static unsafe long Transfer(Request r, MemoryStream pending, long at, int count)
    {
        var all = pending.GetBuffer();
        fixed (byte* b = all)
        {
            var p = new Cf.OperationParameters
            {
                ParamSize = Cf.SizeTransferData, CompletionStatus = Cf.StatusSuccess,
                Buffer = (nint)b, Offset = at, Length = count,
            };
            Execute(r, Cf.OperationType.TransferData, ref p);
        }
        // keep what's left over at the front
        var left = (int)pending.Length - count;
        Buffer.BlockCopy(all, count, all, 0, left);
        pending.SetLength(left);
        return at + count;
    }

    private static void Fail(Request r, long offset, long length, int status)
    {
        var p = new Cf.OperationParameters
        {
            ParamSize = Cf.SizeTransferData, CompletionStatus = status, Buffer = 0, Offset = offset, Length = Math.Max(length, 0),
        };
        Execute(r, Cf.OperationType.TransferData, ref p);
    }

    // ------------------------------------------------------------- deleted in Explorer: bin it on the server
    private unsafe void OnDelete(Cf.CallbackInfo* info, byte* parameters)
    {
        var r = Capture(info);
        var folder = (*(uint*)(parameters + 8) & Cf.DeleteFlagIsDirectory) != 0;
        _ = Task.Run(() => DeleteAsync(r, folder));
    }

    private async Task DeleteAsync(Request r, bool folder)
    {
        {
            var p = new Cf.OperationParameters { ParamSize = Cf.SizeAck, CompletionStatus = Cf.StatusSuccess };
            try
            {
                if (!r.Self && !Paused && r.Id is { } id)
                {
                    if (id.Kind == DriveId.Library)
                        throw new IOException("A library's folder can't be deleted here; untick it in Settings → Drive.");
                    await _api.TrashAsync(folder, id.Id, _stop.Token);       // to the recycle bin, never for good
                    _state.RemoveTree(Rel(r.Path));
                    _log($"drive: binned {Rel(r.Path)} on the server");
                }
                else if (!r.Self && Paused)
                    throw new IOException("Sync is paused; resume it to delete files on the drive.");
            }
            catch (Exception e)
            {
                p.CompletionStatus = e is ServerError { Status: HttpStatusCode.NotFound } ? Cf.StatusSuccess : StatusFor(e);
                _log($"drive: delete of {r.Path} refused: {e.Message}");
            }
            Execute(r, Cf.OperationType.AckDelete, ref p);
        }
    }

    // ----------------------------------------------------- renamed or moved in Explorer: do it on the server
    private unsafe void OnRename(Cf.CallbackInfo* info, byte* parameters)
    {
        var r = Capture(info);
        var flags = *(uint*)(parameters + 8);
        var target = Marshal.PtrToStringUni(info->VolumeDosName) + Marshal.PtrToStringUni(*(nint*)(parameters + 16));
        var folder = (flags & Cf.RenameFlagIsDirectory) != 0;
        var inScope = (flags & Cf.RenameFlagTargetInScope) != 0;
        _ = Task.Run(() => RenameAsync(r, target, folder, inScope));
    }

    private async Task RenameAsync(Request r, string target, bool folder, bool inScope)
    {
        {
            var p = new Cf.OperationParameters { ParamSize = Cf.SizeAck, CompletionStatus = Cf.StatusSuccess };
            try
            {
                if (!r.Self && r.Id is { } id)
                {
                    if (Paused)
                        throw new IOException("Sync is paused; resume it to rename or move files on the drive.");
                    if (!inScope)
                        throw new IOException("Moving out of the drive isn't supported; copy it out instead.");
                    if (id.Kind == DriveId.Library)
                        throw new IOException("A library's folder can't be renamed or moved here.");
                    await RenameOrMoveAsync(id, folder, Rel(r.Path), Rel(target));
                }
            }
            catch (Exception e)
            {
                p.CompletionStatus = StatusFor(e);
                _log($"drive: rename of {r.Path} refused: {e.Message}");
            }
            Execute(r, Cf.OperationType.AckRename, ref p);
        }
    }

    private async Task RenameOrMoveAsync(DriveId id, bool folder, string fromRel, string toRel)
    {
        var known = _state.Find(id.Kind, id.Id) ?? throw new IOException($"{fromRel} isn't known to the drive");
        var fromParent = DriveNames.ParentOf(fromRel);
        var toParent = DriveNames.ParentOf(toRel);
        var newName = DriveNames.NameOf(toRel);
        if (!fromParent.Equals(toParent, StringComparison.OrdinalIgnoreCase))
        {
            var dest = _state.FindPath(toParent) ?? throw new IOException("That folder isn't on the server yet; try again in a moment.");
            long? destFolder = dest.Kind == DriveId.Library ? null : dest.Id;
            await _api.MoveAsync(folder, id.Id, dest.CollectionId, destFolder, _stop.Token);
            known = known with { CollectionId = dest.CollectionId, ParentId = destFolder };
        }
        if (!DriveNames.NameOf(fromRel).Equals(newName, StringComparison.Ordinal))
            await _api.RenameAsync(folder, id.Id, newName, _stop.Token);
        if (folder)
            _state.MovePath(fromRel, toRel);
        _state.Put(known with { RelPath = toRel });
        _log($"drive: {fromRel} -> {toRel} on the server");
    }

    // ===================================================================================== local changes
    private void Touch(string path)
    {
        var name = Path.GetFileName(path);
        if (DriveNames.IsJunk(name) || path.Equals(Folder, StringComparison.OrdinalIgnoreCase))
            return;
        _dirty[path] = DateTime.UtcNow;                        // handled once it's been quiet for a few seconds
    }

    /// <summary>Local edits and new files, once they've settled (a program saving writes in several steps).</summary>
    private async Task UploadSettledAsync(CancellationToken ct)
    {
        foreach (var (path, seen) in _dirty.ToArray())
        {
            if (DateTime.UtcNow - seen < TimeSpan.FromSeconds(3))
                continue;
            _dirty.TryRemove(path, out _);
            await _uploading.WaitAsync(ct);
            try
            {
                await UploadOneAsync(path, ct);
            }
            catch (IOException) when (File.Exists(path))
            {
                _dirty[path] = DateTime.UtcNow;                // still being written or locked: try again soon
            }
            catch (Exception e) when (e is not OperationCanceledException)
            {
                _log($"drive: couldn't send {path}: {e.Message}");
                SetStatus("Couldn't upload " + Path.GetFileName(path));
            }
            finally
            {
                _uploading.Release();
            }
        }
    }

    private async Task UploadOneAsync(string path, CancellationToken ct)
    {
        var isFolder = Directory.Exists(path);
        if (!isFolder && !File.Exists(path))
            return;                                            // gone again (a temp file)
        var rel = Rel(path);
        if (!rel.Contains('\\'))
            return;                                            // the drive's top level holds only the libraries
        var info = Placeholders.Info(path);
        if (info is { Id: { } id } && _state.Find(id.Kind, id.Id) is { } known
            && known.RelPath.Equals(rel, StringComparison.OrdinalIgnoreCase))
        {
            // One of ours: send changed content up (folders have none).
            if (isFolder || info.Value.State == Cf.InSyncState.InSync || !id.IsFile)
                return;
            using (var check = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read))   // throws while still being written
            {
                // Windows also marks a file "not in sync" when the drive itself moved or renamed it: same bytes as the
                // server's version means nothing to send, only the mark to put back.
                if (known.Sha256 is { Length: > 0 } had
                    && Convert.ToHexString(await System.Security.Cryptography.SHA256.HashDataAsync(check, ct)).Equals(had, StringComparison.OrdinalIgnoreCase))
                {
                    Placeholders.MarkInSync(path);
                    return;
                }
            }
            try
            {
                SetStatus("Uploading " + Path.GetFileName(path));
                var saved = await _api.SaveAsync(id.Id, path, known.Sha256 ?? "", ct);
                _state.SetSha(id.Kind, id.Id, saved.Sha256);
                Placeholders.MarkInSync(path);
                _log($"drive: saved {rel}");
                SetStatus("Up to date");
            }
            catch (ServerError e) when (e.Status == HttpStatusCode.Conflict)
            {
                // Changed on the server too: keep both. The local copy steps aside and goes up as a new file;
                // the change feed then brings the server's version back under the original name.
                var parent = DriveNames.ParentOf(rel);
                var aside = DriveNames.Join(parent, DriveNames.Conflict(DriveNames.NameOf(rel), DateTime.Now,
                                                                        n => _disk.Exists(DriveNames.Join(parent, n))));
                _disk.Detach(rel, aside);
                _state.SetSha(id.Kind, id.Id, "");             // the server's version will come down by the feed
                _log($"drive: {rel} changed on the server too; kept this computer's copy as {aside}");
                Touch(_disk.Full(aside));
            }
            return;
        }

        // New here (not a placeholder, or a copy carrying someone else's identity): add it on the server.
        var parentRel = DriveNames.ParentOf(rel);
        var container = _state.FindPath(parentRel);
        if (container is null)
        {
            _dirty[path] = DateTime.UtcNow;                    // its folder hasn't gone up yet: after it
            return;
        }
        long? parentId = container.Kind == DriveId.Library ? null : container.Id;
        var name = DriveNames.NameOf(rel);
        if (isFolder)
        {
            var made = await _api.NewFolderAsync(container.CollectionId, parentId, name, ct);
            Placeholders.Adopt(path, new DriveId(DriveId.Folder, made.Id));
            _state.Put(new LocalItem(DriveId.Folder, made.Id, container.CollectionId, parentId, rel, null, Populated: true));
            _log($"drive: made folder {rel}");
            foreach (var inside in Directory.EnumerateFileSystemEntries(path))
                Touch(inside);                                 // what was copied in with it goes up next
            return;
        }
        using (File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read)) { }      // throws while still being written
        SetStatus("Uploading " + name);
        var added = await _api.NewFileAsync(container.CollectionId, parentId, name, path, ct);
        var finalRel = rel;
        var serverName = DriveNames.Safe(added.Name);
        if (!serverName.Equals(name, StringComparison.Ordinal))
        {
            finalRel = DriveNames.Join(parentRel, serverName);   // the server numbered it ("plan (1).txt")
            _disk.Move(rel, finalRel);
        }
        Placeholders.Adopt(_disk.Full(finalRel), new DriveId(DriveId.File, added.Id));
        _state.Put(new LocalItem(DriveId.File, added.Id, container.CollectionId, parentId, finalRel, added.Sha256, Populated: false));
        _log($"drive: added {finalRel}");
        SetStatus("Up to date");
    }

    // ===================================================================================== the loop
    private async Task LoopAsync(CancellationToken ct)
    {
        var nextFeed = DateTime.UtcNow;
        while (!ct.IsCancellationRequested)
        {
            try
            {
                if (!Paused)
                {
                    await UploadSettledAsync(ct);
                    if (DateTime.UtcNow >= nextFeed)
                    {
                        await PullAsync(ct);
                        nextFeed = DateTime.UtcNow.AddSeconds(60);   // the server's changes: once a minute
                    }
                }
                await Task.Delay(TimeSpan.FromSeconds(2), ct);
            }
            catch (OperationCanceledException)
            {
                return;
            }
            catch (SignInNeeded)
            {
                SetStatus("Signed out: sign in again");
                await Task.Delay(TimeSpan.FromSeconds(30), ct).ContinueWith(_ => { });
            }
            catch (Exception e)
            {
                SetStatus("Offline: retrying");
                _log("drive: " + e.Message);
                await Task.Delay(TimeSpan.FromSeconds(30), ct).ContinueWith(_ => { });
            }
        }
    }

    /// <summary>Bring each library's changes down (only libraries listed at least once have a cursor).</summary>
    public async Task PullAsync(CancellationToken ct = default)
    {
        foreach (var lib in _state.Libraries())
        {
            var cursor = _state.Cursor(lib.CollectionId);
            if (cursor == 0)
                continue;                                     // not opened yet: nothing local to keep up to date
            ChangePage page;
            do
            {
                page = await _api.ChangesAsync(lib.CollectionId, cursor, ct);
                if (page.Changes.Count > 0)
                {
                    _applier.Apply(lib.CollectionId, page.Changes);
                    foreach (var line in _applier.Log) _log("drive: " + line);
                    _applier.Log.Clear();
                }
                cursor = page.Cursor;
                _state.SetCursor(lib.CollectionId, cursor);
            } while (page.More && !ct.IsCancellationRequested);
        }
        SetStatus("Up to date");
    }
}
