using System.Diagnostics;
using System.IO;
using System.Security.Cryptography;
using Microsoft.Win32;
using Mnemerynne.Core;

namespace Mnemerynne.Desktop;

/// <summary>
/// A profile's tokens on disk, encrypted with Windows' DPAPI for this Windows user only (another account, or a
/// copy of the file on another computer, can't read it). Never written anywhere in plain text, never logged.
/// </summary>
public sealed class DpapiVault(string path) : ITokenVault
{
    private static readonly byte[] Entropy = "Mnemerynne.Desktop.tokens.v1"u8.ToArray();   // ties the file to this app

    public TokenSet? Load()
    {
        try
        {
            if (!File.Exists(path))
                return null;
            var plain = ProtectedData.Unprotect(File.ReadAllBytes(path), Entropy, DataProtectionScope.CurrentUser);
            return System.Text.Json.JsonSerializer.Deserialize<TokenSet>(plain);
        }
        catch (Exception e) when (e is CryptographicException or IOException or System.Text.Json.JsonException)
        {
            return null;                                      // unreadable: the app asks to sign in again
        }
    }

    public void Save(TokenSet? tokens)
    {
        if (tokens is null)
        {
            File.Delete(path);                                // signed out: nothing left behind
            return;
        }
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var secret = ProtectedData.Protect(System.Text.Json.JsonSerializer.SerializeToUtf8Bytes(tokens), Entropy,
                                           DataProtectionScope.CurrentUser);
        var tmp = path + ".tmp";
        File.WriteAllBytes(tmp, secret);
        File.Move(tmp, path, overwrite: true);                // whole or not at all: a refresh token is single use
    }
}

/// <summary>Starting with Windows: the HKCU Run value (per user; the installer sets it, Settings can clear it).</summary>
public static class Autostart
{
    private const string Key = @"Software\Microsoft\Windows\CurrentVersion\Run";
    private const string Name = "Mnemerynne Desktop";

    public static bool IsOn()
    {
        using var k = Registry.CurrentUser.OpenSubKey(Key);
        return k?.GetValue(Name) is string;
    }

    public static void Set(bool on)
    {
        using var k = Registry.CurrentUser.CreateSubKey(Key);
        if (on)
            k.SetValue(Name, $"\"{Environment.ProcessPath}\" --background");   // starts in the tray, no window
        else
            k.DeleteValue(Name, throwOnMissingValue: false);
    }
}

/// <summary>Small helpers that talk to Windows.</summary>
public static class Shell
{
    /// <summary>Open an address or file with whatever Windows picks (the system browser for web links).</summary>
    public static void Open(string target)
    {
        try
        {
            Process.Start(new ProcessStartInfo(target) { UseShellExecute = true });
        }
        catch (Exception e) when (e is System.ComponentModel.Win32Exception or InvalidOperationException)
        {
            Log.Write($"couldn't open {target}: {e.Message}");
        }
    }
}

/// <summary>
/// The app's log: %LOCALAPPDATA%\Mnemerynne\desktop.log, kept to about 1 MB (the old one goes to desktop.log.1).
/// Never holds tokens or file contents: only what happened, for troubleshooting.
/// </summary>
public static class Log
{
    private static readonly Lock Gate = new();
    private static string PathNow => System.IO.Path.Combine(ProfileStore.Root, "desktop.log");

    public static void Write(string line)
    {
        lock (Gate)
        {
            try
            {
                Directory.CreateDirectory(ProfileStore.Root);
                var info = new FileInfo(PathNow);
                if (info.Exists && info.Length > 1024 * 1024)
                    File.Move(PathNow, PathNow + ".1", overwrite: true);
                File.AppendAllText(PathNow, $"{DateTime.UtcNow:yyyy-MM-dd HH:mm:ss}Z {line}{Environment.NewLine}");
            }
            catch (IOException)
            {
                // logging must never take the app down
            }
        }
    }
}
