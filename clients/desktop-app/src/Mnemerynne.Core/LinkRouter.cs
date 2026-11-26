namespace Mnemerynne.Core;

/// <summary>Where a link should open.</summary>
public enum LinkTarget
{
    /// <summary>Keep it in the tab it's in (an ordinary page of the server, or a sign-in step).</summary>
    SameTab,
    /// <summary>A new app tab (Ctrl+click, middle-click, target=_blank, window.open on the server's own pages).</summary>
    NewTab,
    /// <summary>The system browser: anything outside the server.</summary>
    External,
    /// <summary>Nothing: a scheme the app won't hand to anyone (javascript:, file:, data: as a page).</summary>
    Blocked,
}

/// <summary>
/// The link rules, kept out of the window code so they can be tested. The server's own pages stay in the app;
/// the few sign-in pages the server sends people through (linking a Microsoft 365 account) stay in the tab so
/// they can come back; everything else goes to the system browser.
/// </summary>
public sealed class LinkRouter
{
    // Microsoft's sign-in, which Preferences → Microsoft 365 goes through and then comes back to the server.
    private static readonly string[] SignInHosts = ["login.microsoftonline.com", "login.live.com", "login.microsoft.com"];

    private readonly Uri _server;   // the profile's server, e.g. https://erynne.eternalhotels.com

    public LinkRouter(Uri server)
    {
        _server = server;           // only its scheme, host and port matter (the origin)
    }

    /// <summary>True when the address is on the server (same scheme, host and port).</summary>
    public bool IsServer(Uri uri) =>
        uri.Scheme.Equals(_server.Scheme, StringComparison.OrdinalIgnoreCase)
        && uri.Host.Equals(_server.Host, StringComparison.OrdinalIgnoreCase)
        && uri.Port == _server.Port;

    /// <summary>
    /// A page asked to go somewhere. <paramref name="newWindow"/> is true when it asked for a new window
    /// (target=_blank, window.open, Ctrl/middle-click).
    /// </summary>
    public LinkTarget Route(string address, bool newWindow)
    {
        if (!Uri.TryCreate(address, UriKind.Absolute, out var uri))
            return LinkTarget.Blocked;                                   // not even an address
        if (uri.Scheme == "about" && !newWindow)
            return LinkTarget.SameTab;                                   // about:blank while a tab starts up
        if (IsServer(uri))
            return newWindow ? LinkTarget.NewTab : LinkTarget.SameTab;   // the server's own pages stay in the app
        if (uri.Scheme is "http" or "https")
        {
            if (!newWindow && SignInHosts.Contains(uri.Host, StringComparer.OrdinalIgnoreCase))
                return LinkTarget.SameTab;                               // M365 linking: comes back to the server
            return LinkTarget.External;                                  // other sites: the system browser
        }
        if (uri.Scheme is "mailto" or "tel" or "ms-word" or "ms-excel" or "ms-powerpoint")
            return LinkTarget.External;                                  // handed to the program Windows picks
        return LinkTarget.Blocked;                                       // javascript:, file:, data:, unknown schemes
    }

    /// <summary>The OAuth redirect the app catches instead of loading (see OAuthPkce.RedirectUri).</summary>
    public static bool IsAppRedirect(string address) =>
        address.StartsWith(OAuthPkce.RedirectUri, StringComparison.OrdinalIgnoreCase);
}
