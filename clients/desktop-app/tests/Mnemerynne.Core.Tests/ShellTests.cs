using Mnemerynne.Core;

namespace Mnemerynne.Core.Tests;

/// <summary>The window's rules: links, tabs, sessions, profiles, versions, sign-in.</summary>
public class ShellTests
{
    private static readonly Uri Server = new("https://erynne.eternalhotels.com");

    [Theory]
    [InlineData("https://erynne.eternalhotels.com/files", false, LinkTarget.SameTab)]
    [InlineData("https://erynne.eternalhotels.com/wiki/Home", true, LinkTarget.NewTab)]       // Ctrl+click, _blank
    [InlineData("https://ERYNNE.eternalhotels.com/help", true, LinkTarget.NewTab)]            // host case doesn't matter
    [InlineData("http://erynne.eternalhotels.com/files", false, LinkTarget.External)]         // another scheme: not us
    [InlineData("https://erynne.eternalhotels.com:8443/files", false, LinkTarget.External)]   // another port: not us
    [InlineData("https://example.com/", false, LinkTarget.External)]
    [InlineData("https://example.com/", true, LinkTarget.External)]
    [InlineData("https://login.microsoftonline.com/common/oauth2", false, LinkTarget.SameTab)]  // M365 linking
    [InlineData("https://login.microsoftonline.com/common/oauth2", true, LinkTarget.External)]
    [InlineData("mailto:someone@example.com", false, LinkTarget.External)]
    [InlineData("javascript:alert(1)", false, LinkTarget.Blocked)]
    [InlineData("file:///C:/Windows/win.ini", false, LinkTarget.Blocked)]
    [InlineData("not a url", false, LinkTarget.Blocked)]
    public void Links_go_where_they_belong(string url, bool newWindow, LinkTarget expected) =>
        Assert.Equal(expected, new LinkRouter(Server).Route(url, newWindow));

    [Fact]
    public void The_sign_in_redirect_is_recognised() =>
        Assert.True(LinkRouter.IsAppRedirect(OAuthPkce.RedirectUri + "?code=abc&state=x"));

    [Fact]
    public void Ctrl_tab_wraps_round_and_ctrl_9_is_the_last_tab()
    {
        Assert.Equal(0, TabMath.Cycle(2, 3, +1));
        Assert.Equal(2, TabMath.Cycle(0, 3, -1));
        Assert.Equal(4, TabMath.Numbered(9, 5));
        Assert.Equal(1, TabMath.Numbered(2, 5));
        Assert.Equal(-1, TabMath.Numbered(7, 5));        // no 7th tab: nothing happens
    }

    [Theory]
    [InlineData(1, 1, 4, 1)]   // closing the showing tab: its right neighbour shows
    [InlineData(3, 3, 4, 2)]   // the last one: its left neighbour shows
    [InlineData(0, 2, 4, 1)]   // a tab before the showing one: the same tab stays showing (one place left)
    [InlineData(3, 1, 4, 1)]   // a tab after it: nothing moves
    [InlineData(0, 0, 1, -1)]  // the only tab
    public void Closing_a_tab_picks_the_next_one(int closed, int active, int count, int expected) =>
        Assert.Equal(expected, TabMath.AfterClose(closed, active, count));

    [Theory]
    [InlineData(0, 2, 0, 2)]   // dragging the showing tab: it stays showing, wherever it lands
    [InlineData(0, 2, 1, 0)]   // dragging one from before it to after it
    [InlineData(3, 0, 1, 2)]   // dragging one from after it to before it
    [InlineData(3, 2, 0, 0)]   // elsewhere: untouched
    public void Dragging_a_tab_keeps_the_right_one_showing(int from, int to, int active, int expected) =>
        Assert.Equal(expected, TabMath.AfterMove(from, to, active));

    [Fact]
    public void Reopening_closed_tabs_goes_newest_first()
    {
        var closed = new ClosedTabs();
        closed.Push(new SavedTab("https://x/a"));
        closed.Push(new SavedTab("https://x/b"));
        Assert.Equal("https://x/b", closed.Pop()!.Url);
        Assert.Equal("https://x/a", closed.Pop()!.Url);
        Assert.Null(closed.Pop());
    }

