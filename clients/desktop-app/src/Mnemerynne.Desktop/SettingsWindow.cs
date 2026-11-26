using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using Mnemerynne.Core;

namespace Mnemerynne.Desktop;

/// <summary>
/// One server's settings: who's signed in, which libraries are on the drive in File Explorer, pausing sync, and
/// whether the app starts with Windows (that one is for every server).
/// </summary>
public sealed class SettingsWindow : Window
{
    private readonly ProfileSession _session;
    private readonly StackPanel _libraries = new();
    private readonly CheckBox _paused = new() { Content = "Pause sync (nothing goes up or comes down until you resume)", Margin = new Thickness(0, 8, 0, 0) };
    private readonly CheckBox _autostart = new() { Content = "Start the app when I sign in to Windows (needed for the drive)", Margin = new Thickness(0, 4, 0, 0) };
    private readonly TextBlock _message = new() { TextWrapping = TextWrapping.Wrap, Foreground = Brushes.Firebrick, Margin = new Thickness(0, 8, 0, 0) };
    private readonly Button _save = new() { Content = "Save", IsDefault = true, Padding = new Thickness(16, 4, 16, 4), Margin = new Thickness(0, 0, 8, 0) };

    public SettingsWindow(ProfileSession session, Window? owner)
    {
        _session = session;
        Title = $"Settings · {session.Profile.Name}";
        Width = 520;
        SizeToContent = SizeToContent.Height;
        MaxHeight = 760;
        Owner = owner;
        WindowStartupLocation = owner is null ? WindowStartupLocation.CenterScreen : WindowStartupLocation.CenterOwner;
        var p = session.Profile;
        var body = new StackPanel { Margin = new Thickness(18) };
        body.Children.Add(Heading("Account"));
        body.Children.Add(new TextBlock
        {
            TextWrapping = TextWrapping.Wrap,
            Text = session.Api.SignedIn
                ? $"Signed in to {p.ServerUri.Host} as {p.Username ?? "you"}."
                : $"Not signed in to {p.ServerUri.Host}. The tabs still work with the site's own sign-in; the drive needs the app signed in (server menu → Sign in).",
        });
        body.Children.Add(Heading("Drive in File Explorer"));
        body.Children.Add(new TextBlock
        {
            TextWrapping = TextWrapping.Wrap,
            Text = $"Tick the libraries to show in {session.DriveFolder(((App)Application.Current).Store.Profiles)}. " +
                   "Files download when you open them; right-click → \"Always keep on this device\" keeps a copy. " +
                   "Unticking a library removes it from this computer only.",
        });
        body.Children.Add(new ScrollViewer { Content = _libraries, MaxHeight = 280, Margin = new Thickness(0, 8, 0, 0), VerticalScrollBarVisibility = ScrollBarVisibility.Auto });
        _paused.IsChecked = p.DrivePaused;
        body.Children.Add(_paused);
        body.Children.Add(Heading("Windows"));
        _autostart.IsChecked = App.DevMode ? app0().Store.StartWithWindows : Autostart.IsOn();
        body.Children.Add(_autostart);
        body.Children.Add(_message);
        var buttons = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right, Margin = new Thickness(0, 14, 0, 0) };
        _save.Click += async (_, _) => await SaveAsync();
        buttons.Children.Add(_save);
        buttons.Children.Add(new Button { Content = "Cancel", IsCancel = true, Padding = new Thickness(16, 4, 16, 4) });
        body.Children.Add(buttons);
        Content = body;
        Loaded += async (_, _) => await LoadLibrariesAsync();
    }

    private static App app0() => (App)Application.Current;

    private static TextBlock Heading(string text) =>
        new() { Text = text, FontWeight = FontWeights.SemiBold, FontSize = 15, Margin = new Thickness(0, 12, 0, 4) };

    private async Task LoadLibrariesAsync()
    {
        if (!_session.Api.SignedIn)
        {
            _libraries.Children.Add(new TextBlock { Text = "Sign in to pick libraries.", Foreground = Brushes.Gray });
            return;
        }
        try
        {
            var list = await _session.Api.LibrariesAsync();
            foreach (var lib in list.Libraries)
            {
                _libraries.Children.Add(new CheckBox
                {
                    Content = lib.CanWrite ? lib.Name : $"{lib.Name} (read-only)",
                    Tag = lib.Id,
                    IsChecked = _session.Profile.DriveLibraries.Contains(lib.Id),
                    Margin = new Thickness(0, 2, 0, 2),
                });
            }
        }
        catch (Exception e)
        {
            _message.Text = "Couldn't get the libraries: " + e.Message;
        }
    }

    private async Task SaveAsync()
    {
        _save.IsEnabled = false;
        _message.Text = "";
        var app = (App)Application.Current;
        var p = _session.Profile;
        if (_session.Api.SignedIn && _libraries.Children.OfType<CheckBox>().Any())
            p.DriveLibraries = _libraries.Children.OfType<CheckBox>().Where(c => c.IsChecked == true).Select(c => (long)c.Tag).ToList();
        p.DrivePaused = _paused.IsChecked == true;
        app.Store.StartWithWindows = _autostart.IsChecked == true;
        if (!App.DevMode)
            Autostart.Set(app.Store.StartWithWindows);   // a trial run never changes Windows' start-up list
        app.Store.Save();
        try
        {
            await _session.SyncDriveAsync(app.Store);          // starts, changes or stops the drive to match
            DialogResult = true;
        }
        catch (Exception e)
        {
            _message.Text = "The drive couldn't start: " + e.Message + " (details in the log; see About).";
            _save.IsEnabled = true;
        }
    }
}
