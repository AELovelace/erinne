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
    /// The tabs in strip order: places first, then documents, each group keeping its own order (a stable sort).
    /// Returns the new order as indexes into <paramref name="kinds"/>.
    /// </summary>
    public static int[] Grouped(IReadOnlyList<TabKind> kinds) =>
        Enumerable.Range(0, kinds.Count).OrderBy(i => kinds[i] == TabKind.Document ? 1 : 0).ThenBy(i => i).ToArray();
}
