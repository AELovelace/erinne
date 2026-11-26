using System.IO;
using System.Threading;
using System.Windows;
using Mnemerynne.Core;
using Mnemerynne.Drive;
using Forms = System.Windows.Forms;

namespace Mnemerynne.Desktop;

/// <summary>
/// The app: one copy per Windows user, a tray icon, one window per server (profile), and each profile's session.
///
/// Command line:
///   (none)               open the default server's window (or ask for a server on first run)
///   --background         start in the tray only (Windows' "start with Windows" uses this)
///   --unregister-drive   take every drive out of File Explorer and quit (the uninstaller runs this)
/// </summary>
public partial class App : Application
{
    public ProfileStore Store { get; private set; } = new();
    public bool Quitting { get; private set; }
    public static bool DevMode { get; private set; }   // MNEMERYNNE_DESKTOP_HOME is set: a trial run

    private readonly Dictionary<string, ProfileSession> _sessions = [];
    private readonly Dictionary<string, MainWindow> _windows = [];
    private Forms.NotifyIcon? _tray;
    private Mutex? _single;
    private EventWaitHandle? _wake;
    private bool _toldAboutTray;
    private Timer? _updateTimer;

    // One copy per Windows user. A trial run (MNEMERYNNE_DESKTOP_HOME) gets its own names, so it runs beside the
    // installed app instead of just bringing that one forward.
    private static string MutexName => DevMode ? @"Local\MnemerynneDesktop.Dev" : @"Local\MnemerynneDesktop";
    private static string WakeName => MutexName + ".Show";

    protected override async void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        // Trying a build without touching this Windows user's real setup: MNEMERYNNE_DESKTOP_HOME=<folder> keeps the
        // profiles, tokens, log and drive folders in there, and never changes "start with Windows".
        if (Environment.GetEnvironmentVariable("MNEMERYNNE_DESKTOP_HOME") is { Length: > 0 } devHome)
        {
            ProfileStore.Root = devHome;
            DevMode = true;
        }
        Store = ProfileStore.Load();
        if (e.Args.Contains("--unregister-drive"))
        {
            UnregisterAllDrives();                          // the uninstaller: out of Explorer, files left in place
            Shutdown();
            return;
        }

        // One copy per user: a second start just shows the first one's window.
        _single = new Mutex(true, MutexName, out var first);
        if (!first)
        {
            try { EventWaitHandle.OpenExisting(WakeName).Set(); } catch (WaitHandleCannotBeOpenedException) { }
            Shutdown();
            return;
        }
        _wake = new EventWaitHandle(false, EventResetMode.AutoReset, WakeName);
        new Thread(() =>
        {
            while (_wake.WaitOne())
                Dispatcher.Invoke(ShowDefault);             // someone started the app again: bring it forward
        }) { IsBackground = true }.Start();

        DispatcherUnhandledException += (_, ex) =>
        {
            Log.Write($"unexpected: {ex.Exception}");
            ex.Handled = true;                              // logged; the app keeps going
        };
        Log.Write($"start {AppVersion.Current} {string.Join(' ', e.Args)}");
        MakeTray();
        if (!DevMode)
            Autostart.Set(Store.StartWithWindows);          // keep the Run value pointing at this copy of the app

