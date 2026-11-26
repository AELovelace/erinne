using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Mnemerynne.Core;

/// <summary>GET /api/desktop/info: what the server is, before signing in (web/desktop.py info).</summary>
public sealed record ServerInfo(string ProductName, string Server, string Icon, bool Enabled, string? Latest, BuildInfo? Download);

/// <summary>The newest installer the server offers.</summary>
public sealed record BuildInfo(string Url, string Name, long Size, string Sha256);

/// <summary>An access token (an hour) and the refresh token that gets the next one (90 days, single use).</summary>
public sealed record TokenSet(string Access, string Refresh, DateTimeOffset AccessExpires);

/// <summary>Where a profile's tokens are kept between runs (DPAPI-protected file in the app; memory in tests).</summary>
public interface ITokenVault
{
    TokenSet? Load();
    void Save(TokenSet? tokens);   // null forgets them (signed out)
}

/// <summary>The server said no. <see cref="Status"/> is the HTTP status; the message is the server's own words.</summary>
public sealed class ServerError(HttpStatusCode status, string message) : Exception(message)
{
    public HttpStatusCode Status { get; } = status;
}

/// <summary>Not signed in (no token, or the server ended the connection): the app shows its Sign in button.</summary>
public sealed class SignInNeeded(string message) : Exception(message);

/// <summary>
/// Everything the app asks a server: sign-in (OAuth), the tabs' sign-in tickets, the update feed and the drive.
/// One per profile. Thread-safe: the drive calls it from Explorer's callbacks while the window uses it too.
/// </summary>
public sealed class ServerApi
{
    public static readonly JsonSerializerOptions Json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,        // the server's names: product_name, sha256
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    private readonly Uri _server;
    private readonly HttpClient _http;
    private readonly ITokenVault _vault;
    private readonly SemaphoreSlim _refreshing = new(1, 1);   // one refresh at a time: a refresh token works once
    private TokenSet? _tokens;

    public string? ClientId { get; set; }                     // our OAuth client on this server (Profile.ClientId)

    public ServerApi(Uri server, HttpClient http, ITokenVault vault, string? clientId)
    {
        _server = server;
        _http = http;
        _vault = vault;
        ClientId = clientId;
        _tokens = vault.Load();
    }

    public bool SignedIn => _tokens is not null;

    private Uri At(string pathAndQuery) => new(_server, pathAndQuery);

    // ------------------------------------------------------------------ before signing in
    /// <summary>The server's name, icon and newest build (no sign-in needed).</summary>
    public async Task<ServerInfo> InfoAsync(CancellationToken ct = default)
    {
        using var r = await _http.GetAsync(At("/api/desktop/info"), ct);
        await ThrowIfFailed(r, ct);
        return (await r.Content.ReadFromJsonAsync<ServerInfo>(Json, ct))!;
    }

    /// <summary>Register the app as an OAuth client (once per profile; dynamic registration, RFC 7591).</summary>
    public async Task<string> RegisterAsync(string computerName, CancellationToken ct = default)
    {
        var body = new Dictionary<string, object>
        {
            ["redirect_uris"] = new[] { OAuthPkce.RedirectUri },
            ["client_name"] = $"Desktop app on {computerName}",   // what Preferences → Desktop app lists
            ["token_endpoint_auth_method"] = "none",              // a public client: PKCE instead of a secret
        };
        using var r = await _http.PostAsJsonAsync(At("/oauth/register"), body, ct);
        await ThrowIfFailed(r, ct);
        var got = await r.Content.ReadFromJsonAsync<JsonElement>(ct);
        ClientId = got.GetProperty("client_id").GetString();
        return ClientId!;
    }

    /// <summary>Swap the code from the sign-in window for tokens, and keep them.</summary>
    public async Task SignInAsync(string code, OAuthPkce pkce, CancellationToken ct = default)
    {
        var form = new Dictionary<string, string>
        {
            ["grant_type"] = "authorization_code",
            ["code"] = code,
            ["redirect_uri"] = OAuthPkce.RedirectUri,
            ["client_id"] = ClientId ?? throw new InvalidOperationException("Register first."),
            ["code_verifier"] = pkce.Verifier,
        };
        await TokenRequest(form, ct);
    }

