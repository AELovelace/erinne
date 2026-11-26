using System.Runtime.InteropServices;
using Mnemerynne.Core;
using Mnemerynne.Core.Drive;

namespace Mnemerynne.Drive;

/// <summary>
/// The drive's own changes to the folder on this computer, done with Cloud Files placeholders. Everything here
/// is done by the app's own process, which the callbacks recognise and never send back to the server
/// (CloudProvider.FromSelf): these are changes the server already has.
/// </summary>
public sealed unsafe class Placeholders(string root) : IPlaceholders
{
    public string Root { get; } = root;                     // the drive's folder, e.g. C:\Users\Edith\Mnemerynne

    public string Full(string rel) => rel.Length == 0 ? Root : Path.Combine(Root, rel);

    public bool Exists(string rel) => File.Exists(Full(rel)) || Directory.Exists(Full(rel));

    /// <summary>Windows' metadata for a server item: its size and time (100 ns ticks since 1601, UTC).</summary>
    internal static Cf.FsMetadata Metadata(RemoteItem item)
    {
        var t = item.ModifiedUtc.ToFileTimeUtc();
        return new Cf.FsMetadata
        {
            CreationTime = t, LastAccessTime = t, LastWriteTime = t, ChangeTime = t,
            FileAttributes = item.IsFolder ? Cf.FileAttributeDirectory : Cf.FileAttributeNormal,
            FileSize = item.IsFolder ? 0 : item.Size,
        };
    }

    public void Create(string parentRel, string name, RemoteItem item, DriveId id) =>
        CreateMany(Full(parentRel), [(name, item, id)]);

    /// <summary>
    /// Placeholders for several server items in one folder (marked "in sync": they match the server). Folders are
    /// left to be listed when Explorer first opens them. Returns how many were made.
    /// </summary>
    public static int CreateMany(string folder, IReadOnlyList<(string Name, RemoteItem Item, DriveId Id)> items)
    {
        if (items.Count == 0)
            return 0;
        var names = new nint[items.Count];
        var ids = new nint[items.Count];
        var infos = new Cf.PlaceholderCreateInfo[items.Count];
        try
        {
            for (var i = 0; i < items.Count; i++)
            {
                names[i] = Marshal.StringToHGlobalUni(items[i].Name);       // freed below
                var identity = items[i].Id.ToBytes();
                ids[i] = Marshal.AllocHGlobal(identity.Length);
                Marshal.Copy(identity, 0, ids[i], identity.Length);
                infos[i] = new Cf.PlaceholderCreateInfo
                {
                    RelativeFileName = names[i],
                    FsMetadata = Metadata(items[i].Item),
                    FileIdentity = ids[i],
                    FileIdentityLength = (uint)identity.Length,
                    Flags = Cf.PlaceholderCreateFlagMarkInSync,
                };
            }
            fixed (Cf.PlaceholderCreateInfo* p = infos)
            {
                // Not stopping on the first error: a name already there fails on its own, the rest are made.
                Cf.CfCreatePlaceholders(folder, p, (uint)infos.Length, 0, out var done);
                return (int)done;
            }
        }
        finally
        {
            foreach (var n in names) Marshal.FreeHGlobal(n);
            foreach (var n in ids) Marshal.FreeHGlobal(n);
        }
    }

    public void Move(string fromRel, string toRel)
    {
        var from = Full(fromRel);
        var to = Full(toRel);
        Directory.CreateDirectory(Path.GetDirectoryName(to)!);
        if (Directory.Exists(from))
            Directory.Move(from, to);                       // never over anything: throws IOException if taken
        else
            File.Move(from, to, overwrite: false);
    }

