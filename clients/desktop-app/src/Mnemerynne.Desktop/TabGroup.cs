using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Windows.Media;
using Mnemerynne.Core;

namespace Mnemerynne.Desktop;

/// <summary>
/// A tab group, like Firefox's (Doll, 2026-11-26): a name and a colour, shown as a label in the strip before its
/// tabs, with its tabs underlined in its colour. Clicking the label folds the group away (only the showing tab
/// stays visible) or opens it again. Saved with the tabs (tabs.json, SavedGroup).
/// </summary>
public sealed class TabGroup : INotifyPropertyChanged
{
    /// <summary>The colours a group can have, in the order new groups take them (Erynne's palette).</summary>
    public static readonly (string Name, string Hex)[] Palette =
    [
        ("Pink", "#FF2E97"), ("Cyan", "#00F0FF"), ("Purple", "#A855F7"), ("Yellow", "#FACC15"),
        ("Green", "#22C55E"), ("Orange", "#FB923C"), ("Red", "#F43F5E"), ("Grey", "#94A3B8"),
    ];

    public string Id { get; }
    private string _name;
    private string _color;
    private bool _collapsed;
    private int _count;

    public TabGroup(string id, string name, string color, bool collapsed = false)
    {
        Id = id;
        _name = name;
        _color = color;
        _collapsed = collapsed;
    }

    public string Name { get => _name; set { _name = value; Changed(); Changed(nameof(Label)); } }

    public string Color
    {
        get => _color;
        set { _color = value; Changed(); Changed(nameof(Brush)); Changed(nameof(Tint)); }
    }

    /// <summary>Folded away: its tabs are hidden in the strip (they keep running), except the showing one.</summary>
    public bool Collapsed { get => _collapsed; set { _collapsed = value; Changed(); Changed(nameof(Label)); } }

    private bool _dropTarget;
    /// <summary>A dragged tab is over its label: the label lights up (MainWindow.Drag.cs).</summary>
    public bool DropTarget { get => _dropTarget; set { _dropTarget = value; Changed(); } }

    /// <summary>How many tabs it has (the label shows it while folded).</summary>
    public int Count { get => _count; set { _count = value; Changed(); Changed(nameof(Label)); } }

    /// <summary>What the label says: the name, and the number of tabs while it's folded ("Budget · 3").</summary>
    public string Label => Collapsed ? $"{Name} · {Count}" : Name;

    /// <summary>The group's colour, for the label and its tabs' underline.</summary>
    public Brush Brush => Frozen((Color)ColorConverter.ConvertFromString(_color));

    /// <summary>A faint wash of the colour, behind the label.</summary>
    public Brush Tint
    {
        get
        {
            var c = (Color)ColorConverter.ConvertFromString(_color);
            return Frozen(System.Windows.Media.Color.FromArgb(0x40, c.R, c.G, c.B));
        }
    }

    private static Brush Frozen(Color c)
    {
        var b = new SolidColorBrush(c);
        b.Freeze();
        return b;
    }

    public SavedGroup Save() => new(Id, Name, Color, Collapsed);

    public static TabGroup From(SavedGroup g) => new(g.Id, g.Name, g.Color, g.Collapsed);

    public event PropertyChangedEventHandler? PropertyChanged;

    private void Changed([CallerMemberName] string? name = null) =>
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
}
