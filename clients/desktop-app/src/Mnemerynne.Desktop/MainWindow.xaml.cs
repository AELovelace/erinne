using System.Collections.ObjectModel;
using System.ComponentModel;
using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media.Imaging;
using Mnemerynne.Core;

namespace Mnemerynne.Desktop;

/// <summary>
/// One window of tabs for one server. Tabs behave like a browser's: Ctrl+T, Ctrl+W, Ctrl+Shift+T, Ctrl+Tab,
/// Ctrl+1..9, Alt+←/→, F5, middle-click to close, drag to reorder; they come back when the app restarts.
/// Like Edge, the tabs are the title bar: there's no separate title bar and no address bar.
/// </summary>
public partial class MainWindow : Window
{
    public ProfileSession Session { get; }
    private readonly ObservableCollection<BrowserTab> _tabs = [];
    private readonly ClosedTabs _closed = new();
    private int _active = -1;
    private string SessionFile => Path.Combine(Session.Folder, "tabs.json");
    private App App => (App)Application.Current;

    private enum BannerKind { None, SignIn, Update }
    private BannerKind _banner;
    private ServerInfo? _update;                          // the newer build the banner offers

    public MainWindow(ProfileSession session)
    {
        Session = session;
        InitializeComponent();
        TabStrip.ItemsSource = _tabs;
        Session.Changed += () => Dispatcher.Invoke(RefreshChrome);   // drive status, signed in or out
        Loaded += async (_, _) => await OnLoadedAsync();
    }

    private async Task OnLoadedAsync()
    {
        RefreshChrome();
        var saved = TabSession.Load(SessionFile, Session.Profile.ServerUri);   // last time's tabs, or the start page
        foreach (var t in saved.Tabs)
            await AddTabAsync(t.Url, t.Title, activate: false);
        Select(saved.Active);
        if (!Session.Api.SignedIn)
            ShowBanner(BannerKind.SignIn, $"Sign the app in to {Session.Profile.Name} to keep your tabs signed in and use the drive in File Explorer.", "Sign in");
        _ = CheckForUpdateAsync(manual: false);
    }

