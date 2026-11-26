using System.Security.Cryptography;
using System.Text;

namespace Mnemerynne.Core;

/// <summary>
/// The app's sign-in is OAuth 2.1 with PKCE, like the backup app's (clients/desktop-sync/signin.py), but the
/// sign-in page opens in the app's own window: the person signs in with the site's normal form (which also signs
/// the tabs in, since they share cookies) and approves, and the app catches the redirect to this loopback address
/// before it loads. Nothing ever listens on that port.
/// </summary>
public sealed class OAuthPkce
{
    /// <summary>Where the server sends the code back to. The server accepts any loopback address (oauth.py).</summary>
    public const string RedirectUri = "http://127.0.0.1:53682/callback";

    /// <summary>The scope that makes a token work on /api/desktop/* and /api/drive/* only (web/desktop.py).</summary>
    public const string Scope = "desktop";

    public string Verifier { get; }    // the secret half, kept in memory until the code is swapped
    public string Challenge { get; }   // its SHA-256, sent with the sign-in request
    public string State { get; }       // a random value the redirect must bring back (stops a forged redirect)

    public OAuthPkce()
    {
        Verifier = Base64Url(RandomNumberGenerator.GetBytes(48));          // 64 characters, as RFC 7636 allows
        Challenge = Base64Url(SHA256.HashData(Encoding.ASCII.GetBytes(Verifier)));
        State = Base64Url(RandomNumberGenerator.GetBytes(16));
    }

    /// <summary>The page the sign-in window opens.</summary>
    public string AuthorizeUrl(Uri server, string clientId)
    {
        var query = new Dictionary<string, string>
        {
            ["response_type"] = "code",
            ["client_id"] = clientId,
            ["redirect_uri"] = RedirectUri,
            ["state"] = State,
            ["code_challenge"] = Challenge,
            ["code_challenge_method"] = "S256",
            ["scope"] = Scope,
        };
        var text = string.Join("&", query.Select(kv => $"{kv.Key}={Uri.EscapeDataString(kv.Value)}"));
        return new Uri(server, "/oauth/authorize?" + text).ToString();
    }

    /// <summary>
    /// The code from the redirect, or null if it isn't ours (wrong state) or the person said no
    /// (then <paramref name="error"/> says so).
    /// </summary>
    public string? CodeFrom(string redirect, out string? error)
    {
        error = null;
        var query = ParseQuery(new Uri(redirect).Query);
        if (query.TryGetValue("error", out var e))
        {
            error = e == "access_denied" ? "You chose Deny, so the app isn't signed in." : $"Sign-in failed ({e}).";
            return null;
        }
        if (!query.TryGetValue("state", out var state) || state != State)
        {
            error = "That sign-in answer wasn't for this window; try again.";
            return null;
        }
        return query.TryGetValue("code", out var code) ? code : null;
    }

    internal static Dictionary<string, string> ParseQuery(string query)
    {
        var found = new Dictionary<string, string>();
        foreach (var pair in query.TrimStart('?').Split('&', StringSplitOptions.RemoveEmptyEntries))
        {
            var parts = pair.Split('=', 2);
            found[Uri.UnescapeDataString(parts[0])] = parts.Length > 1 ? Uri.UnescapeDataString(parts[1].Replace('+', ' ')) : "";
        }
        return found;
    }

    private static string Base64Url(byte[] bytes) =>
        Convert.ToBase64String(bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_');
}