    /// <summary>Sign out: the server ends the connection (all its tokens), and the app forgets them.</summary>
    public async Task SignOutAsync(CancellationToken ct = default)
    {
        var tokens = _tokens;
        Forget();
        if (tokens is null || ClientId is null)
            return;
        try
        {
            using var r = await _http.PostAsync(At("/oauth/revoke"), new FormUrlEncodedContent(new Dictionary<string, string>
            {
                ["token"] = tokens.Refresh, ["client_id"] = ClientId,
            }), ct);
        }
        catch (HttpRequestException)
        {
            // offline: the app has forgotten its tokens anyway; the connection lapses on the server in 90 days
        }
    }

    private void Forget()
    {
        _tokens = null;
        _vault.Save(null);
    }

    private async Task TokenRequest(Dictionary<string, string> form, CancellationToken ct)
    {
        using var r = await _http.PostAsync(At("/oauth/token"), new FormUrlEncodedContent(form), ct);
        if (!r.IsSuccessStatusCode)
        {
            var text = await r.Content.ReadAsStringAsync(ct);
            if (r.StatusCode is HttpStatusCode.BadRequest or HttpStatusCode.Unauthorized)
            {
                Forget();                                      // the grant is gone: sign in again
                throw new SignInNeeded("The app was signed out on the server; sign in again.");
            }
            throw new ServerError(r.StatusCode, text);
        }
        var got = await r.Content.ReadFromJsonAsync<JsonElement>(ct);
        var tokens = new TokenSet(got.GetProperty("access_token").GetString()!, got.GetProperty("refresh_token").GetString()!,
                                  DateTimeOffset.UtcNow.AddSeconds(got.GetProperty("expires_in").GetInt32() - 60));
        _vault.Save(tokens);                                   // saved before use: the old refresh token is spent now
        _tokens = tokens;
    }

    /// <summary>A valid access token, refreshed first if it's (nearly) expired or the server just refused it.</summary>
    private async Task<string> AccessAsync(bool force, CancellationToken ct)
    {
        var tokens = _tokens ?? throw new SignInNeeded("Not signed in.");
        if (!force && tokens.AccessExpires > DateTimeOffset.UtcNow)
            return tokens.Access;
        await _refreshing.WaitAsync(ct);
        try
        {
            if (_tokens is null)
                throw new SignInNeeded("Not signed in.");
            if (!ReferenceEquals(_tokens, tokens) && _tokens.AccessExpires > DateTimeOffset.UtcNow)
                return _tokens.Access;                         // someone else refreshed while we waited
            await TokenRequest(new Dictionary<string, string>
            {
                ["grant_type"] = "refresh_token", ["refresh_token"] = _tokens.Refresh,
                ["client_id"] = ClientId ?? throw new SignInNeeded("Not registered."),
            }, ct);
            return _tokens!.Access;
        }
        finally
        {
            _refreshing.Release();
        }
    }

    /// <summary>
    /// Send a request with the token. <paramref name="make"/> builds it (it may be built twice: once more after a
    /// refresh if the server said 401). The caller disposes the response.
    /// </summary>
    public async Task<HttpResponseMessage> SendAsync(Func<HttpRequestMessage> make, CancellationToken ct = default,
                                                     HttpCompletionOption completion = HttpCompletionOption.ResponseContentRead)
    {
        for (var attempt = 0; ; attempt++)
        {
            var request = make();
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", await AccessAsync(attempt > 0, ct));
            var r = await _http.SendAsync(request, completion, ct);
            if (r.StatusCode == HttpStatusCode.Unauthorized && attempt == 0)
            {
                r.Dispose();
                continue;                                      // the access token was refused: refresh once, try again
            }
            if (r.StatusCode == HttpStatusCode.Unauthorized)
            {
                r.Dispose();
                Forget();
                throw new SignInNeeded("The app was signed out on the server; sign in again.");
            }
            return r;
        }
    }

    private async Task<T> GetJson<T>(string path, CancellationToken ct)
    {
        using var r = await SendAsync(() => new HttpRequestMessage(HttpMethod.Get, At(path)), ct);
        await ThrowIfFailed(r, ct);
        return (await r.Content.ReadFromJsonAsync<T>(Json, ct))!;
    }

    private async Task<T> SendJson<T>(HttpMethod method, string path, object? body, CancellationToken ct)
    {
        using var r = await SendAsync(() => new HttpRequestMessage(method, At(path))
        {
            Content = body is null ? null : JsonContent.Create(body, options: Json),
        }, ct);
        await ThrowIfFailed(r, ct);
        return (await r.Content.ReadFromJsonAsync<T>(Json, ct))!;
    }

