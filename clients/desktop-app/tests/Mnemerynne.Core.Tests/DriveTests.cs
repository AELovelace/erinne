using System.Net;
using System.Text;
using Mnemerynne.Core;
using Mnemerynne.Core.Drive;

namespace Mnemerynne.Core.Tests;

/// <summary>A pretend disk for the change applier: paths -> (is folder, local changes pending).</summary>
internal sealed class FakeDisk : IPlaceholders
{
    public readonly Dictionary<string, (bool Folder, bool Dirty, string? Sha)> Items = new(StringComparer.OrdinalIgnoreCase);
    public readonly List<string> Detached = [];

    public bool Exists(string rel) => Items.ContainsKey(rel);
    public void Create(string parentRel, string name, RemoteItem item, DriveId id) =>
        Items[DriveNames.Join(parentRel, name)] = (item.IsFolder, false, item.Sha256);
    public void Move(string fromRel, string toRel)
    {
        foreach (var key in Items.Keys.Where(k => k.Equals(fromRel, StringComparison.OrdinalIgnoreCase)
                                                || k.StartsWith(fromRel + "\\", StringComparison.OrdinalIgnoreCase)).ToList())
        {
            var v = Items[key];
            Items.Remove(key);
            Items[toRel + key[fromRel.Length..]] = v;
        }
    }
    public void Refresh(string rel, RemoteItem item) => Items[rel] = (false, false, item.Sha256);
    public bool HasLocalChanges(string rel) => Items.TryGetValue(rel, out var v) && v.Dirty;
    public void Delete(string rel)
    {
        foreach (var key in Items.Keys.Where(k => k.Equals(rel, StringComparison.OrdinalIgnoreCase)
                                                || k.StartsWith(rel + "\\", StringComparison.OrdinalIgnoreCase)).ToList())
            Items.Remove(key);
    }
    public void Detach(string fromRel, string toRel)
    {
        Items[toRel] = Items[fromRel];
        Items.Remove(fromRel);
        Detached.Add(toRel);
    }
}

