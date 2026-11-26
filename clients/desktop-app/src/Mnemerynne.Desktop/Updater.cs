using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Windows;
using Mnemerynne.Core;

namespace Mnemerynne.Desktop;

/// <summary>
/// Updates, "ask first" (Doll's choice): the app checks the server's newest build (GET /api/desktop/info), and
/// when there's a newer one it offers it in a banner. Nothing is downloaded or installed until the person clicks.
/// Then: download (with the app's token), check its SHA-256 against what the server announced, run the installer
/// quietly, and quit so it can replace the files; the installer starts the app again.
/// </summary>
public static class Updater
{
    /// <summary>The newer build, or null (none, same version, skipped with "Later", or the server can't be reached).</summary>
    public static async Task<ServerInfo?> CheckAsync(ProfileSession session, string? skipped)
    {
        try
        {
            var info = await session.Api.InfoAsync();
            if (info.Download is null || !AppVersion.IsNewer(info.Latest, AppVersion.Current) || info.Latest == skipped)
                return null;
            return info;
        }
        catch (Exception e) when (e is HttpRequestException or ServerError or TaskCanceledException)
        {
            Log.Write($"update check failed: {e.Message}");
            return null;
        }
    }

    public static async Task InstallAsync(ProfileSession session, ServerInfo info, Window owner)
    {
        var build = info.Download!;
        var path = Path.Combine(Path.GetTempPath(), build.Name);
        try
        {
            if (session.Api.SignedIn)
                await session.Api.DownloadToAsync(build.Url, path);
            else
            {
                Shell.Open(build.Url);                     // not signed in: the browser downloads it with the web sign-in
                return;
            }
            if (!Download.Matches(path, build.Sha256))
            {
                File.Delete(path);
                MessageBox.Show(owner, "The downloaded update didn't match what the server announced, so it wasn't installed. Try again later.",
                                "Update", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }
            // /SILENT: progress only, no questions. /RELAUNCH=1: the installer starts the app again when it's done.
            Process.Start(new ProcessStartInfo(path, "/SILENT /SUPPRESSMSGBOXES /NORESTART /RELAUNCH=1") { UseShellExecute = true });
            Log.Write($"installing update {info.Latest}");
            await ((App)Application.Current).QuitAsync();
        }
        catch (Exception e) when (e is HttpRequestException or ServerError or SignInNeeded or IOException or System.ComponentModel.Win32Exception)
        {
            Log.Write($"update failed: {e.Message}");
            MessageBox.Show(owner, "The update couldn't be downloaded: " + e.Message, "Update", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }
}