    // =========================================================================================== tabs
    /// <summary>Open a tab (at the end, or just after the showing one when a page asked for it).</summary>
    public async Task AddTabAsync(string url, string? title = null, bool activate = true, bool besideActive = false)
    {
        var tab = new BrowserTab(Session, url, u => _ = AddTabAsync(u, activate: true, besideActive: true), title);
        tab.NavigationChanged += () => { if (_active >= 0 && _tabs[_active] == tab) RefreshToolbar(); SaveSession(); };
        tab.PropertyChanged += (_, e) => { if (e.PropertyName == nameof(BrowserTab.Title) && IsShowing(tab)) Title = $"{tab.Title} · {Session.Profile.Name}"; };
        var index = besideActive && _active >= 0 ? _active + 1 : _tabs.Count;
        _tabs.Insert(index, tab);
        if (_active >= index)
            _active++;                                     // the showing tab moved one to the right
        tab.View.Visibility = Visibility.Collapsed;
        PagesHost.Children.Add(tab.View);
        if (activate)
            Select(index);
        try
        {
            await tab.StartAsync();
        }
        catch (Exception e)
        {
            Log.Write($"tab didn't start: {e}");
            MessageBox.Show(this, "The page couldn't open. Is the Microsoft Edge WebView2 Runtime installed?\n\n" + e.Message,
                            "Mnemerynne", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
        SaveSession();
    }

    private bool IsShowing(BrowserTab tab) => _active >= 0 && _active < _tabs.Count && _tabs[_active] == tab;

    /// <summary>Show one tab; hide the others (they keep running, as browser tabs do).</summary>
    private void Select(int index)
    {
        if (index < 0 || index >= _tabs.Count)
            return;
        _active = index;
        for (var i = 0; i < _tabs.Count; i++)
        {
            _tabs[i].Active = i == index;
            _tabs[i].View.Visibility = i == index ? Visibility.Visible : Visibility.Collapsed;
        }
        var tab = _tabs[index];
        Title = $"{tab.Title} · {Session.Profile.Name}";
        RefreshToolbar();
        tab.View.Focus();
        SaveSession();
    }

    private void CloseTab(int index)
    {
        if (index < 0 || index >= _tabs.Count)
            return;
        var tab = _tabs[index];
        _closed.Push(tab.Save());                          // Ctrl+Shift+T brings it back
        var next = TabMath.AfterClose(index, _active, _tabs.Count);
        _tabs.RemoveAt(index);
        PagesHost.Children.Remove(tab.View);
        tab.Close();
        if (next < 0)
        {
            _active = -1;
            _ = AddTabAsync(new Uri(Session.Profile.ServerUri, "/").ToString());   // never an empty window
            return;
        }
        _active = -1;
        Select(next);
    }

    private void SaveSession()
    {
        try
        {
            new TabSession { Tabs = _tabs.Select(t => t.Save()).ToList(), Active = Math.Max(_active, 0) }.Save(SessionFile);
        }
        catch (IOException e)
        {
            Log.Write($"couldn't save tabs: {e.Message}");
        }
    }

    private BrowserTab? Current => _active >= 0 && _active < _tabs.Count ? _tabs[_active] : null;

    // ================================================================== back / forward / reload
    // No address bar (Doll's call): the site's own flower menu is how you get around. Back and forward just
    // follow the showing tab.
    private void RefreshToolbar()
    {
        var tab = Current;
        BackButton.IsEnabled = tab?.CanGoBack ?? false;
        ForwardButton.IsEnabled = tab?.CanGoForward ?? false;
    }

    private void OnBack(object sender, RoutedEventArgs e) => Current?.Back();
    private void OnForward(object sender, RoutedEventArgs e) => Current?.Forward();
    private void OnReload(object sender, RoutedEventArgs e) => Current?.Reload();
    private void OnNewTab(object sender, RoutedEventArgs e) => _ = AddTabAsync(new Uri(Session.Profile.ServerUri, "/").ToString());

    // ============================================================= the window's own buttons
    // Windows' title bar is gone (WindowChrome in MainWindow.xaml), so minimise / maximise / close are ours.
    private void OnMinimise(object sender, RoutedEventArgs e) => WindowState = WindowState.Minimized;

    private void OnMaxRestore(object sender, RoutedEventArgs e) =>
        WindowState = WindowState == WindowState.Maximized ? WindowState.Normal : WindowState.Maximized;

    private void OnCloseWindow(object sender, RoutedEventArgs e) => Close();   // OnClosing hides it to the tray

    /// <summary>
    /// Maximised, Windows pushes the window's edges a few pixels past the screen; pad by that much so the top row
    /// isn't cut off. The maximise button shows "restore" while maximised.
    /// </summary>
    private void OnStateChanged(object? sender, EventArgs e)
    {
        var maximised = WindowState == WindowState.Maximized;
        var edge = SystemParameters.WindowResizeBorderThickness;
        RootGrid.Margin = maximised
            ? new Thickness(edge.Left + 2, edge.Top + 2, edge.Right + 2, edge.Bottom + 2)   // +2: the hidden frame's padding
            : new Thickness(0);
        MaxButton.Content = maximised ? "" : "";                 // Segoe MDL2: restore / maximise
        MaxButton.ToolTip = maximised ? "Restore" : "Maximise";
        System.Windows.Automation.AutomationProperties.SetName(MaxButton, maximised ? "Restore" : "Maximise");   // screen readers
    }

    /// <summary>The tabs take what room there is (scrolling sideways past that), leaving + and a bit of drag space.</summary>
    private void OnTabAreaSized(object sender, SizeChangedEventArgs e) =>
        StripScroller.MaxWidth = Math.Max(120, e.NewSize.Width - 48 - 60);

    // ====================================================================================== keyboard
    private void OnKeyDown(object sender, KeyEventArgs e)
    {
        var key = e.Key == Key.System ? e.SystemKey : e.Key;      // Alt+… arrives as Key.System
        var ctrl = Keyboard.Modifiers.HasFlag(ModifierKeys.Control);
        var shift = Keyboard.Modifiers.HasFlag(ModifierKeys.Shift);
        var alt = Keyboard.Modifiers.HasFlag(ModifierKeys.Alt);
        var handled = true;
        if (ctrl && key == Key.T && shift)
        {
            if (_closed.Pop() is { } back)
                _ = AddTabAsync(back.Url, back.Title);     // Ctrl+Shift+T: the last closed tab
        }
        else if (ctrl && key == Key.T) OnNewTab(this, e);
        else if (ctrl && (key == Key.W || key == Key.F4)) CloseTab(_active);
        else if (ctrl && key == Key.Tab) Select(TabMath.Cycle(_active, _tabs.Count, shift ? -1 : +1));
        else if (ctrl && key is >= Key.D1 and <= Key.D9) Select(TabMath.Numbered(key - Key.D0, _tabs.Count));
        else if (ctrl && key is >= Key.NumPad1 and <= Key.NumPad9) Select(TabMath.Numbered(key - Key.NumPad0, _tabs.Count));
        else if (alt && key == Key.Left) Current?.Back();
        else if (alt && key == Key.Right) Current?.Forward();
        else if (key == Key.F5 || (ctrl && key == Key.R)) Current?.Reload();
        else handled = false;
        e.Handled = handled;
    }

    // ===================================================================== mouse: select, close, drag
    private Point _dragStart;
    private BrowserTab? _dragTab;

    private static BrowserTab? TabOf(object sender) => (sender as FrameworkElement)?.DataContext as BrowserTab;

    private void OnTabMouseDown(object sender, MouseButtonEventArgs e)
    {
        if (TabOf(sender) is { } tab)
        {
            Select(_tabs.IndexOf(tab));
            _dragStart = e.GetPosition(this);
            _dragTab = tab;
        }
    }

    private void OnTabMouseMove(object sender, MouseEventArgs e)
    {
        if (_dragTab is null || e.LeftButton != MouseButtonState.Pressed)
            return;
        var moved = e.GetPosition(this) - _dragStart;
        if (Math.Abs(moved.X) < SystemParameters.MinimumHorizontalDragDistance)
            return;
        var tab = _dragTab;
        _dragTab = null;
        DragDrop.DoDragDrop((DependencyObject)sender, tab, DragDropEffects.Move);   // OnTabDrop finishes it
    }

    private void OnTabMouseUp(object sender, MouseButtonEventArgs e)
    {
        _dragTab = null;
        if (e.ChangedButton == MouseButton.Middle && TabOf(sender) is { } tab)
            CloseTab(_tabs.IndexOf(tab));                  // middle-click closes, as in a browser
    }

    private void OnTabClose(object sender, RoutedEventArgs e)
    {
        if (TabOf(sender) is { } tab)
            CloseTab(_tabs.IndexOf(tab));
        e.Handled = true;
    }

    private void OnTabDragOver(object sender, DragEventArgs e)
    {
        e.Effects = e.Data.GetDataPresent(typeof(BrowserTab)) ? DragDropEffects.Move : DragDropEffects.None;
        e.Handled = true;
    }

    private void OnTabDrop(object sender, DragEventArgs e)
    {
        if (e.Data.GetData(typeof(BrowserTab)) is not BrowserTab tab)
            return;
        var over = (e.OriginalSource as FrameworkElement)?.DataContext as BrowserTab;
        var from = _tabs.IndexOf(tab);
        var to = over is null ? _tabs.Count - 1 : _tabs.IndexOf(over);
        if (from < 0 || to < 0 || from == to)
            return;
        _active = TabMath.AfterMove(from, to, _active);
        _tabs.Move(from, to);
        SaveSession();
    }

    private void OnStripWheel(object sender, MouseWheelEventArgs e)
    {
        var scroller = (ScrollViewer)sender;
        scroller.ScrollToHorizontalOffset(scroller.HorizontalOffset - e.Delta);   // the wheel scrolls a long strip
        e.Handled = true;
    }

    // ==================================================================== the server menu and drive
    /// <summary>The server button's menu: other servers, settings, the drive, sign-in, updates, quit.</summary>
    private void OnServerMenu(object sender, RoutedEventArgs e)
    {
        var menu = new ContextMenu();
        foreach (var p in App.Store.Profiles)
        {
            var item = new MenuItem { Header = $"{p.Name} — {p.ServerUri.Host}", IsChecked = p.Id == Session.Profile.Id };
            item.Click += (_, _) => App.OpenWindow(p);     // that server's window (opened, or brought forward)
            menu.Items.Add(item);
        }
        Add(menu, "Add a server…", () => App.AddServer(this));
        menu.Items.Add(new Separator());
        if (Session.Drive is not null)
            Add(menu, "Open the drive in File Explorer", () => Shell.Open(Session.Drive.Folder));
        Add(menu, "Settings…", () => App.ShowSettings(Session, this));
        Add(menu, Session.Api.SignedIn ? $"Sign out{(Session.Profile.Username is { } u ? $" ({u})" : "")}" : "Sign in…",
            async () => { if (Session.Api.SignedIn) await App.SignOutAsync(Session, this); else await App.SignInAsync(Session, this); });
        Add(menu, "Check for updates", () => _ = CheckForUpdateAsync(manual: true));
        Add(menu, $"About (version {AppVersion.Current})", () =>
            MessageBox.Show(this, $"Mnemerynne Desktop {AppVersion.Current}\n{Session.Profile.ServerUri}\n\nLog: {Path.Combine(ProfileStore.Root, "desktop.log")}",
                            "About", MessageBoxButton.OK, MessageBoxImage.Information));
        menu.Items.Add(new Separator());
        Add(menu, "Quit", () => _ = App.QuitAsync());
        menu.PlacementTarget = (UIElement)sender;
        menu.IsOpen = true;

        static void Add(ContextMenu m, string header, Action click)
        {
            var item = new MenuItem { Header = header };
            item.Click += (_, _) => click();
            m.Items.Add(item);
        }
    }

    private void OnDriveButton(object sender, RoutedEventArgs e)
    {
        if (Session.Drive is { } d)
            Shell.Open(d.Folder);
        else
            App.ShowSettings(Session, this);               // no drive yet: Settings is where libraries are picked
    }

    /// <summary>Server name and icon, the drive's status: redrawn when sign-in or the drive changes.</summary>
    public void RefreshChrome()
    {
        ServerName.Text = Session.Profile.Name;
        if (Session.Profile.IconUrl is { } icon && ServerIcon.Source is null)
        {
            try { ServerIcon.Source = new BitmapImage(new Uri(icon)); }
            catch (Exception e) when (e is UriFormatException or NotSupportedException) { }
        }
        DriveStatus.Text = Session.Drive is { } d
            ? (d.Paused ? "☁ Drive paused" : $"☁ {d.Status}")
            : Session.Api.SignedIn ? "☁ Set up the drive" : "";
        if (Session.Api.SignedIn && _banner == BannerKind.SignIn)
            HideBanner();
    }

    // ======================================================================================= banner
    private void ShowBanner(BannerKind kind, string text, string action)
    {
        _banner = kind;
        BannerText.Text = text;
        BannerAction.Content = action;
        Banner.Visibility = Visibility.Visible;
    }

    private void HideBanner()
    {
        _banner = BannerKind.None;
        Banner.Visibility = Visibility.Collapsed;
    }

    private async void OnBannerAction(object sender, RoutedEventArgs e)
    {
        var kind = _banner;
        HideBanner();
        if (kind == BannerKind.SignIn)
            await App.SignInAsync(Session, this);
        else if (kind == BannerKind.Update && _update is not null)
            await Updater.InstallAsync(Session, _update, this);
    }

    private void OnBannerLater(object sender, RoutedEventArgs e)
    {
        if (_banner == BannerKind.Update && _update?.Latest is { } v)
        {
            App.Store.SkippedVersion = v;                  // "Later": not asked again for this version
            App.Store.Save();
        }
        HideBanner();
    }

    /// <summary>Ask the server for a newer build (ask first: Doll's choice). Daily on its own, or from the menu.</summary>
    public async Task CheckForUpdateAsync(bool manual)
    {
        var info = await Updater.CheckAsync(Session, manual ? null : App.Store.SkippedVersion);
        if (info is not null)
        {
            _update = info;
            ShowBanner(BannerKind.Update, $"Version {info.Latest} of the app is ready (you have {AppVersion.Current}).", "Update now");
        }
        else if (manual)
        {
            MessageBox.Show(this, $"You have the newest version ({AppVersion.Current}).", "Mnemerynne", MessageBoxButton.OK, MessageBoxImage.Information);
        }
    }

    // ======================================================================================= closing
    private void OnClosing(object? sender, CancelEventArgs e)
    {
        SaveSession();
        if (!App.Quitting)
        {
            e.Cancel = true;                               // closing hides to the tray: the drive keeps working
            Hide();
            App.NoteHiddenToTray();
            return;
        }
        foreach (var t in _tabs)
            t.Close();
    }
}
