using System.Text.Json;

namespace Mnemerynne.Core;

/// <summary>One saved tab: where it was. The title comes back from the page when it loads.</summary>
public sealed record SavedTab(string Url, string? Title = null, string? Group = null);   // Group: a SavedGroup's Id

/// <summary>A tab group (like Firefox's): its name, colour (#rrggbb) and whether it's folded away.</summary>
public sealed record SavedGroup(string Id, string Name, string Color, bool Collapsed = false);

/// <summary>A profile's open tabs, in order, and which one was showing: written to tabs.json, read at start.</summary>
public sealed class TabSession
{
    public List<SavedTab> Tabs { get; set; } = [];
    public int Active { get; set; }
    public List<SavedGroup> Groups { get; set; } = [];   // in strip order

    private static readonly JsonSerializerOptions Json = new() { WriteIndented = true };

    /// <summary>The session in a file, or a single tab at the server's start page if there's none (or it's broken).</summary>
    public static TabSession Load(string path, Uri server)
    {
        try
        {
            if (File.Exists(path))
            {
                var got = JsonSerializer.Deserialize<TabSession>(File.ReadAllText(path), Json);
                if (got is not null)
                {
                    var router = new LinkRouter(server);
                    // Only the server's own pages come back (a broken or edited file can't open anything else).
                    got.Tabs = got.Tabs.Where(t => Uri.TryCreate(t.Url, UriKind.Absolute, out var u) && router.IsServer(u))
                                       .ToList();
                    got.Groups ??= [];
                    var known = got.Groups.Select(g => g.Id).ToHashSet();
                    got.Tabs = got.Tabs.Select(t => t.Group is null || known.Contains(t.Group) ? t : t with { Group = null }).ToList();
                    if (got.Tabs.Count > 0)
                    {
                        got.Active = Math.Clamp(got.Active, 0, got.Tabs.Count - 1);
                        return got;
                    }
                }
            }
        }
        catch (Exception e) when (e is IOException or JsonException or UnauthorizedAccessException)
        {
            // fall through to a fresh session: a lost tab list must never stop the app opening
        }
        return new TabSession { Tabs = [new SavedTab(new Uri(server, "/").ToString())], Active = 0 };
    }

    /// <summary>Write the session (to a temp file, then swapped in, so a crash mid-write can't leave half a file).</summary>
    public void Save(string path)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var tmp = path + ".tmp";
        File.WriteAllText(tmp, JsonSerializer.Serialize(this, Json));
        File.Move(tmp, path, overwrite: true);
    }
}

/// <summary>
/// Recently closed tabs, for Ctrl+Shift+T. Newest last; only the last few are kept.
/// </summary>
public sealed class ClosedTabs
{
    private readonly List<SavedTab> _closed = [];
    public const int Keep = 20;

    public void Push(SavedTab tab)
    {
        _closed.Add(tab);
        if (_closed.Count > Keep)
            _closed.RemoveAt(0);                 // the oldest goes
    }

    /// <summary>The most recently closed tab, taken off the list; null when there's none.</summary>
    public SavedTab? Pop()
    {
        if (_closed.Count == 0)
            return null;
        var last = _closed[^1];
        _closed.RemoveAt(_closed.Count - 1);
        return last;
    }

    public int Count => _closed.Count;
}

/// <summary>Keyboard tab switching, as browsers do it.</summary>
public static class TabMath
{
    /// <summary>Ctrl+Tab (+1) / Ctrl+Shift+Tab (-1): the next tab round the ring.</summary>
    public static int Cycle(int current, int count, int step) =>
        count == 0 ? -1 : ((current + step) % count + count) % count;

    /// <summary>Ctrl+1..8 go to that tab (if there is one); Ctrl+9 always goes to the last tab.</summary>
    public static int Numbered(int digit, int count)
    {
        if (count == 0 || digit < 1 || digit > 9)
            return -1;
        if (digit == 9)
            return count - 1;
        return digit <= count ? digit - 1 : -1;
    }

    /// <summary>Which tab shows after closing <paramref name="closed"/>: the one to its right, else its left.</summary>
    public static int AfterClose(int closed, int active, int countBefore)
    {
        var after = countBefore - 1;
        if (after == 0)
            return -1;                                       // that was the last tab
        if (closed != active)
            return active > closed ? active - 1 : active;    // a background tab: the same one stays showing
        return Math.Min(closed, after - 1);                  // the showing tab: its right neighbour moves into its place
    }

    /// <summary>Where the active tab ends up after dragging the tab at <paramref name="from"/> to <paramref name="to"/>.</summary>
    public static int AfterMove(int from, int to, int active)
    {
        if (active == from)
            return to;
        if (from < active && to >= active)
            return active - 1;
        if (from > active && to <= active)
            return active + 1;
        return active;
    }
}
