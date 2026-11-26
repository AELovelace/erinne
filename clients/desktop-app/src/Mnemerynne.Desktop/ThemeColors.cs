using System.Globalization;
using System.Text.Json;
using System.Windows;
using System.Windows.Media;

namespace Mnemerynne.Desktop;

/// <summary>
/// The app's own colours (the top row, the menus), taken from the site's theme so the app wears whatever theme the
/// page does: neon, plain, or an organisation's own. The page's CSS variables are read by <see cref="Script"/> in
/// the showing tab, and <see cref="Apply"/> puts them into a ResourceDictionary the window and its menus use
/// (DynamicResource keys: Strip, TabActive, TabHover, Ink, Muted, Accent, MenuBg, MenuBorder, MenuHover,
/// GlowColor, MenuFont, MenuRadius). Until a page has loaded, Erynne's neon colours are used.
/// </summary>
public static class ThemeColors
{
    /// <summary>
    /// Runs in the page: the theme's variables (erynne.css, theme-*.css, /theme/&lt;slug&gt;.css), already worked
    /// out by the browser (var() references resolved), plus the page's own background.
    /// </summary>
    public const string Script = """
        (() => {
          const s = getComputedStyle(document.documentElement);
          const v = n => s.getPropertyValue(n).trim();
          return JSON.stringify({ bg: v('--blush'), panel: v('--glass-strong'), ink: v('--ink'), muted: v('--muted'),
            accent: v('--rose'), line: v('--line'), glow: v('--glow'), font: v('--serif'), radius: v('--radius'),
            page: getComputedStyle(document.body || document.documentElement).backgroundColor });
        })()
        """;

    /// <summary>The colours before any page has loaded: Erynne's neon theme.</summary>
    public static ResourceDictionary Defaults()
    {
        var d = new ResourceDictionary();
        Fill(d, bg: Rgb(0x16, 0x13, 0x1A), panel: Rgb(0x12, 0x05, 0x1C), ink: Rgb(0xFF, 0xE9, 0xF5), muted: Rgb(0xB9, 0x8A, 0xA9),
             accent: Rgb(0xFF, 0x2E, 0x97), line: Color.FromArgb(0x80, 0xFF, 0x2E, 0x97), glow: Rgb(0xFF, 0x2E, 0x97),
             font: "Bahnschrift, Segoe UI", radius: 6);
        return d;
    }

    /// <summary>Put the page's theme (the JSON <see cref="Script"/> returned) into <paramref name="into"/>. Missing or odd values keep what's there.</summary>
    public static void Apply(ResourceDictionary into, string json)
    {
        JsonElement t;
        try
        {
            t = JsonDocument.Parse(json).RootElement;
        }
        catch (JsonException)
        {
            return;                                            // not a page with the theme (a sign-in page elsewhere)
        }
        string S(string key) => t.TryGetProperty(key, out var p) ? p.GetString() ?? "" : "";
        Color Or(string key, string fallbackKey) =>
            Parse(S(key)) ?? (into[fallbackKey] is SolidColorBrush b ? b.Color : Colors.Black);
        var accent = Or("accent", "Accent");
        var bg = Parse(S("bg")) is { } b0 ? Opaque(b0) : (into["Strip"] as SolidColorBrush)?.Color ?? Colors.Black;
        var panel = Parse(S("panel")) is { } p0 ? Opaque(Over(p0, bg)) : bg;
        var glow = ParseTriple(S("glow")) ?? accent;
        var radius = double.TryParse(S("radius").Replace("px", ""), NumberStyles.Float, CultureInfo.InvariantCulture, out var r) ? r : 6;
        Fill(into, bg, panel, Or("ink", "Ink"), Or("muted", "Muted"), accent, Parse(S("line")) ?? accent, glow,
             Font(S("font")), radius);
    }

