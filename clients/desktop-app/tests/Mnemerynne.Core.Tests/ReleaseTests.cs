using System.Net;
using Mnemerynne.Core;

namespace Mnemerynne.Core.Tests;

/// <summary>Updates from GitHub releases: the newest checkable desktop release is offered, nothing else.</summary>
public class ReleaseTests
{
    private const string Sha = "c13800608f1c27b6ca3052519e89223caf9d3fa105e81d572d440c78db3dabd8";

    private static string Asset(string name, string? digest = null, string url = "https://github.com/dl/") =>
        $$"""{"name":"{{name}}","size":64293006,"browser_download_url":"{{url}}{{name}}"{{(digest is null ? "" : $",\"digest\":\"{digest}\"")}}}""";

    private static string Release(string tag, string assets, bool draft = false, bool pre = false) =>
        $$"""{"tag_name":"{{tag}}","draft":{{(draft ? "true" : "false")}},"prerelease":{{(pre ? "true" : "false")}},"html_url":"https://github.com/r/{{tag}}","assets":[{{assets}}]}""";

    private static FakeServer GitHub(string releasesJson, string? sums = null) => new()
    {
        Answer = r => r.RequestUri!.AbsolutePath.EndsWith("/releases")
            ? FakeServer.Json(releasesJson)
            : sums is not null ? new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(sums) }
            : new HttpResponseMessage(HttpStatusCode.NotFound),
    };

    [Fact]
    public async Task The_newest_desktop_release_is_offered_with_githubs_own_hash()
    {
        var json = "[" + string.Join(",",
            Release("desktop-v0.10.0", Asset("Mnemerynne-Desktop-Setup-0.10.0.exe", "sha256:" + Sha)),
            Release("desktop-v0.9.0", Asset("Mnemerynne-Desktop-Setup-0.9.0.exe", "sha256:" + new string('a', 64))),
            Release("v5.0.0", Asset("Mnemerynne-Desktop-Setup-5.0.0.exe", "sha256:" + Sha)),          // not a desktop tag
            Release("desktop-v0.11.0", Asset("Mnemerynne-Desktop-Setup-0.11.0.exe", "sha256:" + Sha), draft: true),
            Release("desktop-v0.12.0", Asset("Mnemerynne-Desktop-Setup-0.12.0.exe", "sha256:" + Sha), pre: true)) + "]";
        var server = GitHub(json);
        var got = await GitHubReleases.LatestAsync(new HttpClient(server));
        Assert.NotNull(got);
        Assert.Equal("0.10.0", got.Version);                 // numbers, not text: 0.10 beats 0.9
        Assert.Equal(Sha, got.Sha256);
        Assert.EndsWith("Mnemerynne-Desktop-Setup-0.10.0.exe", got.Url);
        Assert.Contains("GET /repos/AELovelace/erinne/releases?per_page=30 ", server.Asked[0]);
    }

    [Fact]
    public async Task A_sha256sums_file_works_when_github_has_no_digest()
    {
        var json = "[" + Release("desktop-v0.2.0", Asset("Mnemerynne-Desktop-Setup-0.2.0.exe") + "," + Asset("SHA256SUMS")) + "]";
        var got = await GitHubReleases.LatestAsync(new HttpClient(GitHub(json, $"{Sha.ToUpperInvariant()}  Mnemerynne-Desktop-Setup-0.2.0.exe\n")));
        Assert.Equal(Sha, got!.Sha256);
    }

    [Fact]
    public async Task A_release_that_cant_be_checked_is_never_offered()
    {
        var json = "[" + string.Join(",",
            Release("desktop-v0.3.0", Asset("Mnemerynne-Desktop-Setup-0.3.0.exe")),                     // no hash anywhere
            Release("desktop-v0.2.0", Asset("Mnemerynne-Desktop-Setup-0.2.0.exe", "sha256:" + Sha))) + "]";
        var got = await GitHubReleases.LatestAsync(new HttpClient(GitHub(json)));
        Assert.Equal("0.2.0", got!.Version);                 // falls back to the newest one it can check
    }

    [Fact]
    public async Task A_tag_and_file_name_that_disagree_are_ignored()
    {
        var json = "[" + Release("desktop-v0.4.0", Asset("Mnemerynne-Desktop-Setup-0.3.9.exe", "sha256:" + Sha)) + "]";
        Assert.Null(await GitHubReleases.LatestAsync(new HttpClient(GitHub(json))));
    }
}