public class DriveTests : IDisposable
{
    private readonly string _dbPath = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString("N"), "drive.db");
    private readonly DriveState _state;
    private readonly FakeDisk _disk = new();
    private readonly ChangeApplier _apply;
    private const long Box = 5;

    public DriveTests()
    {
        _state = new DriveState(_dbPath);
        _apply = new ChangeApplier(_state, _disk, () => new DateTime(2026, 11, 25));
        // The library's top folder, already listed (populated), as the drive leaves it after Explorer opened it.
        _disk.Items["File box"] = (true, false, null);
        _state.Put(new LocalItem(DriveId.Library, Box, Box, null, "File box", null, Populated: true));
    }

    public void Dispose() => _state.Dispose();

    private static RemoteItem File(long id, string name, string sha, long? folder = null, bool deleted = false) =>
        new() { Type = "file", Id = id, Name = name, Sha256 = sha, FolderId = folder, Deleted = deleted, Size = 1 };

    private static RemoteItem Folder(long id, string name, long? parent = null, bool deleted = false) =>
        new() { Type = "folder", Id = id, Name = name, ParentId = parent, Deleted = deleted };

    [Fact]
    public void Identities_round_trip_and_junk_is_refused()
    {
        Assert.Equal(new DriveId('f', 123), DriveId.Parse(new DriveId('f', 123).ToBytes()));
        Assert.Null(DriveId.Parse("x:1"u8));
        Assert.Null(DriveId.Parse("f:"u8));
    }

    [Theory]
    [InlineData("Report: Q1?.pdf", "Report_ Q1_.pdf")]
    [InlineData("notes. ", "notes")]
    [InlineData("CON.txt", "_CON.txt")]
    [InlineData("plain.docx", "plain.docx")]
    public void Names_windows_cant_hold_are_made_safe(string name, string safe) => Assert.Equal(safe, DriveNames.Safe(name));

    [Fact]
    public void A_conflict_copy_gets_a_free_name()
    {
        var day = new DateTime(2026, 11, 25);
        var first = DriveNames.Conflict("Budget.xlsx", day, _ => false);
        Assert.Equal($"Budget ({Environment.MachineName} conflict 2026-11-25).xlsx", first);
        var second = DriveNames.Conflict("Budget.xlsx", day, n => n == first);
        Assert.EndsWith(" 2).xlsx", second);
    }

    [Theory]
    [InlineData("~$Budget.xlsx", true)]
    [InlineData("desktop.ini", true)]
    [InlineData("download.crdownload", true)]
    [InlineData("Budget.xlsx", false)]
    public void Junk_files_are_never_uploaded(string name, bool junk) => Assert.Equal(junk, DriveNames.IsJunk(name));

    [Fact]
    public void New_items_appear_only_in_folders_already_listed()
    {
        _apply.Apply(Box, [Folder(10, "Reports"), File(20, "a.txt", "s1"), File(21, "deep.txt", "s2", folder: 10)]);
        Assert.True(_disk.Exists(@"File box\Reports"));
        Assert.True(_disk.Exists(@"File box\a.txt"));
        Assert.False(_disk.Exists(@"File box\Reports\deep.txt"));   // Reports isn't listed yet: comes on demand
        Assert.Equal(@"File box\a.txt", _state.Find('f', 20)!.RelPath);
    }

    [Fact]
    public void Applying_the_same_change_twice_changes_nothing()
    {
        _apply.Apply(Box, [File(20, "a.txt", "s1")]);
        _apply.Apply(Box, [File(20, "a.txt", "s1")]);
        Assert.Single(_disk.Items, kv => kv.Key.EndsWith("a.txt"));
    }

    [Fact]
    public void Renames_and_folder_moves_carry_everything_under_them()
    {
        _apply.Apply(Box, [Folder(10, "Reports")]);
        _state.SetPopulated(@"File box\Reports");
        _apply.Apply(Box, [File(21, "q1.pdf", "s", folder: 10)]);
        _apply.Apply(Box, [Folder(10, "Old reports")]);
        Assert.True(_disk.Exists(@"File box\Old reports\q1.pdf"));
        Assert.Equal(@"File box\Old reports\q1.pdf", _state.Find('f', 21)!.RelPath);
    }

    [Fact]
    public void New_content_refreshes_a_clean_file()
    {
        _apply.Apply(Box, [File(20, "a.txt", "v1")]);
        _apply.Apply(Box, [File(20, "a.txt", "v2")]);
        Assert.Equal("v2", _disk.Items[@"File box\a.txt"].Sha);
        Assert.Equal("v2", _state.Find('f', 20)!.Sha256);
        Assert.Empty(_disk.Detached);
    }

    [Fact]
    public void A_file_changed_in_both_places_keeps_both()
    {
        _apply.Apply(Box, [File(20, "Budget.xlsx", "v1")]);
        _disk.Items[@"File box\Budget.xlsx"] = (false, true, "local");   // edited here, not uploaded yet
        _apply.Apply(Box, [File(20, "Budget.xlsx", "v2")]);
        var kept = Assert.Single(_disk.Detached);
        Assert.Contains("conflict 2026-11-25", kept);
        Assert.Equal("v2", _disk.Items[@"File box\Budget.xlsx"].Sha);      // the server's version is in place
        Assert.True(_disk.Items[kept].Dirty);                               // and the local work is still here
    }

    [Fact]
    public void Deleted_on_the_server_removes_here_unless_changed_here()
    {
        _apply.Apply(Box, [File(20, "gone.txt", "v1"), File(21, "mine.txt", "v1")]);
        _disk.Items[@"File box\mine.txt"] = (false, true, "local");
        _apply.Apply(Box, [File(20, "gone.txt", "v1", deleted: true), File(21, "mine.txt", "v1", deleted: true)]);
        Assert.False(_disk.Exists(@"File box\gone.txt"));
        Assert.Single(_disk.Detached);                                      // mine.txt kept under a conflict name
        Assert.Null(_state.Find('f', 21));
    }

    [Fact]
    public void A_deleted_folder_takes_its_subtree()
    {
        _apply.Apply(Box, [Folder(10, "Reports")]);
        _state.SetPopulated(@"File box\Reports");
        _apply.Apply(Box, [File(21, "q1.pdf", "s", folder: 10)]);
        _apply.Apply(Box, [Folder(10, "Reports", deleted: true)]);
        Assert.False(_disk.Exists(@"File box\Reports\q1.pdf"));
        Assert.Null(_state.Find('f', 21));
    }

    [Fact]
    public void A_local_file_in_the_way_steps_aside()
    {
        _disk.Items[@"File box\plan.txt"] = (false, true, null);           // made here, never uploaded
        _apply.Apply(Box, [File(30, "plan.txt", "server")]);
        Assert.Equal("server", _disk.Items[@"File box\plan.txt"].Sha);
        Assert.Single(_disk.Detached);
    }

    [Fact]
    public void Cursors_are_kept_per_library()
    {
        _state.SetCursor(Box, 42);
        _state.SetCursor(9, 7);
        Assert.Equal(42, _state.Cursor(Box));
        _state.DropLibrary(9);
        Assert.Equal(0, _state.Cursor(9));
    }
}

/// <summary>A pretend server for ServerApi: answers by "METHOD path", records what was asked.</summary>
internal sealed class FakeServer : HttpMessageHandler
{
    public readonly List<string> Asked = [];
    public Func<HttpRequestMessage, HttpResponseMessage> Answer = _ => new HttpResponseMessage(HttpStatusCode.NotFound);

    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
    {
        Asked.Add($"{request.Method} {request.RequestUri!.PathAndQuery} {request.Headers.Authorization?.Parameter}");
        return Task.FromResult(Answer(request));
    }

