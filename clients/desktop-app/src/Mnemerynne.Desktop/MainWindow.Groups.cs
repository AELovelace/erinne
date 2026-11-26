using System.Collections.ObjectModel;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using Mnemerynne.Core;

namespace Mnemerynne.Desktop;

/// <summary>
/// Tab groups, like Firefox's (Doll, 2026-11-26), and the strip that shows them. A group has a name and a colour;
/// its label sits before its tabs, which are underlined in its colour. Grouped tabs stay together, between the
/// ungrouped places and the ungrouped opened things (TabKinds.Ordered). Clicking a label folds the group away (only
/// the showing tab stays visible); right-click menus do the rest. Groups are saved with the tabs (tabs.json).
/// </summary>
public partial class MainWindow
{
    private readonly List<TabGroup> _groups = [];                    // in strip order
    private readonly ObservableCollection<object> _strip = [];       // what the strip shows: TabGroup labels and BrowserTabs

    private static TabGroup? GroupOf(object sender) => (sender as FrameworkElement)?.DataContext as TabGroup;

    /// <summary>A tab's place in the strip order: its kind and its group's position (TabKinds.Block).</summary>
    private (TabKind, int?) SortKey(BrowserTab t) => (t.Kind, t.Group is { } g ? _groups.IndexOf(g) : null);

    /// <summary>
    /// Put the tabs in strip order (TabKinds.Ordered), drop groups left empty, and redraw the strip. The showing tab
    /// stays showing wherever it moves.
    /// </summary>
    private void Regroup()
    {
        var showing = Current;
        _groups.RemoveAll(g => _tabs.All(t => t.Group != g));    // a group with no tabs left is gone
        var order = TabKinds.Ordered(_tabs.Select(SortKey).ToList()).Select(i => _tabs[i]).ToList();
        for (var i = 0; i < order.Count; i++)
            if (!ReferenceEquals(_tabs[i], order[i]))
                _tabs.Move(_tabs.IndexOf(order[i]), i);
        _active = showing is null ? -1 : _tabs.IndexOf(showing);
        RebuildStrip();
    }

    /// <summary>
    /// Draw the strip from the tabs: each group's label just before its first tab; a folded group's tabs left out,
    /// except the showing one. Then fit the widths.
    /// </summary>
    private void RebuildStrip()
    {
        var items = new List<object>();
        TabGroup? current = null;
        foreach (var g in _groups)
            g.Count = _tabs.Count(t => t.Group == g);
        foreach (var t in _tabs)
        {
            if (t.Group is { } g && g != current)
                items.Add(g);                                      // the label, once, before the group's tabs
            current = t.Group;
            if (t.Group is { Collapsed: true } && !t.Active)
                continue;                                          // folded away (it keeps running)
            items.Add(t);
        }
        if (!_strip.SequenceEqual(items))
        {
            _strip.Clear();
            foreach (var item in items)
                _strip.Add(item);
        }
        Dispatcher.BeginInvoke(() => FitStrips(TabArea.ActualWidth));   // widths change with what's showing
    }

    /// <summary>
    /// Fit the tabs in the row, as a browser does (TabWidths.Fit): places compact (100), opened things the normal
    /// width (200), both shrinking alike when there are many, before the strip has to scroll.
    /// </summary>
    private void FitStrips(double width)
    {
        if (width <= 0)
            return;
        var room = Math.Max(120, width - 48 - 40);                       // 48: the + button, 40: room to drag the window
        var shown = _strip.OfType<BrowserTab>().ToList();
        var labels = _strip.OfType<TabGroup>().Sum(g => 30 + g.Label.Length * 7.5);   // about how wide the labels are
        var (place, doc) = TabWidths.Fit(room - labels, shown.Count(t => !t.IsDocument), shown.Count(t => t.IsDocument));
        foreach (var t in _tabs)
            t.TabWidth = t.IsDocument ? doc : place;
        StripScroller.MaxWidth = room;
    }

    // ============================================================================ the menus
    /// <summary>Right-click a tab: start a group with it, add it to one, take it out, or close it.</summary>
    private void OnTabRightClick(object sender, MouseButtonEventArgs e)
    {
        if (TabOf(sender) is not { } tab)
            return;
        e.Handled = true;
        var menu = NewMenu((UIElement)sender);
        Item(menu, "New group with this tab", () => NewGroup(tab));
        foreach (var g in _groups.Where(g => g != tab.Group))
            Item(menu, GroupDot(g, $"Add to “{g.Name}”"), () => MoveToGroup(tab, g));
        if (tab.Group is { } mine)
            Item(menu, $"Remove from “{mine.Name}”", () => MoveToGroup(tab, null));
        Line(menu);
        Item(menu, "Close tab", () => CloseTab(_tabs.IndexOf(tab)), gesture: "Ctrl+W");
        menu.IsOpen = true;
    }

    /// <summary>Click a group's label: fold it away, or open it again.</summary>
    private void OnGroupClick(object sender, MouseButtonEventArgs e)
    {
        if (GroupOf(sender) is not { } g)
            return;
        g.Collapsed = !g.Collapsed;
        RebuildStrip();
        SaveSession();
    }

