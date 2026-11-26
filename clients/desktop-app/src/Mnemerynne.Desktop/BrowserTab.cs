using System.ComponentModel;
using System.IO;
using System.Runtime.CompilerServices;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.Wpf;
using Mnemerynne.Core;

namespace Mnemerynne.Desktop;

/// <summary>
/// One tab: a WebView2 showing a page of the server, plus what the tab strip shows (title, icon, loading).
/// The link rules (LinkRouter) decide what stays here, what opens a new tab and what goes to the browser.
/// </summary>
public sealed class BrowserTab : INotifyPropertyChanged
{
    private readonly ProfileSession _session;
    private readonly Action<string> _openTab;           // MainWindow: open this address in a new tab
    private string _title = "New tab";
    private ImageSource? _icon;
    private bool _loading;
    private string? _intended;                           // where the tab was going when the server sent it to /login
    private DateTime _lastTicket = DateTime.MinValue;    // one silent sign-in a minute at most (no loops)

    public WebView2 View { get; } = new();
    public string Url => View.Source?.ToString() ?? _startUrl;
    private readonly string _startUrl;

    public string Title { get => _title; private set => Set(ref _title, value); }
    public ImageSource? Icon { get => _icon; private set => Set(ref _icon, value); }
    public bool Loading { get => _loading; private set => Set(ref _loading, value); }
    private bool _active;
    public bool Active { get => _active; set => Set(ref _active, value); }   // the showing tab (the strip highlights it)

    public event PropertyChangedEventHandler? PropertyChanged;
    public event Action? NavigationChanged;               // back/forward/address need redrawing

    public BrowserTab(ProfileSession session, string url, Action<string> openTab, string? title = null)
    {
        _session = session;
        _openTab = openTab;
        _startUrl = url;
        if (title is not null)
            _title = title;
    }

    private void Set<T>(ref T field, T value, [CallerMemberName] string? name = null)
    {
        field = value;
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
    }

    /// <summary>Start the browser engine for this tab (with the profile's cookies) and load its page.</summary>
    public async Task StartAsync()
    {
        await View.EnsureCoreWebView2Async(await _session.EnvironmentAsync());
        var web = View.CoreWebView2;
        _session.Prepare(web);
        web.NewWindowRequested += OnNewWindow;
        web.NavigationStarting += OnNavigationStarting;
        web.NavigationCompleted += OnNavigationCompleted;
        web.DocumentTitleChanged += (_, _) => Title = string.IsNullOrWhiteSpace(web.DocumentTitle) ? Url : web.DocumentTitle;
        web.FaviconChanged += async (_, _) => await LoadIconAsync(web);
        web.HistoryChanged += (_, _) => NavigationChanged?.Invoke();
        web.SourceChanged += (_, _) => NavigationChanged?.Invoke();
        web.Navigate(_startUrl);
    }

    /// <summary>target=_blank, window.open, Ctrl/middle-click: a new app tab for the server, the browser for anything else.</summary>
    private void OnNewWindow(object? sender, CoreWebView2NewWindowRequestedEventArgs e)
    {
        e.Handled = true;                                 // never a separate WebView2 popup window
        switch (_session.Links.Route(e.Uri, newWindow: true))
        {
            case LinkTarget.NewTab:
                _openTab(e.Uri);
                break;
            case LinkTarget.External:
                Shell.Open(e.Uri);
                break;
        }
    }

    private void OnNavigationStarting(object? sender, CoreWebView2NavigationStartingEventArgs e)
    {
        if (LinkRouter.IsAppRedirect(e.Uri))
        {
            // The app's own sign-in answer, landing in a tab (someone approved here instead of in the sign-in
            // window): never load it, never hand it to the browser. The sign-in window starts the sign-in again.
            e.Cancel = true;
            View.CoreWebView2.Navigate(new Uri(_session.Profile.ServerUri, "/").ToString());
            return;
        }
        var route = _session.Links.Route(e.Uri, newWindow: false);
        if (route == LinkTarget.External)
        {
            e.Cancel = true;                              // other sites open in the system browser
            Shell.Open(e.Uri);
            return;
        }
        if (route == LinkTarget.Blocked)
        {
            e.Cancel = true;
            return;
        }
        Loading = true;
        if (Uri.TryCreate(e.Uri, UriKind.Absolute, out var u) && _session.Links.IsServer(u)
            && u.AbsolutePath is not ("/login" or "/desktop/signin"))
            _intended = u.PathAndQuery;                   // remembered in case the server sends this tab to sign in
    }

    private async void OnNavigationCompleted(object? sender, CoreWebView2NavigationCompletedEventArgs e)
    {
        Loading = false;
        NavigationChanged?.Invoke();
        // Landed on the sign-in page (the day-long web session ran out): sign the tab in with the app's token.
        if (View.Source is { } here && _session.Links.IsServer(here) && here.AbsolutePath == "/login"
            && _session.Api.SignedIn && DateTime.UtcNow - _lastTicket > TimeSpan.FromMinutes(1))
        {
            _lastTicket = DateTime.UtcNow;
            try
            {
                View.CoreWebView2.Navigate(await _session.Api.SigninUrlAsync(_intended ?? "/"));
                Log.Write($"[{_session.Profile.Name}] signed a tab in again (web session had run out)");
            }
            catch (Exception ex)
            {
                Log.Write($"[{_session.Profile.Name}] silent sign-in failed: {ex.Message}");   // the normal form stays
            }
        }
    }

    private async Task LoadIconAsync(CoreWebView2 web)
    {
        try
        {
            using var stream = await web.GetFaviconAsync(CoreWebView2FaviconImageFormat.Png);
            if (stream is null || stream.Length == 0)
                return;
            var copy = new MemoryStream();
            await stream.CopyToAsync(copy);
            copy.Position = 0;
            var image = new BitmapImage();
            image.BeginInit();
            image.CacheOption = BitmapCacheOption.OnLoad;
            image.StreamSource = copy;
            image.EndInit();
            image.Freeze();
            Icon = image;
        }
        catch (Exception e) when (e is IOException or NotSupportedException or InvalidOperationException)
        {
            // no icon: the tab shows without one
        }
    }

    public bool CanGoBack => View.CoreWebView2?.CanGoBack ?? false;
    public bool CanGoForward => View.CoreWebView2?.CanGoForward ?? false;
    public void Back() => View.CoreWebView2?.GoBack();
    public void Forward() => View.CoreWebView2?.GoForward();
    public void Reload() => View.CoreWebView2?.Reload();
    public void Navigate(string url) => View.CoreWebView2?.Navigate(url);

    public SavedTab Save() => new(Url, Title);

    public void Close()
    {
        View.Dispose();                                   // frees the page's process share
    }
}
