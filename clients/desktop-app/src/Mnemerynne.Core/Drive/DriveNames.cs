using System.Text;

namespace Mnemerynne.Core.Drive;

/// <summary>What a placeholder is on the server. Stored inside each placeholder as its "file identity" bytes.</summary>
public readonly record struct DriveId(char Kind, long Id)
{
    public const char Library = 'c';   // a library's top folder (collection id)
    public const char Folder = 'd';
    public const char File = 'f';

    public bool IsFile => Kind == File;

    /// <summary>"f:123": short and readable, so a support person can see it in a debugger.</summary>
    public byte[] ToBytes() => Encoding.ASCII.GetBytes($"{Kind}:{Id}");

    public override string ToString() => $"{Kind}:{Id}";

    public static DriveId? Parse(ReadOnlySpan<byte> bytes)
    {
        var text = Encoding.ASCII.GetString(bytes);
        if (text.Length < 3 || text[1] != ':' || !"cdf".Contains(text[0]) || !long.TryParse(text.AsSpan(2), out var id))
            return null;
        return new DriveId(text[0], id);
    }
}

/// <summary>Names on the drive: Windows can't hold every name the server can, and conflicts need a second name.</summary>
public static class DriveNames
{
    private static readonly char[] Invalid = ['<', '>', ':', '"', '/', '\\', '|', '?', '*'];
    private static readonly HashSet<string> Reserved = new(StringComparer.OrdinalIgnoreCase)
    {
        "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    };

    /// <summary>
    /// A server name made safe for Windows: characters Windows refuses become "_", trailing dots and spaces go,
    /// and device names (CON, NUL, ...) get "_" added. Most names come through unchanged.
    /// </summary>
    public static string Safe(string name)
    {
        var sb = new StringBuilder(name.Length);
        foreach (var c in name)
            sb.Append(c < 32 || Invalid.Contains(c) ? '_' : c);
        var safe = sb.ToString().TrimEnd('.', ' ');
        if (safe.Length == 0)
            safe = "_";
        var stem = safe.Split('.')[0];
        if (Reserved.Contains(stem))
            safe = "_" + safe;
        return safe;
    }

    /// <summary>
    /// The name a local copy gets when it can't go over the server's (someone changed it there too):
    /// "Budget (PC conflict 2026-11-25).xlsx", with a number added if that's taken too.
    /// </summary>
    public static string Conflict(string name, DateTime today, Func<string, bool> taken)
    {
        var ext = Path.GetExtension(name);
        var stem = Path.GetFileNameWithoutExtension(name);
        var label = $"{Environment.MachineName} conflict {today:yyyy-MM-dd}";
        for (var n = 1; ; n++)
        {
            var candidate = n == 1 ? $"{stem} ({label}){ext}" : $"{stem} ({label} {n}){ext}";
            if (!taken(candidate))
                return candidate;
        }
    }

    /// <summary>Temporary and lock files programs make beside documents; the drive never uploads these.</summary>
    public static bool IsJunk(string name) =>
        name.StartsWith("~$", StringComparison.Ordinal)            // Word/Excel lock files
        || name.StartsWith(".~lock.", StringComparison.Ordinal)    // LibreOffice
        || name.EndsWith(".tmp", StringComparison.OrdinalIgnoreCase)
        || name.EndsWith(".crdownload", StringComparison.OrdinalIgnoreCase)
        || name.EndsWith(".partial", StringComparison.OrdinalIgnoreCase)
        || name.Equals("desktop.ini", StringComparison.OrdinalIgnoreCase)
        || name.Equals("Thumbs.db", StringComparison.OrdinalIgnoreCase)
        || (name.StartsWith('~') && name.EndsWith(".tmp", StringComparison.OrdinalIgnoreCase));

    /// <summary>"a\b" + "c" with backslashes, the way the drive's state keeps paths (relative to the drive's folder).</summary>
    public static string Join(string parent, string name) => parent.Length == 0 ? name : parent + "\\" + name;

    public static string ParentOf(string rel)
    {
        var i = rel.LastIndexOf('\\');
        return i < 0 ? "" : rel[..i];
    }

    public static string NameOf(string rel)
    {
        var i = rel.LastIndexOf('\\');
        return i < 0 ? rel : rel[(i + 1)..];
    }
}