    public static HttpResponseMessage Json(string json, HttpStatusCode status = HttpStatusCode.OK) =>
        new(status) { Content = new StringContent(json, Encoding.UTF8, "application/json") };
}

internal sealed class MemoryVault : ITokenVault
{
    public TokenSet? Tokens;
    public TokenSet? Load() => Tokens;
    public void Save(TokenSet? tokens) => Tokens = tokens;
}

public class ServerApiTests
{
    private static readonly Uri Server = new("https://erynne.test");

    [Fact]
    public async Task A_refused_token_is_refreshed_once_and_the_new_one_kept()
    {
        var server = new FakeServer();
        var vault = new MemoryVault { Tokens = new TokenSet("old", "r1", DateTimeOffset.UtcNow.AddHours(1)) };
        server.Answer = r => r.RequestUri!.AbsolutePath switch
        {
            "/oauth/token" => FakeServer.Json("""{"access_token":"new","refresh_token":"r2","expires_in":3600}"""),
            "/api/drive/libraries" when r.Headers.Authorization!.Parameter == "old" => new HttpResponseMessage(HttpStatusCode.Unauthorized),
            "/api/drive/libraries" => FakeServer.Json("""{"libraries":[{"id":5,"kind":"private","name":"File box","can_write":true}],"cursor":9}"""),
            _ => new HttpResponseMessage(HttpStatusCode.NotFound),
        };
        var api = new ServerApi(Server, new HttpClient(server), vault, "mcp_x");
        var libs = await api.LibrariesAsync();
        Assert.Equal("File box", Assert.Single(libs.Libraries).Name);
        Assert.Equal(9, libs.Cursor);
        Assert.Equal("r2", vault.Tokens!.Refresh);                       // saved: the old refresh token is spent
        Assert.Equal(3, server.Asked.Count);                             // refused, refreshed, answered
    }

    [Fact]
    public async Task A_dead_connection_asks_for_sign_in_and_forgets_the_tokens()
    {
        var server = new FakeServer
        {
            Answer = r => r.RequestUri!.AbsolutePath == "/oauth/token"
                ? FakeServer.Json("""{"error":"invalid_grant"}""", HttpStatusCode.BadRequest)
                : new HttpResponseMessage(HttpStatusCode.Unauthorized),
        };
        var vault = new MemoryVault { Tokens = new TokenSet("old", "r1", DateTimeOffset.UtcNow.AddHours(1)) };
        var api = new ServerApi(Server, new HttpClient(server), vault, "mcp_x");
        await Assert.ThrowsAsync<SignInNeeded>(() => api.LibrariesAsync());
        Assert.Null(vault.Tokens);
        Assert.False(api.SignedIn);
    }

    [Fact]
    public async Task Server_refusals_carry_the_servers_own_words()
    {
        var server = new FakeServer { Answer = _ => FakeServer.Json("""{"detail":"File not found"}""", HttpStatusCode.NotFound) };
        var api = new ServerApi(Server, new HttpClient(server),
                                new MemoryVault { Tokens = new TokenSet("a", "r", DateTimeOffset.UtcNow.AddHours(1)) }, "mcp_x");
        var e = await Assert.ThrowsAsync<ServerError>(() => api.ListAsync(5, 99));
        Assert.Equal(HttpStatusCode.NotFound, e.Status);
        Assert.Equal("File not found", e.Message);
        Assert.Contains("GET /api/drive/list?collection_id=5&folder_id=99 a", server.Asked);
    }

    [Fact]
    public async Task The_change_feed_reads_the_servers_names()
    {
        var server = new FakeServer
        {
            Answer = _ => FakeServer.Json("""
                {"changes":[{"type":"file","id":3,"folder_id":null,"name":"a.txt","size":2,"sha256":"ab",
                 "modified":"2026-11-25 10:00:00","can_write":true,"deleted":false},
                 {"type":"folder","id":4,"parent_id":7,"name":"Docs","deleted":true}],"cursor":12,"more":false}
                """),
        };
        var api = new ServerApi(Server, new HttpClient(server),
                                new MemoryVault { Tokens = new TokenSet("a", "r", DateTimeOffset.UtcNow.AddHours(1)) }, "mcp_x");
        var page = await api.ChangesAsync(5, 0);
        Assert.Equal(12, page.Cursor);
        Assert.Equal("ab", page.Changes[0].Sha256);
        Assert.Equal(new DateTime(2026, 11, 25, 10, 0, 0, DateTimeKind.Utc), page.Changes[0].ModifiedUtc);
        Assert.True(page.Changes[1].IsFolder && page.Changes[1].Deleted && page.Changes[1].Parent == 7);
    }
}