    internal static async Task ThrowIfFailed(HttpResponseMessage r, CancellationToken ct)
    {
        if (r.IsSuccessStatusCode)
            return;
        var text = await r.Content.ReadAsStringAsync(ct);
        try
        {
            // FastAPI's errors are {"detail": "..."}: show the server's own sentence
            var detail = JsonDocument.Parse(text).RootElement.GetProperty("detail");
            text = detail.ValueKind == JsonValueKind.String ? detail.GetString()! : detail.ToString();
        }
        catch (Exception e) when (e is JsonException or KeyNotFoundException or InvalidOperationException)
        {
            // not JSON: keep the text as it is
        }
        throw new ServerError(r.StatusCode, string.IsNullOrWhiteSpace(text) ? r.ReasonPhrase ?? "Request failed" : text);
    }

    // -------------------------------------------------------------------- the app
    public sealed record Me(long Id, string Username, string DisplayName);

    public Task<Me> MeAsync(CancellationToken ct = default) => GetJson<Me>("/api/desktop/me", ct);

    /// <summary>The address that signs a tab in again (single use, a minute), going on to <paramref name="next"/>.</summary>
    public async Task<string> SigninUrlAsync(string next, CancellationToken ct = default)
    {
        using var r = await SendAsync(() => new HttpRequestMessage(HttpMethod.Post, At("/api/desktop/session-ticket")), ct);
        await ThrowIfFailed(r, ct);
        var got = await r.Content.ReadFromJsonAsync<JsonElement>(ct);
        return got.GetProperty("signin_url").GetString() + "&next=" + Uri.EscapeDataString(next);
    }

    /// <summary>Download a URL on the server with the token (the update installer) into a file.</summary>
    public async Task DownloadToAsync(string url, string path, CancellationToken ct = default)
    {
        using var r = await SendAsync(() => new HttpRequestMessage(HttpMethod.Get, url), ct, HttpCompletionOption.ResponseHeadersRead);
        await ThrowIfFailed(r, ct);
        await using var file = File.Create(path);
        await r.Content.CopyToAsync(file, ct);
    }

    // ------------------------------------------------------------------- the drive
    public Task<LibraryList> LibrariesAsync(CancellationToken ct = default) => GetJson<LibraryList>("/api/drive/libraries", ct);

    public Task<Listing> ListAsync(long collectionId, long? folderId, CancellationToken ct = default) =>
        GetJson<Listing>($"/api/drive/list?collection_id={collectionId}" + (folderId is { } f ? $"&folder_id={f}" : ""), ct);

    public Task<ChangePage> ChangesAsync(long collectionId, long cursor, CancellationToken ct = default) =>
        GetJson<ChangePage>($"/api/drive/changes?collection_id={collectionId}&cursor={cursor}", ct);

    /// <summary>
    /// Part of a file's bytes (offset, length; length null = to the end), streamed to <paramref name="sink"/>
    /// as they arrive: sink(buffer, count) is called with each piece in order.
    /// </summary>
    public async Task DownloadRangeAsync(long fileId, long offset, long? length, Func<byte[], int, Task> sink,
                                         CancellationToken ct = default)
    {
        using var r = await SendAsync(() =>
        {
            var m = new HttpRequestMessage(HttpMethod.Get, At($"/api/drive/files/{fileId}/content"));
            m.Headers.Range = new RangeHeaderValue(offset, length is { } n ? offset + n - 1 : null);
            return m;
        }, ct, HttpCompletionOption.ResponseHeadersRead);
        await ThrowIfFailed(r, ct);
        await using var body = await r.Content.ReadAsStreamAsync(ct);
        if (r.StatusCode == HttpStatusCode.OK && offset > 0)
            await Skip(body, offset, ct);                       // the server ignored the Range: skip to the offset
        var buffer = new byte[1024 * 1024];
        while (true)
        {
            var n = await body.ReadAsync(buffer, ct);
            if (n == 0)
                break;
            await sink(buffer, n);
        }
    }

    private static async Task Skip(Stream s, long count, CancellationToken ct)
    {
        var buffer = new byte[81920];
        while (count > 0)
        {
            var n = await s.ReadAsync(buffer.AsMemory(0, (int)Math.Min(buffer.Length, count)), ct);
            if (n == 0)
                return;
            count -= n;
        }
    }