    /// <summary>Right-click a group's label: rename, colour, ungroup, close the group.</summary>
    private void OnGroupRightClick(object sender, MouseButtonEventArgs e)
    {
        if (GroupOf(sender) is not { } g)
            return;
        e.Handled = true;
        var menu = NewMenu((UIElement)sender);
        Item(menu, "Rename…", () => RenameGroup(g));
        Item(menu, g.Collapsed ? "Unfold" : "Fold away", () => OnGroupClick(sender, e));
        Item(menu, "Colour", null);                                      // a heading for the colours below
        foreach (var (name, hex) in TabGroup.Palette)
            Item(menu, GroupDot(new TabGroup("", name, hex), "   " + name), () => { g.Color = hex; SaveSession(); },
                 current: g.Color.Equals(hex, StringComparison.OrdinalIgnoreCase));
        Line(menu);
        Item(menu, "Ungroup", () =>
        {
            foreach (var t in _tabs.Where(t => t.Group == g))
                t.Group = null;                                  // the tabs stay open, just ungrouped
            Regroup();
            SaveSession();
        });
        Item(menu, "Close group", () =>
        {
            foreach (var t in _tabs.Where(t => t.Group == g).ToList())
                CloseTab(_tabs.IndexOf(t));                      // closed tabs can still come back with Ctrl+Shift+T
        });
        menu.IsOpen = true;
    }

    /// <summary>A tab dropped on a group's label joins that group.</summary>
    private void OnGroupDrop(object sender, DragEventArgs e)
    {
        if (GroupOf(sender) is { } g && e.Data.GetData(typeof(BrowserTab)) is BrowserTab tab)
        {
            MoveToGroup(tab, g);
            e.Handled = true;                                    // not also a reorder on the strip
        }
    }

    /// <summary>A coloured dot before a menu line, so each group is easy to tell apart.</summary>
    private static object GroupDot(TabGroup g, string text)
    {
        var row = new StackPanel { Orientation = Orientation.Horizontal };
        row.Children.Add(new Border { Width = 10, Height = 10, CornerRadius = new CornerRadius(5), Background = g.Brush,
                                      Margin = new Thickness(0, 0, 8, 0), VerticalAlignment = VerticalAlignment.Center });
        row.Children.Add(new TextBlock { Text = text, VerticalAlignment = VerticalAlignment.Center });
        return row;
    }

    // ========================================================================== the changes
    private void NewGroup(BrowserTab tab)
    {
        var colour = TabGroup.Palette[_groups.Count % TabGroup.Palette.Length].Hex;   // the next colour round
        var g = new TabGroup(Guid.NewGuid().ToString("N")[..8], $"Group {_groups.Count + 1}", colour);
        _groups.Add(g);
        tab.Group = g;
        Regroup();
        SaveSession();
        RenameGroup(g);                                          // name it now, as Firefox asks
    }

    private void MoveToGroup(BrowserTab tab, TabGroup? g)
    {
        tab.Group = g;
        Regroup();
        SaveSession();
    }

    private void RenameGroup(TabGroup g)
    {
        var dialog = new GroupNameWindow(this, g.Name);
        if (dialog.ShowDialog() == true && dialog.Result is { Length: > 0 } name)
        {
            g.Name = name;
            RebuildStrip();
            SaveSession();
        }
    }

    /// <summary>Groups back from tabs.json (OnLoadedAsync): the group a saved tab belongs to.</summary>
    private TabGroup? RestoreGroup(TabSession saved, string? id)
    {
        if (id is null)
            return null;
        if (_groups.FirstOrDefault(g => g.Id == id) is { } known)
            return known;
        if (saved.Groups.FirstOrDefault(g => g.Id == id) is not { } s)
            return null;
        var made = TabGroup.From(s);
        _groups.Add(made);
        return made;
    }
}

/// <summary>"Name this group": a small dialog with the name chosen, Enter to keep it, Esc to leave it.</summary>
public sealed class GroupNameWindow : Window
{
    private readonly TextBox _name;
    public string? Result { get; private set; }

    public GroupNameWindow(Window owner, string name)
    {
        Title = "Name this group";
        Owner = owner;
        Width = 340;
        SizeToContent = SizeToContent.Height;
        ResizeMode = ResizeMode.NoResize;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;
        _name = new TextBox { Text = name, Margin = new Thickness(0, 6, 0, 0), Padding = new Thickness(6, 4, 6, 4) };
        var ok = new Button { Content = "OK", IsDefault = true, Padding = new Thickness(16, 4, 16, 4), Margin = new Thickness(0, 0, 8, 0) };
        ok.Click += (_, _) => { Result = _name.Text.Trim(); DialogResult = true; };
        var buttons = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right,
                                       Margin = new Thickness(0, 12, 0, 0) };
        buttons.Children.Add(ok);
        buttons.Children.Add(new Button { Content = "Cancel", IsCancel = true, Padding = new Thickness(16, 4, 16, 4) });
        var body = new StackPanel { Margin = new Thickness(16) };
        body.Children.Add(new TextBlock { Text = "Group name" });
        body.Children.Add(_name);
        body.Children.Add(buttons);
        Content = body;
        Loaded += (_, _) => { _name.Focus(); _name.SelectAll(); };
    }
}
