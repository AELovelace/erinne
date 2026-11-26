using System.Security.Cryptography;

namespace Mnemerynne.Core;

/// <summary>Version numbers ("0.10.2") compared as numbers, the way the server picks the newest build.</summary>
public static class AppVersion
{
    /// <summary>This app's own version (Directory.Build.props), as "major.minor.patch".</summary>
    /// <remarks>
    /// Written exactly as in Directory.Build.props <c>&lt;Version&gt;</c>, so Doll's two-digit patch numbers show as
    /// typed ("0.2.02", not "0.2.2"). Comparisons are by number (<see cref="IsNewer"/>), so 0.2.02 is newer than 0.2.1,
    /// and 0.2.01 is the same as 0.2.1.
    /// </remarks>
    public static string Current
    {
        get
        {
            // The informational version is the <Version> text, plus "+<commit>" that the build adds: drop that part.
            var info = typeof(AppVersion).Assembly
                .GetCustomAttributes(typeof(System.Reflection.AssemblyInformationalVersionAttribute), false)
                .OfType<System.Reflection.AssemblyInformationalVersionAttribute>().FirstOrDefault()?.InformationalVersion;
            if (!string.IsNullOrWhiteSpace(info))
                return info.Split('+')[0];
            var v = typeof(AppVersion).Assembly.GetName().Version ?? new Version(0, 0, 0);   // no attribute: the plain number
            return $"{v.Major}.{v.Minor}.{Math.Max(v.Build, 0)}";
        }
    }

    /// <summary>True when <paramref name="candidate"/> is newer than <paramref name="installed"/>. Junk is never newer.</summary>
    public static bool IsNewer(string? candidate, string installed)
    {
        var a = Parse(candidate);
        var b = Parse(installed);
        if (a is null || b is null)
            return false;
        for (var i = 0; i < Math.Max(a.Length, b.Length); i++)
        {
            var x = i < a.Length ? a[i] : 0;   // "1.2" == "1.2.0"
            var y = i < b.Length ? b[i] : 0;
            if (x != y)
                return x > y;
        }
        return false;
    }

    private static int[]? Parse(string? text)
    {
        if (string.IsNullOrWhiteSpace(text))
            return null;
        var parts = text.Trim().Split('.');
        var numbers = new int[parts.Length];
        for (var i = 0; i < parts.Length; i++)
            if (!int.TryParse(parts[i], out numbers[i]) || numbers[i] < 0)
                return null;
        return numbers;
    }
}

/// <summary>Checking a downloaded installer before it is run.</summary>
public static class Download
{
    /// <summary>True when the file's SHA-256 is the one the server announced (case doesn't matter).</summary>
    public static bool Matches(string path, string expectedSha256)
    {
        using var stream = File.OpenRead(path);
        var actual = Convert.ToHexString(SHA256.HashData(stream));
        return actual.Equals(expectedSha256.Trim(), StringComparison.OrdinalIgnoreCase);
    }
}