    /// <summary>Save new content over a file. 409 (ServerError) when it changed on the server since <paramref name="ifMatch"/>.</summary>
    public async Task<SavedFile> SaveAsync(long fileId, string localPath, string ifMatch, CancellationToken ct = default)
    {
        using var r = await SendAsync(() =>
        {
            var m = new HttpRequestMessage(HttpMethod.Put, At($"/api/drive/files/{fileId}/content"))
            {
                Content = new StreamContent(OpenShared(localPath)),   // a new stream each try
            };
            m.Headers.TryAddWithoutValidation("If-Match", $"\"{ifMatch}\"");
            return m;
        }, ct);
        await ThrowIfFailed(r, ct);
        return (await r.Content.ReadFromJsonAsync<SavedFile>(Json, ct))!;
    }

    /// <summary>A new file from Explorer. The answer has the name the server kept (it adds a number if one is taken).</summary>
    public async Task<RemoteItem> NewFileAsync(long collectionId, long? folderId, string name, string localPath,
                                               CancellationToken ct = default)
    {
        var path = $"/api/drive/files?collection_id={collectionId}&name={Uri.EscapeDataString(name)}"
                   + (folderId is { } f ? $"&folder_id={f}" : "");
        using var r = await SendAsync(() => new HttpRequestMessage(HttpMethod.Post, At(path))
        {
            Content = new StreamContent(OpenShared(localPath)),
        }, ct);
        await ThrowIfFailed(r, ct);
        return (await r.Content.ReadFromJsonAsync<RemoteItem>(Json, ct))!;
    }

    public Task<RemoteItem> NewFolderAsync(long collectionId, long? parentId, string name, CancellationToken ct = default) =>
        SendJson<RemoteItem>(HttpMethod.Post, "/api/drive/folders", new { collection_id = collectionId, parent_id = parentId, name }, ct);

    public async Task<string> RenameAsync(bool folder, long id, string name, CancellationToken ct = default)
    {
        var got = await SendJson<JsonElement>(HttpMethod.Patch, $"/api/drive/{(folder ? "folders" : "files")}/{id}", new { name }, ct);
        return got.GetProperty("name").GetString()!;
    }

    public Task<JsonElement> MoveAsync(bool folder, long id, long collectionId, long? folderId, CancellationToken ct = default) =>
        SendJson<JsonElement>(HttpMethod.Post, $"/api/drive/{(folder ? "folders" : "files")}/{id}/move",
                              new { collection_id = collectionId, folder_id = folderId }, ct);

    public Task<JsonElement> TrashAsync(bool folder, long id, CancellationToken ct = default) =>
        SendJson<JsonElement>(HttpMethod.Delete, $"/api/drive/{(folder ? "folders" : "files")}/{id}", null, ct);

    /// <summary>Read a file other programs may still have open (Word keeps its documents open).</summary>
    private static FileStream OpenShared(string path) =>
        new(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete, 1024 * 1024, useAsync: true);
}

// ------------------------------------------------------------------ the drive's answers (drive.py)
public sealed record Library(long Id, string Kind, string Name, bool CanWrite);
public sealed record LibraryList(List<Library> Libraries, long Cursor);

/// <summary>A file or folder as the server describes it (listing, change feed, new file answers).</summary>
public sealed record RemoteItem
{
    public string Type { get; init; } = "file";        // "file" or "folder"
    public long Id { get; init; }
    public long? FolderId { get; init; }               // a file's folder (null = the library's top)
    public long? ParentId { get; init; }               // a folder's parent (null = the library's top)
    public string Name { get; init; } = "";
    public long Size { get; init; }
    public string? Sha256 { get; init; }               // the content's version (If-Match when saving)
    public string? Modified { get; init; }             // UTC "YYYY-MM-DD HH:MM:SS"
    public bool CanWrite { get; init; } = true;
    public bool Deleted { get; init; }                 // change feed: gone (binned, moved away, no longer readable)

    public bool IsFolder => Type == "folder";
    public long? Parent => IsFolder ? ParentId : FolderId;

    /// <summary>The server's time as a UTC DateTime (now when it's missing or odd).</summary>
    public DateTime ModifiedUtc =>
        DateTime.TryParse(Modified, System.Globalization.CultureInfo.InvariantCulture,
                          System.Globalization.DateTimeStyles.AssumeUniversal | System.Globalization.DateTimeStyles.AdjustToUniversal,
                          out var t) ? t : DateTime.UtcNow;
}

public sealed record Listing(List<RemoteItem> Folders, List<RemoteItem> Files, bool CanWrite, long Cursor);
public sealed record ChangePage(List<RemoteItem> Changes, long Cursor, bool More);
public sealed record SavedFile(long Id, string Sha256, long Size, string? Modified);