    /// <summary>
    /// New content on the server: the placeholder takes the new size and time and drops its downloaded bytes
    /// (fetched again when something opens it). A file pinned "Always keep on this device" can't drop them, so its
    /// placeholder is made again instead (it loses the pin; the person can pin it again).
    /// </summary>
    public void Refresh(string rel, RemoteItem item)
    {
        var path = Full(rel);
        var meta = Metadata(item);
        var id = new DriveId(DriveId.File, item.Id).ToBytes();
        int hr;
        using (var h = Cf.OpenForState(path, write: true))
        fixed (byte* p = id)
            hr = Cf.CfUpdatePlaceholder(h, meta, p, (uint)id.Length, 0, 0, Cf.UpdateFlagMarkInSync | Cf.UpdateFlagDehydrate, 0, 0);
        if (hr >= 0)
            return;
        // Couldn't drop the bytes (pinned, or open in a program): replace the placeholder.
        File.Delete(path);                                   // our own process: the server isn't told
        CreateMany(Path.GetDirectoryName(path)!, [(Path.GetFileName(path), item, new DriveId(DriveId.File, item.Id))]);
    }

    /// <summary>True when the local file has changes the server hasn't got (or isn't a placeholder at all).</summary>
    public bool HasLocalChanges(string rel)
    {
        var state = InSync(Full(rel));
        return state is null || state == Cf.InSyncState.NotInSync;
    }

    /// <summary>A placeholder's identity and in-sync state; null when the path isn't a placeholder (a new local file).</summary>
    internal static (DriveId? Id, Cf.InSyncState State)? Info(string path)
    {
        try
        {
            using var h = Cf.OpenForState(path, write: false);
            var buffer = stackalloc byte[256];
            var hr = Cf.CfGetPlaceholderInfo(h, 0 /* CF_PLACEHOLDER_INFO_BASIC */, buffer, 256, out _);
            if (hr < 0)
                return null;                                // not a placeholder
            var state = (Cf.InSyncState)(*(uint*)(buffer + 4));
            var length = *(uint*)(buffer + 24);
            var id = length is > 0 and <= 200 ? DriveId.Parse(new ReadOnlySpan<byte>(buffer + Cf.BasicInfoIdentityOffset, (int)length)) : null;
            return (id, state);
        }
        catch (IOException)
        {
            return null;
        }
    }

    private static Cf.InSyncState? InSync(string path) => Info(path)?.State;

    public void Delete(string rel)
    {
        var path = Full(rel);
        if (Directory.Exists(path))
            Directory.Delete(path, recursive: true);
        else if (File.Exists(path))
            File.Delete(path);
    }

    /// <summary>Set a local file aside under another name, as a plain file (it goes up as a new file).</summary>
    public void Detach(string fromRel, string toRel)
    {
        var to = Full(toRel);
        File.Move(Full(fromRel), to, overwrite: false);
        try
        {
            using var h = Cf.OpenForConvert(to);
            Cf.CfRevertPlaceholder(h, 0, 0);                 // a plain file now; fails harmlessly if it already is
        }
        catch (IOException)
        {
            // open in a program: it stays a placeholder with the old identity; LocalSync sees it isn't that file's path
        }
    }

    /// <summary>Make a new local file or folder (made in Explorer, now uploaded) a placeholder of its server item.</summary>
    public static void Adopt(string path, DriveId id)
    {
        var identity = id.ToBytes();
        using var h = Cf.OpenForConvert(path);
        fixed (byte* p = identity)
            Cf.Check(Cf.CfConvertToPlaceholder(h, p, (uint)identity.Length, Cf.ConvertFlagMarkInSync, 0, 0), "Convert to placeholder");
    }

    /// <summary>Mark a placeholder as matching the server again (after its changes went up).</summary>
    public static void MarkInSync(string path)
    {
        using var h = Cf.OpenForState(path, write: true);
        Cf.Check(Cf.CfSetInSyncState(h, Cf.InSyncState.InSync, 0, 0), "Mark in sync");
    }

    /// <summary>
    /// After a save went up, the placeholder also takes the server's identity (unchanged) and stays downloaded.
    /// </summary>
    public static void Retag(string path, DriveId id)
    {
        var identity = id.ToBytes();
        using var h = Cf.OpenForState(path, write: true);
        var meta = new Cf.FsMetadata();                          // zeros: keep what's there (only the identity changes)
        fixed (byte* p = identity)
            Cf.Check(Cf.CfUpdatePlaceholder(h, meta, p, (uint)identity.Length, 0, 0, Cf.UpdateFlagMarkInSync, 0, 0), "Update placeholder");
    }
}
