using System.Text.RegularExpressions;

namespace Mnemerynne.Core;

/// <summary>Which side of the tab strip a tab sits on (Doll: "so tabs stay organized automatically").</summary>
public enum TabKind
{
    /// <summary>One of the site's places (Ask, Files, Wiki, Rooms, Automator, Preferences…): the left group.</summary>
    Place,
    /// <summary>Something opened from a place: a file's view or PDF, the editor, a wiki page, a help page: the right group.</summary>
    Document,
}

/// <summary>
/// Sorting tabs into the two groups, from the address alone. A tab moves to the other group by itself when it
/// navigates (Files → a file's view goes right; back to Files goes left).
/// </summary>
public static partial class TabKinds
{
    // The server's addresses for one opened thing (web/files.py, web/editor.py, web/wiki.py, web/help.py, /s/ shares).
    [GeneratedRegex(@"^/(files/\d+(/|$)|edit/|wiki/\d+|help(/|$)|s/|media/files/\d+)", RegexOptions.IgnoreCase)]
    private static partial Regex DocumentPath();

    public static TabKind KindOf(string url) =>
        Uri.TryCreate(url, UriKind.Absolute, out var u) && DocumentPath().IsMatch(u.AbsolutePath) ? TabKind.Document : TabKind.Place;

    /// <summary>
    /// The tabs in strip order: places first, then documents, each kind keeping its own order (a stable sort).
    /// Returns the new order as indexes into <paramref name="kinds"/>.
    /// </summary>
    public static int[] Grouped(IReadOnlyList<TabKind> kinds) =>
        Ordered(kinds.Select(k => (k, (int?)null)).ToList());

    /// <summary>
    /// The strip order with tab groups (Doll, 2026-11-26): ungrouped places first, then each group's tabs together
    /// (groups in their own order, <c>group</c> = the group's position), then ungrouped opened things. Within each
    /// block the tabs keep their order. Returns the new order as indexes into <paramref name="tabs"/>.
    /// </summary>
    public static int[] Ordered(IReadOnlyList<(TabKind Kind, int? Group)> tabs) =>
        Enumerable.Range(0, tabs.Count).OrderBy(i => Block(tabs[i])).ThenBy(i => i).ToArray();

    /// <summary>Which block of the strip a tab belongs in (lower comes first).</summary>
    public static int Block((TabKind Kind, int? Group) tab) =>
        tab.Group is { } g ? 1 + g : tab.Kind == TabKind.Place ? 0 : int.MaxValue;
}

/// <summary>How wide tabs are: places compact, opened things the normal width, both shrinking alike to fit.</summary>
public static class TabWidths
{
    public const double Place = 100, Document = 200;          // the widths with room to spare (Doll, 2026-11-26)
    public const double PlaceMin = 64, DocumentMin = 90;      // how far each shrinks before the strip scrolls

    /// <summary>
    /// The width of a place tab and of an opened-thing tab when <paramref name="places"/> and
    /// <paramref name="documents"/> tabs share <paramref name="room"/> (each tab also takes <paramref name="gap"/>).
    /// With room to spare they're 100 and 200. With less, the wide opened-thing tabs shrink first (down to 90); only
    /// then do the already-compact places shrink (down to 64), so the places you work in stay readable.
    /// </summary>
    public static (double Place, double Document) Fit(double room, int places, int documents, double gap = 2)
    {
        var want = places * (Place + gap) + documents * (Document + gap);
        if (want <= room || want <= 0)
            return (Place, Document);
        var doc = documents == 0 ? Document
            : Math.Clamp((room - places * (Place + gap)) / documents - gap, DocumentMin, Document);   // files give way first
        var place = places == 0 ? Place
            : Math.Clamp((room - documents * (doc + gap)) / places - gap, PlaceMin, Place);           // then places, if they must
        return (place, doc);
    }
}