    /// <summary>Every key the XAML asks for, from the few theme colours.</summary>
    private static void Fill(ResourceDictionary d, Color bg, Color panel, Color ink, Color muted, Color accent, Color line,
                             Color glow, string font, double radius)
    {
        d["Strip"] = Brush(bg);                                       // the top row
        d["TabActive"] = Brush(Mix(bg, accent, 0.16));                // the showing tab: the background, tinted with the accent
        d["TabHover"] = Brush(Color.FromArgb(0x30, accent.R, accent.G, accent.B));          // a tab under the mouse
        d["Ink"] = Brush(ink);                                        // text
        d["Muted"] = Brush(muted);                                    // quieter text
        d["Accent"] = Brush(accent);                                  // the current page, badges
        d["MenuBg"] = Brush(Color.FromArgb(0xF5, panel.R, panel.G, panel.B));                // the menus' glass
        d["MenuBorder"] = Brush(line);
        d["MenuHover"] = Brush(Color.FromArgb(0x38, accent.R, accent.G, accent.B));
        d["GlowColor"] = glow;                                        // the menus' halo
        d["MenuFont"] = new FontFamily(font);
        d["MenuRadius"] = new CornerRadius(Math.Clamp(radius, 0, 14));
    }

    private static SolidColorBrush Brush(Color c)
    {
        var b = new SolidColorBrush(c);
        b.Freeze();                                                   // shared across windows and menus
        return b;
    }

    private static Color Rgb(byte r, byte g, byte b) => Color.FromRgb(r, g, b);

    private static Color Opaque(Color c) => Color.FromRgb(c.R, c.G, c.B);

    /// <summary>A see-through colour laid over another (the theme's glass over its background).</summary>
    private static Color Over(Color top, Color under)
    {
        var a = top.A / 255.0;
        return Color.FromRgb((byte)(top.R * a + under.R * (1 - a)), (byte)(top.G * a + under.G * (1 - a)), (byte)(top.B * a + under.B * (1 - a)));
    }

    /// <summary>A step of the way from one colour to another (0 = a, 1 = b).</summary>
    private static Color Mix(Color a, Color b, double t) =>
        Color.FromRgb((byte)(a.R + (b.R - a.R) * t), (byte)(a.G + (b.G - a.G) * t), (byte)(a.B + (b.B - a.B) * t));

    /// <summary>A CSS colour as the browser reports it: #rgb, #rrggbb, #rrggbbaa, rgb(…) or rgba(…). Null when it isn't one.</summary>
    internal static Color? Parse(string css)
    {
        css = css.Trim();
        if (css.StartsWith('#'))
        {
            var hex = css[1..];
            if (hex.Length is 3 or 4)
                hex = string.Concat(hex.Select(c => $"{c}{c}"));      // #f0a -> #ff00aa
            if (hex.Length is 6 or 8 && uint.TryParse(hex, NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var n))
                return hex.Length == 6
                    ? Color.FromRgb((byte)(n >> 16), (byte)(n >> 8), (byte)n)
                    : Color.FromArgb((byte)n, (byte)(n >> 24), (byte)(n >> 16), (byte)(n >> 8));
            return null;
        }
        if (css.StartsWith("rgb", StringComparison.OrdinalIgnoreCase) && css.IndexOf('(') is var open and > 0 && css.EndsWith(')'))
        {
            var parts = css[(open + 1)..^1].Split([',', ' ', '/'], StringSplitOptions.RemoveEmptyEntries);
            if (parts.Length < 3)
                return null;
            byte C(string x) => (byte)Math.Clamp(double.Parse(x.TrimEnd('%'), CultureInfo.InvariantCulture) * (x.EndsWith('%') ? 2.55 : 1), 0, 255);
            try
            {
                var alpha = parts.Length > 3 ? double.Parse(parts[3].TrimEnd('%'), CultureInfo.InvariantCulture) / (parts[3].EndsWith('%') ? 100 : 1) : 1;
                return Color.FromArgb((byte)Math.Clamp(alpha * 255, 0, 255), C(parts[0]), C(parts[1]), C(parts[2]));
            }
            catch (FormatException)
            {
                return null;
            }
        }
        return null;
    }

    /// <summary>--glow is written "255, 46, 151" (r, g, b without rgb()).</summary>
    private static Color? ParseTriple(string text) => string.IsNullOrWhiteSpace(text) ? null : Parse($"rgb({text})");

    /// <summary>The theme's font list made into WPF's ("Bahnschrift, DIN Alternate, …, Segoe UI"): quotes and CSS-only names go.</summary>
    private static string Font(string css)
    {
        var names = css.Split(',').Select(n => n.Trim().Trim('"', '\'')).Where(n => n.Length > 0
            && n is not ("system-ui" or "sans-serif" or "serif" or "monospace" or "ui-sans-serif" or "-apple-system" or "BlinkMacSystemFont"));
        return string.Join(", ", names.Append("Segoe UI"));
    }
}
