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
    private readonly ObservableCollection<BrowserTab> _tabs = [];   // every tab, in strip order: places, then opened things
    private readonly ClosedTabs _closed = new();
    private int _active = -1;
    private string SessionFile => Path.Combine(Session.Folder, "tabs.json");
    private App App => (App)Application.Current;

    private enum BannerKind { None, SignIn, Update }
    private BannerKind _banner;
    private AppRelease? _update;                          // the newer GitHub release the banner offers
    private readonly ResourceDictionary _theme = ThemeColors.Defaults();   // the app's colours, from the site's theme

    public MainWindow(ProfileSession session)
    {
        Session = session;
        InitializeComponent();
        Resources.MergedDictionaries.Add(_theme);          // every DynamicResource colour in MainWindow.xaml comes from here
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
        tab.PageLoaded += () => { if (IsShowing(tab)) _ = RefreshFromPageAsync(); };   // its theme and menu
        tab.PropertyChanged += (_, e) =>
        {
            if (e.PropertyName == nameof(BrowserTab.Title) && IsShowing(tab))
                Title = tab.Title;
            if (e.PropertyName == nameof(BrowserTab.Kind))
                Regroup();                                 // it opened a file (or went back to a place): other side
        };
        var index = besideActive && _active >= 0 ? _active + 1 : _tabs.Count;
        _tabs.Insert(index, tab);
        if (_active >= index)
            _active++;                                     // the showing tab moved one to the right
        tab.View.Visibility = Visibility.Collapsed;
        PagesHost.Children.Add(tab.View);
        Regroup();                                         // into its group (this may move it)
        if (activate)
            Select(_tabs.IndexOf(tab));
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
        Title = tab.Title;                                 // the page's name only (no server name), like its tab
        RefreshToolbar();
        _ = RefreshFromPageAsync();
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
        Regroup();
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
    private void OnTabAreaSized(object sender, SizeChangedEventArgs e) => FitStrips(e.NewSize.Width);

    private const double TabWidest = 200, TabNarrowest = 90, TabGap = 2;   // a tab's width range, and its margins

    /// <summary>
    /// Fit the tabs in the row, as a browser does: they share what's left after + and a bit of drag space, shrinking
    /// (down to 90 px) before any has to scroll out of sight.
    /// </summary>
    private void FitStrips(double width)
    {
        if (width <= 0)
            return;
        var room = Math.Max(120, width - 48 - 40);                       // 48: the + button, 40: room to drag the window
        TabStrip.Tag = Math.Clamp(room / Math.Max(1, _tabs.Count) - TabGap, TabNarrowest, TabWidest);   // each tab's Width
        StripScroller.MaxWidth = room;
    }

    /// <summary>
    /// Keep the tabs sorted (TabKinds): the site's places first, then opened things, each kind in its own order. The
    /// showing tab stays showing wherever it moves.
    /// </summary>
    private void Regroup()
    {
        var showing = Current;
        var order = TabKinds.Grouped(_tabs.Select(t => t.Kind).ToList()).Select(i => _tabs[i]).ToList();
        for (var i = 0; i < order.Count; i++)
            if (!ReferenceEquals(_tabs[i], order[i]))
                _tabs.Move(_tabs.IndexOf(order[i]), i);
        _active = showing is null ? -1 : _tabs.IndexOf(showing);
        Dispatcher.BeginInvoke(() => FitStrips(TabArea.ActualWidth));   // a tab more or less: widths change
    }

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
        if (from < 0 || to < 0 || from == to || (over is not null && over.Kind != tab.Kind))
            return;                                        // a tab moves within its own group only
        _active = TabMath.AfterMove(from, to, _active);
        _tabs.Move(from, to);
        Regroup();
        SaveSession();
    }

    private void OnStripWheel(object sender, MouseWheelEventArgs e)
    {
        var scroller = (ScrollViewer)sender;
        scroller.ScrollToHorizontalOffset(scroller.HorizontalOffset - e.Delta);   // the wheel scrolls a long strip
        e.Handled = true;
    }

    // ============================================================ the two menus: pages and ⚙
    /// <summary>One line of the pages menu, as the site's own (hidden) flower menu has it.</summary>
    private sealed record NavLink(string Href, string Label, bool Current, string Badge);

    /// <summary>What the pages menu shows: the site's links, Messages (and its unread count), Help.</summary>
    private sealed record NavInfo(List<NavLink> Tabs, string? Help, string? MessagesHref, bool MessagesButton, string Unread);

    /// <summary>
    /// Runs in the showing tab: reads the site's own pages menu (_nav.html #nav-more, hidden in the app by
    /// erynne.css), so the app's menu always has exactly the pages this person has, with live badges.
    /// </summary>
    private const string NavScript = """
        (() => {
          const q = s => document.querySelector(s);
          const more = q('#nav-more');
          if (!more) return null;
          const tabs = [...more.querySelectorAll('nav.tabs a')].map(a => ({
            href: a.href,
            label: [...a.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim(),
            current: a.getAttribute('aria-current') === 'page',
            badge: (a.querySelector('.badge')?.textContent || '').trim() }));
          const notify = q('#notify-button');
          const unread = q('#nav-unread');
          return JSON.stringify({ tabs, help: more.querySelector('.help-link')?.href || null,
            messagesHref: notify && notify.tagName === 'A' ? notify.href : null,
            messagesButton: !!notify && notify.tagName === 'BUTTON',
            unread: unread && !unread.hidden ? unread.textContent.trim() : '' });
        })()
        """;

    private NavInfo? _nav;                                 // the last menu read from a page (kept for pages without one)

    /// <summary>The page's own JSON is camelCase (messagesButton), unlike the server's API (snake_case).</summary>
    private static readonly System.Text.Json.JsonSerializerOptions PageJson = new() { PropertyNameCaseInsensitive = true };

    /// <summary>The page's theme and menu, read again after the showing tab loads a page (or another tab shows).</summary>
    private async Task RefreshFromPageAsync()
    {
        if (Current is not { } tab)
            return;
        if (await tab.RunScriptAsync(ThemeColors.Script) is { } theme)
            ThemeColors.Apply(_theme, theme);              // the app wears the page's theme
        if (await tab.RunScriptAsync(NavScript) is { } navJson)
        {
            try
            {
                var got = System.Text.Json.JsonSerializer.Deserialize<NavInfo>(navJson, PageJson);   // the page's camelCase names
                if (got is { Tabs.Count: > 0 })
                    _nav = got;
            }
            catch (System.Text.Json.JsonException)
            {
                // not the menu we know: keep the last one
            }
        }
        // The badge on the name: unread messages plus proposals waiting (the site's flower showed these).
        var count = (int.TryParse(_nav?.Unread, out var u) ? u : 0)
                    + (_nav?.Tabs.Sum(t => int.TryParse(t.Badge, out var b) ? b : 0) ?? 0);
        NavBadge.Visibility = count > 0 ? Visibility.Visible : Visibility.Collapsed;
        NavBadgeText.Text = count > 99 ? "99+" : count.ToString();
    }

    /// <summary>A menu in Erynne's look (App.xaml), using this window's theme colours.</summary>
    private ContextMenu NewMenu(UIElement under)
    {
        var menu = new ContextMenu { Style = (Style)FindResource("ErynneMenu"), PlacementTarget = under,
                                     Placement = System.Windows.Controls.Primitives.PlacementMode.Bottom };
        menu.Resources.MergedDictionaries.Add(_theme);    // a menu is a pop-up of its own: give it the colours
        return menu;
    }

    /// <summary>One line in a menu; with no click it's a quiet heading ("Signed in as …").</summary>
    private MenuItem Item(ContextMenu menu, object header, Action? click, bool current = false, string? gesture = null)
    {
        var item = new MenuItem { Header = header, Style = (Style)FindResource("ErynneItem"), IsChecked = current,
                                  InputGestureText = gesture ?? "", IsEnabled = click is not null };
        if (click is not null)
            item.Click += (_, _) => click();
        menu.Items.Add(item);
        return item;
    }

    private void Line(ContextMenu menu) => menu.Items.Add(new Separator { Style = (Style)FindResource("ErynneSeparator") });

    /// <summary>A page's name with its count beside it (Proposals 2, Messages 5), the count in the accent colour.</summary>
    private static object WithBadge(string label, string badge)
    {
        if (string.IsNullOrWhiteSpace(badge))
            return label;
        var row = new StackPanel { Orientation = Orientation.Horizontal };
        row.Children.Add(new TextBlock { Text = label, VerticalAlignment = VerticalAlignment.Center });
        var pill = new Border { CornerRadius = new CornerRadius(8), Padding = new Thickness(6, 0, 6, 0), Margin = new Thickness(8, 0, 0, 0),
                                VerticalAlignment = VerticalAlignment.Center };
        pill.SetResourceReference(Border.BackgroundProperty, "Accent");
        var text = new TextBlock { Text = badge, FontSize = 11, FontWeight = FontWeights.Bold };
        text.SetResourceReference(TextBlock.ForegroundProperty, "Strip");
        pill.Child = text;
        row.Children.Add(pill);
        return row;
    }

    /// <summary>
    /// The server's name: the pages menu, and only that. A click goes there in this tab; Ctrl+click opens it in a
    /// new tab, as with a link.
    /// </summary>
    private async void OnPagesMenu(object sender, RoutedEventArgs e)
    {
        await RefreshFromPageAsync();                      // live: badges and the current page
        var menu = NewMenu((UIElement)sender);
        var server = Session.Profile.ServerUri;
        var links = _nav?.Tabs ?? [new(server.ToString(), "Ask", false, ""), new(new Uri(server, "/files").ToString(), "Files", false, ""),
                                   new(new Uri(server, "/wiki").ToString(), "Wiki", false, ""),
                                   new(new Uri(server, "/preferences").ToString(), "Preferences", false, "")];
        foreach (var link in links)
            Item(menu, WithBadge(link.Label, link.Badge), () => Go(link.Href), current: link.Current);
        Line(menu);
        if (_nav?.MessagesHref is { } inbox)
            Item(menu, WithBadge("Messages", _nav.Unread), () => Go(inbox));
        else if (_nav?.MessagesButton == true)
            Item(menu, WithBadge("Messages", _nav.Unread),
                 () => _ = Current?.RunScriptAsync("document.getElementById('notify-button').click()"));   // the site's pop-up
        Item(menu, "Help", () => _ = AddTabAsync(_nav?.Help ?? new Uri(server, "/help").ToString()), gesture: "new tab");
        menu.IsOpen = true;

        void Go(string href)
        {
            if (Keyboard.Modifiers.HasFlag(ModifierKeys.Control))
                _ = AddTabAsync(href);                      // Ctrl: a new tab, like Ctrl+clicking a link
            else
                Current?.Navigate(href);
        }
    }

    /// <summary>⚙ (right of the drive's status): servers, adding one, the drive, settings, sign-in, updates, quit.</summary>
    private void OnSystemMenu(object sender, RoutedEventArgs e)
    {
        var menu = NewMenu((UIElement)sender);
        menu.HorizontalOffset = -190;                      // opens towards the window, not off its right edge
        Item(menu, Session.Api.SignedIn ? $"Signed in as {Session.Profile.Username ?? "you"}" : "Not signed in", null);
        Line(menu);
        foreach (var p in App.Store.Profiles)
            Item(menu, $"{p.Name}  ·  {p.ServerUri.Host}", () => App.OpenWindow(p), current: p.Id == Session.Profile.Id);
        Item(menu, "Add a server…", () => App.AddServer(this));
        Line(menu);
        if (Session.Drive is not null)
            Item(menu, "Open the drive in File Explorer", () => Shell.Open(Session.Drive.Folder));
        Item(menu, "Settings…", () => App.ShowSettings(Session, this));
        Item(menu, Session.Api.SignedIn ? "Sign out" : "Sign in…",
             async () => { if (Session.Api.SignedIn) await App.SignOutAsync(Session, this); else await App.SignInAsync(Session, this); });
        Item(menu, "Check for updates", () => _ = CheckForUpdateAsync(manual: true));
        Item(menu, $"About (version {AppVersion.Current})", () =>
            MessageBox.Show(this, $"Mnemerynne Desktop {AppVersion.Current}\n{Session.Profile.ServerUri}\n\nLog: {Path.Combine(ProfileStore.Root, "desktop.log")}",
                            "About", MessageBoxButton.OK, MessageBoxImage.Information));
        Line(menu);
        Item(menu, "Quit", () => _ = App.QuitAsync());
        menu.IsOpen = true;
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
            await Updater.InstallAsync(_update, this);
    }

    private void OnBannerLater(object sender, RoutedEventArgs e)
    {
        if (_banner == BannerKind.Update && _update?.Version is { } v)
        {
            App.Store.SkippedVersion = v;                  // "Later": not asked again for this version
            App.Store.Save();
        }
        HideBanner();
    }

    /// <summary>Look for a newer release on GitHub (ask first: Doll's choice). At start, daily, or from ⚙ → Check for updates.</summary>
    public async Task CheckForUpdateAsync(bool manual)
    {
        var info = await Updater.CheckAsync(manual ? null : App.Store.SkippedVersion);
        if (info is not null)
        {
            _update = info;
            ShowBanner(BannerKind.Update, $"Version {info.Version} of the app is ready (you have {AppVersion.Current}).", "Update now");
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
