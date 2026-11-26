using System.IO;
using System.Net.Http;
using Microsoft.Web.WebView2.Core;
using Mnemerynne.Core;
using Mnemerynne.Core.Drive;
using Mnemerynne.Drive;

namespace Mnemerynne.Desktop;

/// <summary>
/// Everything one profile (one server) has while the app runs: its browser environment (cookies shared by all its
/// tabs and the sign-in window), its API client and tokens, and its drive. Windows and the tray share it.
/// </summary>
public sealed class ProfileSession : IAsyncDisposable
{
    public Profile Profile { get; }
    public ServerApi Api { get; }
    public LinkRouter Links { get; }
    public DriveService? Drive { get; private set; }
    public string Folder => ProfileStore.FolderOf(Profile);
    public event Action? Changed;                       // signed in or out, drive started or stopped: menus redraw

    private CoreWebView2Environment? _environment;
    private static readonly HttpClient Http = new() { Timeout = TimeSpan.FromMinutes(30) };   // big files take a while

    /// <summary>The user agent's extra word: the site hides its "Install" buttons when it sees it (front.py).</summary>
    public static string UserAgentTag => $"MnemerynneDesktop/{AppVersion.Current}";

    static ProfileSession()
    {
        Http.DefaultRequestHeaders.UserAgent.ParseAdd(UserAgentTag);
    }

    public ProfileSession(Profile profile)
    {
        Profile = profile;
        Links = new LinkRouter(profile.ServerUri);
        Api = new ServerApi(profile.ServerUri, Http, new DpapiVault(Path.Combine(Folder, "token.bin")), profile.ClientId);
    }

    /// <summary>
    /// The profile's WebView2 environment, made once: its own data folder, so each server keeps its own cookies
    /// and a work sign-in never meets a home one.
    /// </summary>
    public async Task<CoreWebView2Environment> EnvironmentAsync()
    {
        if (_environment is null)
        {
            var options = new CoreWebView2EnvironmentOptions { AllowSingleSignOnUsingOSPrimaryAccount = false };
            _environment = await CoreWebView2Environment.CreateAsync(null, Path.Combine(Folder, "webview"), options);
        }
        return _environment;
    }

    /// <summary>Settings every tab (and the sign-in window) gets once its WebView2 is ready.</summary>
    public void Prepare(CoreWebView2 web)
    {
        var s = web.Settings;
        if (!s.UserAgent.Contains("MnemerynneDesktop/"))
            s.UserAgent += " " + UserAgentTag;
        s.AreBrowserAcceleratorKeysEnabled = false;      // Ctrl+T/W/R, F5... are the app's (MainWindow), not the page's
        s.IsStatusBarEnabled = true;                      // shows where a link goes, as browsers do
        s.AreDefaultContextMenusEnabled = true;
        s.IsZoomControlEnabled = true;                    // Ctrl+wheel zoom, as in a browser
        s.IsPasswordAutosaveEnabled = false;              // the app signs itself in; no saved passwords
        s.IsGeneralAutofillEnabled = false;
        web.PermissionRequested += (_, e) =>
        {
            // Notifications (Rooms, Lumi reminders) are allowed for the server only; everything else: Windows' default.
            if (e.PermissionKind == CoreWebView2PermissionKind.Notifications
                && Uri.TryCreate(e.Uri, UriKind.Absolute, out var u) && Links.IsServer(u))
                e.State = CoreWebView2PermissionState.Allow;
        };
    }

    // ------------------------------------------------------------------------------- the drive
    /// <summary>Where this profile's drive lives: %USERPROFILE%\&lt;server's name&gt; (picked once, then kept).</summary>
    public string DriveFolder(IEnumerable<Profile> all)
    {
        if (Profile.DriveFolder is { } chosen)
            return chosen;
        var home = App.DevMode ? Path.Combine(ProfileStore.Root, "Drives")      // a trial run: beside its own data
                               : Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        var name = DriveNames.Safe(Profile.Name);
        // Two servers with one name (both "Mnemerynne"): the second gets its address too.
        if (all.Any(p => p.Id != Profile.Id && p.DriveFolder is { } f && Path.GetFileName(f).Equals(name, StringComparison.OrdinalIgnoreCase)))
            name = DriveNames.Safe($"{Profile.Name} ({Profile.ServerUri.Host})");
        Profile.DriveFolder = Path.Combine(home, name);
        return Profile.DriveFolder;
    }

    /// <summary>Start the drive when libraries are ticked and the app is signed in; stop it when not.</summary>
    public async Task SyncDriveAsync(ProfileStore store)
    {
        var want = Api.SignedIn && Profile.DriveLibraries.Count > 0;
        if (!want)
        {
            await StopDriveAsync();
            return;
        }
        if (Drive is not null)
        {
            await Drive.SetLibrariesAsync(Profile.DriveLibraries);   // ticked or unticked in Settings
            Drive.Paused = Profile.DrivePaused;
            Changed?.Invoke();
            return;
        }
        var folder = DriveFolder(store.Profiles);
        store.Save();
        var drive = new DriveService(Api, folder, Path.Combine(Folder, "drive.db"), Profile.Id, line => Log.Write($"[{Profile.Name}] {line}"));
        drive.StatusChanged += _ => Changed?.Invoke();
        try
        {
            await drive.StartAsync(Profile.Name, Environment.ProcessPath!, Profile.ServerUri, Profile.DriveLibraries);
            drive.Paused = Profile.DrivePaused;
            Drive = drive;
        }
        catch (Exception e)
        {
            Log.Write($"[{Profile.Name}] drive didn't start: {e}");
            await drive.DisposeAsync();
            throw;
        }
        Changed?.Invoke();
    }

    public async Task StopDriveAsync()
    {
        if (Drive is null)
            return;
        var d = Drive;
        Drive = null;
        await d.DisposeAsync();
        Changed?.Invoke();
    }

    /// <summary>Sign out: the server ends the connection; the drive stops and is unregistered (downloaded files stay).</summary>
    public async Task SignOutAsync()
    {
        var folder = Profile.DriveFolder;
        await StopDriveAsync();
        if (folder is not null)
            DriveService.Unregister(folder, Profile.Id);
        await Api.SignOutAsync();
        Profile.Username = null;
        Changed?.Invoke();
    }

    public void NotifyChanged() => Changed?.Invoke();

    public async ValueTask DisposeAsync() => await StopDriveAsync();
}
