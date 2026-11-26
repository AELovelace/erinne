using System.Text.Json;

namespace Mnemerynne.Core;

/// <summary>
/// One server the app knows (work, home, ...). Each has its own cookies, tabs, sign-in and drive, all in its own
/// folder under %LOCALAPPDATA%\Mnemerynne\profiles\&lt;Id&gt;.
/// </summary>
public sealed class Profile
{
    public string Id { get; set; } = Guid.NewGuid().ToString("N")[..12];   // folder name; never changes
    public string Server { get; set; } = "";                                // e.g. https://erynne.eternalhotels.com
    public string Name { get; set; } = "Mnemerynne";                        // the server's product name (its branding)
    public string? IconUrl { get; set; }                                    // the server's brand mark
    public string? ClientId { get; set; }                                   // our OAuth client on that server
    public string? Username { get; set; }                                   // who signed in (shown in Settings)
    public List<long> DriveLibraries { get; set; } = [];                    // library ids ticked for the drive
    public bool DrivePaused { get; set; }                                   // the tray's Pause sync
    public string? DriveFolder { get; set; }                                // e.g. C:\Users\Edith\Mnemerynne (picked once)

    public Uri ServerUri => new(Server);
}

/// <summary>profiles.json: every profile, and which one opens first.</summary>
public sealed class ProfileStore
{
    public List<Profile> Profiles { get; set; } = [];
    public string? Default { get; set; }               // Id of the profile the app opens with
    public bool StartWithWindows { get; set; } = true;  // HKCU Run (Settings); on, since the drive needs the app running
    public string? SkippedVersion { get; set; }         // "Later" on an update: don't ask again for this version

    private static readonly JsonSerializerOptions Json = new() { WriteIndented = true };

    /// <summary>The app's own folder: %LOCALAPPDATA%\Mnemerynne (overridable for tests).</summary>
    public static string Root { get; set; } =
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Mnemerynne");

    public static string FilePath => Path.Combine(Root, "profiles.json");

    /// <summary>A profile's own folder (WebView2 data, tabs.json, token, drive state).</summary>
    public static string FolderOf(Profile p) => Path.Combine(Root, "profiles", p.Id);

    public static ProfileStore Load()
    {
        try
        {
            if (File.Exists(FilePath))
                return JsonSerializer.Deserialize<ProfileStore>(File.ReadAllText(FilePath), Json) ?? new ProfileStore();
        }
        catch (Exception e) when (e is IOException or JsonException or UnauthorizedAccessException)
        {
            // a broken file: start again (the profile folders are still there, so nothing is lost but the list)
        }
        return new ProfileStore();
    }

    public void Save()
    {
        Directory.CreateDirectory(Root);
        var tmp = FilePath + ".tmp";
        File.WriteAllText(tmp, JsonSerializer.Serialize(this, Json));
        File.Move(tmp, FilePath, overwrite: true);       // swapped in whole: never half a file
    }

    public Profile? Find(string? id) => Profiles.FirstOrDefault(p => p.Id == id);

    /// <summary>The profile to open: the default, else the first; null when there's none yet (first run).</summary>
    public Profile? Startup() => Find(Default) ?? Profiles.FirstOrDefault();

    /// <summary>
    /// A server address as typed ("erynne.eternalhotels.com", "https://x/", "http://localhost:8000") made into
    /// the origin the app uses, or null when it can't be one. https is assumed when no scheme is given; plain http
    /// is only accepted for this computer (a dev server), since the token and cookies would travel in the clear.
    /// </summary>
    public static string? NormaliseServer(string typed)
    {
        typed = (typed ?? "").Trim();
        if (typed.Length == 0)
            return null;
        if (!typed.Contains("://"))
            typed = "https://" + typed;
        if (!Uri.TryCreate(typed, UriKind.Absolute, out var uri) || uri.Host.Length == 0)
            return null;
        if (uri.Scheme == "http" && !uri.IsLoopback)
            return null;
        if (uri.Scheme is not ("http" or "https"))
            return null;
        return uri.GetLeftPart(UriPartial.Authority);   // scheme://host[:port], no path
    }
}
