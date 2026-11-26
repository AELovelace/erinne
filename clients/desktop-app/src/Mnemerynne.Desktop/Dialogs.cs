using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.Wpf;
using Mnemerynne.Core;

namespace Mnemerynne.Desktop;

/// <summary>
/// The sign-in window: the server's own sign-in and consent pages in a WebView2 that shares the profile's cookies
/// (so signing in here also signs the tabs in). When the server sends the browser back to the app's loopback
/// address, the window catches it before it loads, swaps the code for tokens and closes.
/// </summary>
public sealed class SignInWindow : Window
{
    private readonly ProfileSession _session;
    private readonly OAuthPkce _pkce = new();
    private readonly WebView2 _web = new();
    private readonly TaskCompletionSource<string?> _done = new();   // null = signed in; text = why not

    public SignInWindow(ProfileSession session, Window? owner)
    {
        _session = session;
        Title = $"Sign in to {session.Profile.Name}";
        Width = 520;
        Height = 720;
        Owner = owner;
        WindowStartupLocation = owner is null ? WindowStartupLocation.CenterScreen : WindowStartupLocation.CenterOwner;
        Content = _web;
        Loaded += async (_, _) => await StartAsync();
        Closed += (_, _) =>
        {
            if (_done.TrySetResult("The sign-in window was closed."))
                Log.Write($"[{session.Profile.Name}] sign-in window closed before finishing");
        };
    }

    /// <summary>Show it; returns null when signed in, or the reason it didn't happen.</summary>
    public Task<string?> RunAsync()
    {
        Show();
        return _done.Task;
    }

    private async Task StartAsync()
    {
        try
        {
            if (_session.Api.ClientId is null)
            {
                // First sign-in on this server: register the app as an OAuth client (kept in the profile).
                _session.Profile.ClientId = await _session.Api.RegisterAsync(Environment.MachineName);
                ((App)Application.Current).Store.Save();
            }
            await _web.EnsureCoreWebView2Async(await _session.EnvironmentAsync());
            _session.Prepare(_web.CoreWebView2);
            _web.CoreWebView2.NavigationStarting += OnNavigationStarting;
            _web.CoreWebView2.NewWindowRequested += (_, e) => { e.Handled = true; Shell.Open(e.Uri); };
            _web.CoreWebView2.Navigate(_pkce.AuthorizeUrl(_session.Profile.ServerUri, _session.Api.ClientId!));
        }
        catch (Exception e)
        {
            Log.Write($"sign-in didn't start: {e.Message}");
            _done.TrySetResult("The server couldn't be reached: " + e.Message);
            Close();
        }
    }

    private async void OnNavigationStarting(object? sender, CoreWebView2NavigationStartingEventArgs e)
    {
        if (!LinkRouter.IsAppRedirect(e.Uri))
        {
            Log.Write($"[{_session.Profile.Name}] sign-in window: {new Uri(e.Uri).AbsolutePath}");   // path only: no codes in the log
            return;                                        // the server's own sign-in and consent pages load as usual
        }
        e.Cancel = true;                                   // nothing listens on the loopback address: never load it
        var code = _pkce.CodeFrom(e.Uri, out var error);
        if (code is null)
        {
            _done.TrySetResult(error ?? "Sign-in didn't finish.");
            Close();
            return;
        }
        try
        {
            await _session.Api.SignInAsync(code, _pkce);
            var me = await _session.Api.MeAsync();
            _session.Profile.Username = me.Username;
            ((App)Application.Current).Store.Save();
            _done.TrySetResult(null);
        }
        catch (Exception ex)
        {
            Log.Write($"sign-in failed: {ex.Message}");
            _done.TrySetResult(ex.Message);
        }
        Close();
    }
}

/// <summary>
/// "Add a server": type its address (the work one is filled in), the app checks it's a Mnemerynne and picks up its
/// name and icon. Returns the new profile, or null when cancelled.
/// </summary>
public sealed class AddServerWindow : Window
{
    public const string DefaultServer = "https://erynne.eternalhotels.com";

    private readonly TextBox _address = new() { Text = DefaultServer, Margin = new Thickness(0, 6, 0, 0), Padding = new Thickness(6, 4, 6, 4) };
    private readonly TextBlock _message = new() { TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 8, 0, 0), Foreground = Brushes.Firebrick };
    private readonly Button _add = new() { Content = "Add", IsDefault = true, Padding = new Thickness(16, 4, 16, 4), Margin = new Thickness(0, 0, 8, 0) };
    public Profile? Result { get; private set; }

    public AddServerWindow(Window? owner, bool firstRun)
    {
        Title = firstRun ? "Welcome to Mnemerynne" : "Add a server";
        Width = 460;
        SizeToContent = SizeToContent.Height;
        ResizeMode = ResizeMode.NoResize;
        Owner = owner;
        WindowStartupLocation = owner is null ? WindowStartupLocation.CenterScreen : WindowStartupLocation.CenterOwner;
        var cancel = new Button { Content = "Cancel", IsCancel = true, Padding = new Thickness(16, 4, 16, 4) };
        _add.Click += async (_, _) => await AddAsync();
        var buttons = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right, Margin = new Thickness(0, 14, 0, 0) };
        buttons.Children.Add(_add);
        buttons.Children.Add(cancel);
        var body = new StackPanel { Margin = new Thickness(18) };
        body.Children.Add(new TextBlock
        {
            TextWrapping = TextWrapping.Wrap,
            Text = firstRun
                ? "Which Mnemerynne should the app open? You can add more later (for example work and home) from the server menu."
                : "The address of another Mnemerynne. It gets its own window, tabs, sign-in and drive.",
        });
        body.Children.Add(_address);
        body.Children.Add(_message);
        body.Children.Add(buttons);
        Content = body;
        Loaded += (_, _) => { _address.Focus(); _address.SelectAll(); };
    }

    private async Task AddAsync()
    {
        _message.Text = "";
        var server = ProfileStore.NormaliseServer(_address.Text);
        if (server is null)
        {
            _message.Text = "That isn't an address the app can use (it needs https, except for this computer).";
            return;
        }
        var app = (App)Application.Current;
        if (app.Store.Profiles.Any(p => p.Server.Equals(server, StringComparison.OrdinalIgnoreCase)))
        {
            _message.Text = "That server is already in the app.";
            return;
        }
        _add.IsEnabled = false;
        try
        {
            var probe = new Profile { Server = server };
            var info = await new ProfileSession(probe).Api.InfoAsync();   // is it a Mnemerynne? what's it called?
            probe.Name = info.ProductName;
            probe.IconUrl = info.Icon;
            Result = probe;
            DialogResult = true;
        }
        catch (Exception e)
        {
            _message.Text = e is ServerError { Status: System.Net.HttpStatusCode.NotFound }
                ? "That server doesn't have the desktop app's side yet (it needs an update)."
                : "Couldn't reach it: " + e.Message;
            _add.IsEnabled = true;
        }
    }
}