    [Fact]
    public void Sessions_round_trip_and_never_restore_other_sites()
    {
        var path = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString("N"), "tabs.json");
        new TabSession
        {
            Tabs = [new SavedTab("https://erynne.eternalhotels.com/files"), new SavedTab("https://evil.example/"),
                    new SavedTab("https://erynne.eternalhotels.com/wiki")],
            Active = 2,
        }.Save(path);
        var back = TabSession.Load(path, Server);
        Assert.Equal(["https://erynne.eternalhotels.com/files", "https://erynne.eternalhotels.com/wiki"], back.Tabs.Select(t => t.Url));
        Assert.Equal(1, back.Active);                        // clamped into the list that's left
    }

    [Fact]
    public void A_missing_or_broken_session_opens_the_start_page()
    {
        var path = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString("N") + ".json");
        File.WriteAllText(path, "{not json");
        var fresh = TabSession.Load(path, Server);
        Assert.Equal("https://erynne.eternalhotels.com/", Assert.Single(fresh.Tabs).Url);
    }

    [Theory]
    [InlineData("erynne.eternalhotels.com", "https://erynne.eternalhotels.com")]
    [InlineData("https://erynne.eternalhotels.com/files?x=1", "https://erynne.eternalhotels.com")]
    [InlineData("http://localhost:8000", "http://localhost:8000")]          // a dev server on this computer
    [InlineData("http://erynne.eternalhotels.com", null)]                   // plain http elsewhere: refused
    [InlineData("ftp://x", null)]
    [InlineData("", null)]
    public void Server_addresses_are_cleaned_up(string typed, string? expected) =>
        Assert.Equal(expected, ProfileStore.NormaliseServer(typed));

    [Fact]
    public void Profiles_round_trip_and_pick_the_default()
    {
        ProfileStore.Root = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString("N"));
        var store = new ProfileStore();
        var work = new Profile { Server = "https://erynne.eternalhotels.com", Name = "Mnemerynne" };
        var home = new Profile { Server = "https://beepsquad.wtf", Name = "Sad Girls Club", DriveLibraries = [3, 7] };
        store.Profiles.AddRange([work, home]);
        store.Default = home.Id;
        store.Save();
        var back = ProfileStore.Load();
        Assert.Equal("Sad Girls Club", back.Startup()!.Name);
        Assert.Equal([3L, 7L], back.Startup()!.DriveLibraries);
        Assert.Null(new ProfileStore().Startup());          // first run: none yet
    }

    [Theory]
    [InlineData("0.10.0", "0.9.2", true)]
    [InlineData("0.2", "0.1.9", true)]
    [InlineData("0.1.0", "0.1.0", false)]
    [InlineData("0.1", "0.1.0", false)]
    [InlineData("1.0.0", "1.0.1", false)]
    [InlineData(null, "0.1.0", false)]
    [InlineData("v2", "0.1.0", false)]                       // not a version: never "newer"
    [InlineData("0.2.02", "0.2.1", true)]                    // Doll's two-digit patches (from 2026-11-26)
    [InlineData("0.2.01", "0.2.1", false)]                   // the same number as 0.2.1: never offered over it
    [InlineData("0.2.10", "0.2.09", true)]
    public void Versions_compare_as_numbers(string? candidate, string installed, bool newer) =>
        Assert.Equal(newer, AppVersion.IsNewer(candidate, installed));

    [Fact]
    public void A_download_is_checked_against_its_hash()
    {
        var path = Path.GetTempFileName();
        File.WriteAllBytes(path, "newest"u8.ToArray());
        Assert.True(Download.Matches(path, Convert.ToHexString(System.Security.Cryptography.SHA256.HashData("newest"u8.ToArray()))));
        // the server sends lower case
        Assert.True(Download.Matches(path, Convert.ToHexString(System.Security.Cryptography.SHA256.HashData("newest"u8.ToArray())).ToLowerInvariant()));
        Assert.False(Download.Matches(path, new string('0', 64)));
    }

    [Fact]
    public void The_sign_in_answer_must_carry_our_state()
    {
        var pkce = new OAuthPkce();
        var url = pkce.AuthorizeUrl(Server, "mcp_abc");
        Assert.Contains("scope=desktop", url);
        Assert.Contains("code_challenge_method=S256", url);
        Assert.Equal("the-code", pkce.CodeFrom($"{OAuthPkce.RedirectUri}?code=the-code&state={pkce.State}", out var none));
        Assert.Null(none);
        Assert.Null(pkce.CodeFrom($"{OAuthPkce.RedirectUri}?code=the-code&state=forged", out var forged));
        Assert.NotNull(forged);
        Assert.Null(pkce.CodeFrom($"{OAuthPkce.RedirectUri}?error=access_denied&state={pkce.State}", out var denied));
        Assert.Contains("Deny", denied);
    }
}

/// <summary>Tabs sort themselves: the site's places on the left, opened things on the right.</summary>
public class TabKindTests
{
    [Theory]
    [InlineData("https://erynne.eternalhotels.com/", TabKind.Place)]
    [InlineData("https://erynne.eternalhotels.com/files#c=3&f=12", TabKind.Place)]
    [InlineData("https://erynne.eternalhotels.com/automations", TabKind.Place)]
    [InlineData("https://erynne.eternalhotels.com/wiki", TabKind.Place)]
    [InlineData("https://erynne.eternalhotels.com/files/7391/view", TabKind.Document)]     // a Markdown file
    [InlineData("https://erynne.eternalhotels.com/files/7538/inline", TabKind.Document)]   // a spreadsheet's preview
    [InlineData("https://erynne.eternalhotels.com/files/12/pdf", TabKind.Document)]
    [InlineData("https://erynne.eternalhotels.com/edit/7391", TabKind.Document)]
    [InlineData("https://erynne.eternalhotels.com/wiki/7370", TabKind.Document)]           // a wiki page
    [InlineData("https://erynne.eternalhotels.com/help/user/desktop-app", TabKind.Document)]
    [InlineData("https://erynne.eternalhotels.com/files/zip/abc", TabKind.Place)]          // not one file
    public void Tabs_know_which_side_they_belong_on(string url, TabKind kind) => Assert.Equal(kind, TabKinds.KindOf(url));

    [Fact]
    public void Grouping_keeps_each_groups_own_order()
    {
        var kinds = new[] { TabKind.Document, TabKind.Place, TabKind.Document, TabKind.Place };
        Assert.Equal([1, 3, 0, 2], TabKinds.Grouped(kinds));
    }
}

/// <summary>The app's own version, as written in Directory.Build.props.</summary>
public class CurrentVersionTests
{
    [Fact]
    public void The_version_shows_as_written_without_the_commit() =>
        Assert.Matches(@"^\d+\.\d+\.\d+$", AppVersion.Current);   // e.g. "0.2.1" or "0.2.02", never "0.2.1+a1de155…"
}
