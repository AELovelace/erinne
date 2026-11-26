using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Effects;
using Mnemerynne.Core;

namespace Mnemerynne.Desktop;

/// <summary>
/// Dragging a tab, so you can see what you're doing (Doll, 2026-11-26): the tab lifts (a little see-through, with a
/// glow in the theme's colour) and follows the mouse along the strip; a bright line shows where it will land, only
/// in places it may go (its own block: places among places, a group's tabs among that group's, files among files);
/// over a group's label the label lights up, and letting go there adds the tab to that group. Esc puts it back.
/// A click without moving just shows the tab, as before.
/// </summary>
public partial class MainWindow
{
    private BrowserTab? _pressed;                 // the tab the left button went down on (not yet dragged)
    private FrameworkElement? _pressedBox;        // its Border in the strip
    private Point _pressedAt;                     // where, in the window
    private bool _dragging;
    private double _grab;                         // how far into the tab the mouse took hold (strip coordinates)
    private BrowserTab? _dropBefore;              // where it will land: before this tab ...
    private bool _dropAtEnd;                      // ... or at the end of its block
    private TabGroup? _dropGroup;                 // ... or into this group (over its label)

    private static BrowserTab? TabOf(object sender) => (sender as FrameworkElement)?.DataContext as BrowserTab;

    private void OnTabMouseDown(object sender, MouseButtonEventArgs e)
    {
        if (TabOf(sender) is not { } tab)
            return;
        Select(_tabs.IndexOf(tab), focusPage: false);     // a click shows it straight away; the page gets focus on
                                                          // the button's release, so it can't take the mouse from a drag
        _pressed = tab;
        _pressedBox = (FrameworkElement)sender;
        _pressedAt = e.GetPosition(this);
    }

    private void OnTabMouseMove(object sender, MouseEventArgs e)
    {
        if (_pressed is null || _pressedBox is null || e.LeftButton != MouseButtonState.Pressed)
            return;
        if (!_dragging)
        {
            if (Math.Abs(e.GetPosition(this).X - _pressedAt.X) < SystemParameters.MinimumHorizontalDragDistance)
                return;                                   // not a drag yet: a click may still be on its way
            StartDrag();
        }
        FollowMouse(e.GetPosition(TabStrip).X);
    }

    private void OnTabMouseUp(object sender, MouseButtonEventArgs e)
    {
        if (_dragging && e.ChangedButton == MouseButton.Left)
        {
            FinishDrag(drop: true);
            e.Handled = true;
            return;
        }
        if (e.ChangedButton == MouseButton.Left && _pressed is { } clicked)
            clicked.View.Focus();                         // a plain click: typing goes to the page now
        _pressed = null;
        if (e.ChangedButton == MouseButton.Middle && TabOf(sender) is { } tab)
            CloseTab(_tabs.IndexOf(tab));                 // middle-click closes, as in a browser
    }

    private void OnTabClose(object sender, RoutedEventArgs e)
    {
        if (TabOf(sender) is { } tab)
            CloseTab(_tabs.IndexOf(tab));
        e.Handled = true;
    }

    /// <summary>Lift the tab: it keeps the mouse (so the drag goes on outside it), turns see-through and glows.</summary>
    private void StartDrag()
    {
        // Clicking it may have redrawn the strip (a folded group's visible tab changes with the showing tab), which
        // makes fresh elements: take hold of the tab's element as it is now, not the one the button went down on.
        TabStrip.UpdateLayout();
        if (Holder(_pressed!) is { } slotNow && VisualTreeHelper.GetChildrenCount(slotNow) > 0
            && VisualTreeHelper.GetChild(slotNow, 0) is FrameworkElement now)
            _pressedBox = now;
        _dragging = true;
        _grab = _pressedAt.X - _pressedBox!.TranslatePoint(new Point(0, 0), this).X;
        _pressedBox.RenderTransform = new TranslateTransform();   // set up before taking the mouse (see below)
        _pressedBox.Opacity = 0.85;
        _pressedBox.Effect = new DropShadowEffect
        {
            ShadowDepth = 0, BlurRadius = 16, Opacity = 0.8,
            Color = TryFindResource("GlowColor") is Color c ? c : Colors.HotPink,   // the theme's glow colour, as it is now
        };
        if (VisualTreeHelper.GetParent(_pressedBox) is UIElement holder)
            Panel.SetZIndex(holder, 10);                  // drawn over its neighbours while it moves
        // Last: taking the mouse makes WPF send a mouse-move straight away, into FollowMouse, before this method
        // would carry on (that early move used to find no offset yet, fail, and leave the tab un-lifted: Doll saw it
        // on the showing tab).
        _pressedBox.LostMouseCapture += OnDragCaptureLost;
        _pressedBox.CaptureMouse();
    }