        if (Store.Profiles.Count == 0 && !AddServer(null, firstRun: true))
        {
            await QuitAsync();                              // first run, cancelled: nothing to do
            return;
        }
        foreach (var p in Store.Profiles)
            await StartDriveAsync(SessionFor(p));           // drives run whether or not a window is open
        if (!e.Args.Contains("--background"))
            ShowDefault();
        // Updates: checked at start (by each window) and every day after that.
        _updateTimer = new Timer(_ => Dispatcher.Invoke(() => { foreach (var w in _windows.Values) _ = w.CheckForUpdateAsync(manual: false); }),
                                 null, TimeSpan.FromHours(24), TimeSpan.FromHours(24));
    }

    public ProfileSession SessionFor(Profile p)
    {
        if (!_sessions.TryGetValue(p.Id, out var s))
        {
            s = new ProfileSession(p);
            s.Changed += () => Dispatcher.Invoke(RefreshTray);
            _sessions[p.Id] = s;
        }
        return s;
    }

    private async Task StartDriveAsync(ProfileSession s)
    {
        try
        {
            await s.SyncDriveAsync(Store);
        }
        catch (Exception e)
        {
            Log.Write($"[{s.Profile.Name}] drive not started: {e.Message}");   // shown in the toolbar as "Set up the drive"
        }
    }

    // ===================================================================================== windows
    private void ShowDefault()
    {
        if (Store.Startup() is { } p)
            OpenWindow(p);
    }

    /// <summary>The server's window: opened, or shown and brought forward if it's already there.</summary>
    public void OpenWindow(Profile p)
    {
        if (!_windows.TryGetValue(p.Id, out var w))
        {
            w = new MainWindow(SessionFor(p));
            _windows[p.Id] = w;
        }
        w.Show();
        if (w.WindowState == WindowState.Minimized)
            w.WindowState = WindowState.Normal;
        w.Activate();
        Store.Default = p.Id;                               // the next start opens the one used last
        Store.Save();
    }

    /// <summary>"Add a server": ask, save, open its window, offer to sign in. False when cancelled.</summary>
    public bool AddServer(Window? owner, bool firstRun = false)
    {
        var dialog = new AddServerWindow(owner, firstRun);
        if (dialog.ShowDialog() != true || dialog.Result is not { } profile)
            return false;
        Store.Profiles.Add(profile);
        Store.Default = profile.Id;
        Store.Save();
        OpenWindow(profile);
        _ = SignInAsync(SessionFor(profile), _windows[profile.Id]);   // straight on to signing in (it can be skipped)
        RefreshTray();
        return true;
    }

    public void ShowSettings(ProfileSession s, Window? owner)
    {
        new SettingsWindow(s, owner).ShowDialog();
        RefreshTray();
    }

    public async Task SignInAsync(ProfileSession s, Window? owner)
    {
        var problem = await new SignInWindow(s, owner).RunAsync();
        if (problem is null)
        {
            Log.Write($"[{s.Profile.Name}] signed in as {s.Profile.Username}");
            s.NotifyChanged();
            if (s.Profile.DriveLibraries.Count == 0)
                ShowSettings(s, owner);                     // first sign-in: pick the drive's libraries
            else
                await StartDriveAsync(s);
        }
        else if (owner is not null)
        {
            MessageBox.Show(owner, problem, "Sign in", MessageBoxButton.OK, MessageBoxImage.Information);
        }
    }

    public async Task SignOutAsync(ProfileSession s, Window owner)
    {
        var sure = MessageBox.Show(owner,
            $"Sign the app out of {s.Profile.Name}? The drive leaves File Explorer (files already downloaded stay in " +
            $"{s.Profile.DriveFolder ?? "its folder"}), and the tabs keep working until the site's own sign-in runs out.",
            "Sign out", MessageBoxButton.OKCancel, MessageBoxImage.Question);
        if (sure != MessageBoxResult.OK)
            return;
        await s.SignOutAsync();
        Store.Save();
        Log.Write($"[{s.Profile.Name}] signed out");
    }

    /// <summary>The first time a window hides to the tray, say so (people otherwise think it quit).</summary>
    public void NoteHiddenToTray()
    {
        if (_toldAboutTray || _tray is null)
            return;
        _toldAboutTray = true;
        _tray.ShowBalloonTip(4000, "Still running", "Mnemerynne keeps the drive in sync from here. Right-click the icon to quit.", Forms.ToolTipIcon.Info);
    }

    // ======================================================================================== tray
    private void MakeTray()
    {
        _tray = new Forms.NotifyIcon
        {
            Icon = new System.Drawing.Icon(GetResourceStream(new Uri("pack://application:,,,/Assets/app.ico")).Stream),
            Text = "Mnemerynne",
            Visible = true,
        };
        _tray.DoubleClick += (_, _) => ShowDefault();
        RefreshTray();
    }

    /// <summary>The tray menu: each server (open, drive status, open drive, pause), then Quit.</summary>
    private void RefreshTray()
    {
        if (_tray is null)
            return;
        var menu = new Forms.ContextMenuStrip();
        foreach (var p in Store.Profiles)
        {
            var s = SessionFor(p);
            menu.Items.Add($"Open {p.Name}", null, (_, _) => OpenWindow(p));
            if (s.Drive is { } d)
            {
                menu.Items.Add(new Forms.ToolStripMenuItem($"   Drive: {(d.Paused ? "paused" : d.Status)}") { Enabled = false });
                menu.Items.Add("   Open the drive folder", null, (_, _) => Shell.Open(d.Folder));
                menu.Items.Add(d.Paused ? "   Resume sync" : "   Pause sync", null, (_, _) =>
                {
                    d.Paused = !d.Paused;
                    p.DrivePaused = d.Paused;
                    Store.Save();
                    RefreshTray();
                });
            }
            menu.Items.Add(new Forms.ToolStripSeparator());
        }
        menu.Items.Add("Quit", null, async (_, _) => await QuitAsync());
        var old = _tray.ContextMenuStrip;
        _tray.ContextMenuStrip = menu;
        old?.Dispose();
        _tray.Text = Store.Profiles.FirstOrDefault() is { } first && SessionFor(first).Drive is { } drive
            ? $"Mnemerynne · {Trim(drive.Status, 40)}" : "Mnemerynne";
    }

    private static string Trim(string s, int n) => s.Length <= n ? s : s[..n];

    // ======================================================================================= quit
    public async Task QuitAsync()
    {
        if (Quitting)
            return;
        Quitting = true;
        foreach (var w in _windows.Values.ToList())
            w.Close();                                       // saves each window's tabs
        foreach (var s in _sessions.Values)
            await s.DisposeAsync();                          // drives disconnect cleanly
        _updateTimer?.Dispose();
        if (_tray is not null)
        {
            _tray.Visible = false;
            _tray.Dispose();
        }
        Log.Write("quit");
        Shutdown();
    }

    /// <summary>For the uninstaller: unregister every profile's drive. Files already downloaded are left alone.</summary>
    private void UnregisterAllDrives()
    {
        foreach (var p in Store.Profiles.Where(p => p.DriveFolder is not null))
        {
            try
            {
                DriveService.Unregister(p.DriveFolder!, p.Id);
                Log.Write($"[{p.Name}] drive unregistered (uninstall)");
            }
            catch (Exception e)
            {
                Log.Write($"[{p.Name}] couldn't unregister the drive: {e.Message}");
            }
        }
        if (!DevMode)
            Autostart.Set(false);
    }

    protected override void OnExit(ExitEventArgs e)
    {
        _single?.Dispose();
        _wake?.Dispose();
        base.OnExit(e);
    }
}
