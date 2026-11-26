using System.Net.Http.Json;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace Mnemerynne.Core;

/// <summary>A desktop-app release found on GitHub: its version, the installer to download and its SHA-256.</summary>
public sealed record AppRelease(string Version, string Url, string Name, long Size, string Sha256, string Page);

/// <summary>
/// Where updates come from: the public GitHub repo's releases (Doll, 2026-11-26). Desktop releases are tagged
/// <c>desktop-v&lt;version&gt;</c> (ps\publish-release.ps1 makes them) and carry the installer
/// <c>Mnemerynne-Desktop-Setup-&lt;version&gt;.exe</c>. Its SHA-256 comes from GitHub's own record of the upload (the
/// asset's "digest"), or from a SHA256SUMS file in the same release; a release without either is never offered,
/// because the app won't run an installer it can't check. No token is needed: the repo is public.
/// </summary>
public static partial class GitHubReleases
{
    /// <summary>The repo the app updates from (owner/name). profiles.json "UpdateRepo" can point elsewhere (a fork).</summary>
    public const string DefaultRepo = "AELovelace/erinne";

    /// <summary>The tag prefix of desktop releases, so the repo's other releases (if it gets any) are ignored.</summary>
    public const string TagPrefix = "desktop-v";

    [GeneratedRegex(@"^Mnemerynne-Desktop-Setup-(\d+(?:\.\d+){1,3})\.exe$")]
    private static partial Regex InstallerName();

    [GeneratedRegex(@"^([0-9a-fA-F]{64})\s+\*?(.+)$")]
    private static partial Regex SumsLine();

    /// <summary>
    /// The newest published (not draft, not pre-release) desktop release that has an installer with a known
    /// SHA-256, or null when there's none. Throws HttpRequestException when GitHub can't be reached.
    /// </summary>
    public static async Task<AppRelease?> LatestAsync(HttpClient http, string repo = DefaultRepo, CancellationToken ct = default)
    {
        using var request = new HttpRequestMessage(HttpMethod.Get, $"https://api.github.com/repos/{repo}/releases?per_page=30");
        request.Headers.UserAgent.ParseAdd($"MnemerynneDesktop/{AppVersion.Current}");   // GitHub wants a user agent
        request.Headers.Accept.ParseAdd("application/vnd.github+json");
        using var r = await http.SendAsync(request, ct);
        r.EnsureSuccessStatusCode();
        var releases = await r.Content.ReadFromJsonAsync<JsonElement>(ct);
        var candidates = new List<(string Version, JsonElement Release, JsonElement Asset)>();
        foreach (var rel in releases.EnumerateArray())
        {
            if (Bool(rel, "draft") || Bool(rel, "prerelease"))
                continue;                                   // only finished, published releases
            var tag = Str(rel, "tag_name");
            if (!tag.StartsWith(TagPrefix, StringComparison.Ordinal))
                continue;
            var version = tag[TagPrefix.Length..];
            foreach (var asset in rel.GetProperty("assets").EnumerateArray())
            {
                var m = InstallerName().Match(Str(asset, "name"));
                if (m.Success && m.Groups[1].Value == version)
                    candidates.Add((version, rel, asset));   // the tag and the file name agree
            }
        }
        // Newest first, by version number (not by date: a fix to an old line mustn't look newest).
        foreach (var (version, rel, asset) in candidates.OrderByDescending(c => c.Version, Comparer<string>.Create(
                     (a, b) => AppVersion.IsNewer(a, b) ? 1 : AppVersion.IsNewer(b, a) ? -1 : 0)))
        {
            var name = Str(asset, "name");
            var sha = Digest(asset) ?? await SumsAsync(http, rel, name, ct);
            if (sha is null)
                continue;                                   // can't be checked: never offered
            return new AppRelease(version, Str(asset, "browser_download_url"), name,
                                  asset.TryGetProperty("size", out var size) ? size.GetInt64() : 0, sha, Str(rel, "html_url"));
        }
        return null;
    }

    /// <summary>GitHub's own record of an uploaded file's hash: "digest": "sha256:&lt;hex&gt;".</summary>
    private static string? Digest(JsonElement asset)
    {
        var d = Str(asset, "digest");
        return d.StartsWith("sha256:", StringComparison.OrdinalIgnoreCase) && d.Length == 7 + 64 ? d[7..].ToLowerInvariant() : null;
    }

    /// <summary>The hash from a SHA256SUMS file in the release ("&lt;hex&gt;  &lt;file name&gt;" lines), if there is one.</summary>
    private static async Task<string?> SumsAsync(HttpClient http, JsonElement rel, string name, CancellationToken ct)
    {
        foreach (var asset in rel.GetProperty("assets").EnumerateArray())
        {
            if (!Str(asset, "name").Equals("SHA256SUMS", StringComparison.OrdinalIgnoreCase))
                continue;
            var text = await http.GetStringAsync(Str(asset, "browser_download_url"), ct);
            foreach (var line in text.Split('\n'))
            {
                var m = SumsLine().Match(line.Trim());
                if (m.Success && m.Groups[2].Value.Trim() == name)
                    return m.Groups[1].Value.ToLowerInvariant();
            }
        }
        return null;
    }

    private static string Str(JsonElement e, string key) =>
        e.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() ?? "" : "";

    private static bool Bool(JsonElement e, string key) => e.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.True;
}