    /// <summary>Move the lifted tab under the mouse and work out where it would land.</summary>
    private void FollowMouse(double mouseX)
    {
        var slot = Holder(_pressed!)?.TranslatePoint(new Point(0, 0), TabStrip).X ?? 0;
        if (_pressedBox?.RenderTransform is not TranslateTransform move)
            return;                                       // not lifted (yet): nothing to move
        move.X = mouseX - _grab - slot;                   // the tab stays under the mouse

        _dropBefore = null;
        _dropAtEnd = false;
        _dropGroup = null;
        foreach (var g in _groups)
            g.DropTarget = false;

        // Over a group's label (not its own group's): letting go adds it there.
        foreach (var g in _strip.OfType<TabGroup>())
        {
            if (g == _pressed!.Group || Holder(g) is not { } box)
                continue;
            var left = box.TranslatePoint(new Point(0, 0), TabStrip).X;
            if (mouseX >= left && mouseX <= left + box.ActualWidth)
            {
                _dropGroup = g;
                g.DropTarget = true;                      // the label lights up
                DropLine.Visibility = Visibility.Collapsed;
                return;
            }
        }

        // Otherwise: before the first tab of its own block whose middle is right of the mouse, else at the block's end.
        var block = TabKinds.Block(SortKey(_pressed!));
        var mates = _strip.OfType<BrowserTab>().Where(t => t != _pressed && TabKinds.Block(SortKey(t)) == block).ToList();
        double lineX;
        var next = mates.FirstOrDefault(t => Holder(t) is { } h && h.TranslatePoint(new Point(h.ActualWidth / 2, 0), TabStrip).X > mouseX);
        if (next is not null)
        {
            _dropBefore = next;
            lineX = Holder(next)!.TranslatePoint(new Point(0, 0), TabStrip).X;
        }
        else
        {
            _dropAtEnd = true;
            var last = mates.LastOrDefault() is { } l && Holder(l) is { } lh ? lh : Holder(_pressed!);
            lineX = last is null ? 0 : last.TranslatePoint(new Point(last.ActualWidth, 0), TabStrip).X;
        }
        Canvas.SetLeft(DropLine, lineX - 1);              // the bright line where it would land
        DropLine.Visibility = Visibility.Visible;
    }

    /// <summary>Put the tab down: where the line was, into the lit group, or (Esc, or a lost mouse) back where it was.</summary>
    private void FinishDrag(bool drop)
    {
        var tab = _pressed;
        var box = _pressedBox;
        _dragging = false;
        _pressed = null;
        _pressedBox = null;
        DropLine.Visibility = Visibility.Collapsed;
        foreach (var g in _groups)
            g.DropTarget = false;
        if (box is not null)
        {
            box.LostMouseCapture -= OnDragCaptureLost;
            box.ReleaseMouseCapture();
            box.RenderTransform = Transform.Identity;     // back into its slot (the reorder moves the slot)
            box.Opacity = 1;
            box.Effect = null;
            if (VisualTreeHelper.GetParent(box) is UIElement holder)
                Panel.SetZIndex(holder, 0);
        }
        if (tab is not null)
            Dispatcher.BeginInvoke(() => tab.View.Focus(), System.Windows.Threading.DispatcherPriority.Input);   // after the drop
        if (!drop || tab is null)
            return;
        if (_dropGroup is { } group)
        {
            MoveToGroup(tab, group);                      // joins the group (Regroup moves it there)
            return;
        }
        var from = _tabs.IndexOf(tab);
        var to = _dropBefore is { } before ? _tabs.IndexOf(before) : _dropAtEnd ? LastOfBlock(tab) : from;
        if (_dropBefore is not null && from < to)
            to--;                                         // taking it out first shifts the ones after it
        if (from < 0 || to < 0 || from == to)
            return;
        _active = TabMath.AfterMove(from, to, _active);
        _tabs.Move(from, to);
        Regroup();
        SaveSession();
    }

    private void OnDragCaptureLost(object sender, MouseEventArgs e)
    {
        if (_dragging)
            FinishDrag(drop: false);                      // something else took the mouse: put it back
    }

    /// <summary>The position of the last tab in the same block as <paramref name="tab"/>.</summary>
    private int LastOfBlock(BrowserTab tab)
    {
        var block = TabKinds.Block(SortKey(tab));
        var last = -1;
        for (var i = 0; i < _tabs.Count; i++)
            if (TabKinds.Block(SortKey(_tabs[i])) == block)
                last = i;
        return last;
    }

    /// <summary>The strip's holder for a tab or a label (its place in the row).</summary>
    private FrameworkElement? Holder(object item) => TabStrip.ItemContainerGenerator.ContainerFromItem(item) as FrameworkElement;
}
