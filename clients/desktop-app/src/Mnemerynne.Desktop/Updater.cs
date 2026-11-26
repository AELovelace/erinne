using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Windows;
using Mnemerynne.Core;

namespace Mnemerynne.Desktop;

/// <summary>
/// Updates, "ask first" (Doll's choice), from the public GitHub repo's releases (GitHubReleases.cs). The app looks
/// for a newer desktop release (tag desktop-v&lt;version&gt;) at start and once a day, and offers it in a banner.
/// Nothing is downloaded or installed until the person clicks. Then: download the installer, check its SHA-256
/// against GitHub's record, run it quietly, and quit so it can replace the files; the installer starts the app
/// again. The same for every server: the app is one program, whichever servers it opens.
/// </summary>
public static class Updater
{
    private static readonly HttpClient Http = new() { Timeout = TimeSpan.FromMinutes(20) };   // a 60 MB installer, slowly

    /// <summary>The repo to update from: profiles.json "UpdateRepo", else Doll's (GitHubReleases.DefaultRepo).</summary>
    private static string Repo => ((App)Application.Current).Store.UpdateRepo is { Length: > 0 } r ? r : GitHubReleases.DefaultRepo;

    /// <summary>The newer release, or null (none, the same version, skipped with "Later", or GitHub can't be reached).</summary>
    public static async Task<AppRelease?> CheckAsync(string? skipped)
    {
        try
        {
            var latest = await GitHubReleases.LatestAsync(Http, Repo);
            if (latest is null || !AppVersion.IsNewer(latest.Version, AppVersion.Current) || latest.Version == skipped)
                return null;
            return latest;
        }
        catch (Exception e) when (e is HttpRequestException or TaskCanceledException or System.Text.Json.JsonException)
        {
            Log.Write($"update check failed: {e.Message}");   // offline or rate-limited: tomorrow's check tries again
            return null;
        }
    }

    public static async Task InstallAsync(AppRelease release, Window owner)
    {
        var path = Path.Combine(Path.GetTempPath(), release.Name);
        try
        {
            using (var r = await Http.GetAsync(release.Url, HttpCompletionOption.ResponseHeadersRead))
            {
                r.EnsureSuccessStatusCode();
                await using var file = File.Create(path);
                await r.Content.CopyToAsync(file);
            }
            if (!Download.Matches(path, release.Sha256))
            {
                File.Delete(path);
                MessageBox.Show(owner, "The downloaded update didn't match its published checksum, so it wasn't installed. Try again later.",
                                "Update", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }
            // /SILENT: progress only, no questions. /RELAUNCH=1: the installer starts the app again when it's done.
            Process.Start(new ProcessStartInfo(path, "/SILENT /SUPPRESSMSGBOXES /NORESTART /RELAUNCH=1") { UseShellExecute = true });
            Log.Write($"installing update {release.Version} from {release.Page}");
            await ((App)Application.Current).QuitAsync();
        }
        catch (Exception e) when (e is HttpRequestException or TaskCanceledException or IOException or System.ComponentModel.Win32Exception)
        {
            Log.Write($"update failed: {e.Message}");
            MessageBox.Show(owner, "The update couldn't be downloaded: " + e.Message, "Update", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }
}
