#!/usr/bin/env python3
"""
timetrack - a terminal time tracker with a modern TUI built on Textual.

Single file. Requires Textual (which pulls in Rich):

    pip install textual          # or: pipx install textual / uv pip install textual

Run:

    python3 timetrack.py

Data is stored as JSON in ~/.timetrack.json (override with $TIMETRACK_FILE).

Concepts
--------
- One project is "active" at a time; switching is a single keypress.
- Each project holds a list of tasks. The active project can also have an
  active task, so time rolls up per project AND per task.
- A continuous stretch is a "session" (project, task, start, end).
- A "context switch" is counted only when the active PROJECT changes to a
  different project. Switching tasks inside the same project, or pausing then
  resuming, is NOT a context switch.
- Projects carry tags, so focus time can be aggregated per tag.
- The flame graph is an icicle view: a project layer (sized by focus time)
  with a task layer stacked on top, widths proportional to time.

Keys
----
  tab                move focus between the Projects and Tasks panels
  up/down            move selection in the focused panel
  1..9               jump to project/task N (whichever panel is focused) and,
                     for projects, start tracking instantly
  enter              act on the highlighted row (start project / pick task)
  s                  start/resume the selected project
  p                  pause (stop the running timer)
  a                  add a project
  n                  add a task to the selected project
  space / c          toggle the selected task done / not done
  t                  edit tags of the selected project
  r                  rename the selected project
  d                  delete the selected project
  x                  delete the selected task
  f                  set/clear a tag filter for the stats panel
  w                  cycle the stats window (today / 7 days / all time)
  q                  quit (the running session is kept and resumes next launch)
"""

import json
import os
import time
from datetime import datetime, date

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.coordinate import Coordinate
    from textual.reactive import reactive
    from textual.screen import ModalScreen
    from textual.widgets import (
        DataTable, Footer, Header, Input, Label, Static,
    )
except ModuleNotFoundError:
    raise SystemExit(
        "Textual is not installed. Install it with:\n\n"
        "    pip install textual\n"
    )

DATA_FILE = os.environ.get(
    "TIMETRACK_FILE", os.path.expanduser("~/.timetrack.json")
)
NO_TASK = "(no task)"
DOT = "\u00b7"


# --------------------------------------------------------------------------- #
# Logic layer (pure Python, no UI dependency)
# --------------------------------------------------------------------------- #
class TimeTracker:
    def __init__(self, path=DATA_FILE):
        self.path = path
        self.projects = []   # [{"name", "tags":[...], "tasks":[{"name","done"}]}]
        self.sessions = []   # [{"project","task","start","end"}]
        self.active = None   # {"project","task","start"} or None
        self.load()

    # ---- persistence ------------------------------------------------------ #
    def load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            return
        self.projects = data.get("projects", [])
        for p in self.projects:               # migrate older files
            p.setdefault("tags", [])
            p.setdefault("tasks", [])
        self.sessions = data.get("sessions", [])
        for s in self.sessions:
            s.setdefault("task", None)
        active = data.get("active")
        if active and self._find(active.get("project")) is not None:
            active.setdefault("task", None)
            self.active = active  # resume an in-progress session after a quit

    def save(self):
        data = {"projects": self.projects, "sessions": self.sessions,
                "active": self.active}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, self.path)

    # ---- projects --------------------------------------------------------- #
    def _find(self, name):
        for p in self.projects:
            if p["name"] == name:
                return p
        return None

    def add_project(self, name, tags=None):
        name = (name or "").strip()
        if not name or self._find(name):
            return False
        self.projects.append({"name": name, "tags": tags or [], "tasks": []})
        self.save()
        return True

    def rename_project(self, old, new):
        new = (new or "").strip()
        if not new or self._find(new):
            return False
        p = self._find(old)
        if not p:
            return False
        p["name"] = new
        for s in self.sessions:
            if s["project"] == old:
                s["project"] = new
        if self.active and self.active["project"] == old:
            self.active["project"] = new
        self.save()
        return True

    def delete_project(self, name):
        p = self._find(name)
        if not p:
            return False
        if self.active and self.active["project"] == name:
            self.stop()
        self.projects.remove(p)
        self.sessions = [s for s in self.sessions if s["project"] != name]
        self.save()
        return True

    def set_tags(self, name, tags):
        p = self._find(name)
        if not p:
            return False
        p["tags"] = [t.strip() for t in tags if t.strip()]
        self.save()
        return True

    def tags_of(self, name):
        p = self._find(name)
        return p["tags"] if p else []

    def all_tags(self):
        tags = set()
        for p in self.projects:
            tags.update(p["tags"])
        return sorted(tags)

    # ---- tasks ------------------------------------------------------------ #
    def tasks_of(self, project):
        p = self._find(project)
        return p["tasks"] if p else []

    def _find_task(self, project, task):
        for t in self.tasks_of(project):
            if t["name"] == task:
                return t
        return None

    def add_task(self, project, task):
        task = (task or "").strip()
        p = self._find(project)
        if not p or not task or self._find_task(project, task):
            return False
        p["tasks"].append({"name": task, "done": False})
        self.save()
        return True

    def toggle_task(self, project, task):
        t = self._find_task(project, task)
        if not t:
            return False
        t["done"] = not t["done"]
        self.save()
        return True

    def delete_task(self, project, task):
        p = self._find(project)
        t = self._find_task(project, task)
        if not p or not t:
            return False
        p["tasks"].remove(t)
        if self.active and self.active["project"] == project \
                and self.active["task"] == task:
            self.active["task"] = None
        self.save()
        return True

    # ---- tracking --------------------------------------------------------- #
    def start(self, name, task=None, now=None):
        """Make (name, task) active. Returns True on a real PROJECT switch."""
        now = now or time.time()
        if self._find(name) is None:
            return False
        switched = False
        if self.active:
            if self.active["project"] == name and self.active["task"] == task:
                return False  # already on this exact project+task, no-op
            switched = self.active["project"] != name
            self.stop(now)    # split the session (task change splits too)
        self.active = {"project": name, "task": task, "start": now}
        self.save()
        return switched

    def stop(self, now=None):
        now = now or time.time()
        if not self.active:
            return False
        start = self.active["start"]
        if now > start:
            self.sessions.append({
                "project": self.active["project"],
                "task": self.active["task"],
                "start": start, "end": now,
            })
        self.active = None
        self.save()
        return True

    def is_running(self):
        return self.active is not None

    def active_project(self):
        return self.active["project"] if self.active else None

    def active_task(self):
        return self.active["task"] if self.active else None

    def active_elapsed(self, now=None):
        if not self.active:
            return 0.0
        now = now or time.time()
        return max(0.0, now - self.active["start"])

    # ---- statistics ------------------------------------------------------- #
    def _window_bounds(self, window, now=None):
        now = now or time.time()
        if window == "today":
            start = datetime.combine(date.today(), datetime.min.time()).timestamp()
            return start, now
        if window == "7d":
            return now - 7 * 86400, now
        return 0.0, now

    def _iter_sessions(self, now=None):
        now = now or time.time()
        for s in self.sessions:
            yield s["project"], s.get("task"), s["start"], s["end"]
        if self.active:
            yield (self.active["project"], self.active["task"],
                   self.active["start"], now)

    @staticmethod
    def _overlap(a0, a1, b0, b1):
        return max(0.0, min(a1, b1) - max(a0, b0))

    def focus_by_project(self, window="today", now=None):
        now = now or time.time()
        w0, w1 = self._window_bounds(window, now)
        totals = {}
        for proj, task, s0, s1 in self._iter_sessions(now):
            dur = self._overlap(s0, s1, w0, w1)
            if dur > 0:
                totals[proj] = totals.get(proj, 0.0) + dur
        return totals

    def focus_by_task(self, project, window="today", now=None):
        now = now or time.time()
        w0, w1 = self._window_bounds(window, now)
        totals = {}
        for proj, task, s0, s1 in self._iter_sessions(now):
            if proj != project:
                continue
            dur = self._overlap(s0, s1, w0, w1)
            if dur > 0:
                key = task or NO_TASK
                totals[key] = totals.get(key, 0.0) + dur
        return totals

    def focus_by_tag(self, window="today", now=None):
        per_proj = self.focus_by_project(window, now)
        totals = {}
        for proj, secs in per_proj.items():
            for tag in self.tags_of(proj):
                totals[tag] = totals.get(tag, 0.0) + secs
        return totals

    def focus_for_tag(self, tag, window="today", now=None):
        return self.focus_by_tag(window, now).get(tag, 0.0)

    def total_focus(self, window="today", now=None):
        return sum(self.focus_by_project(window, now).values())

    def context_switches(self, window="today", now=None):
        now = now or time.time()
        w0, w1 = self._window_bounds(window, now)
        timeline = sorted(self._iter_sessions(now), key=lambda x: x[2])
        switches, prev = 0, None
        for proj, task, s0, s1 in timeline:
            if prev is not None and proj != prev and w0 <= s0 <= w1:
                switches += 1
            prev = proj
        return switches

    def switch_events(self, window="today", now=None):
        """Return [(timestamp, from_project, to_project)] for project switches."""
        now = now or time.time()
        w0, w1 = self._window_bounds(window, now)
        timeline = sorted(self._iter_sessions(now), key=lambda x: x[2])
        events, prev = [], None
        for proj, task, s0, s1 in timeline:
            if prev is not None and proj != prev and w0 <= s0 <= w1:
                events.append((s0, prev, proj))
            prev = proj
        return events


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #
def fmt_hms(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:d}h{m:02d}m{s:02d}s"
    if m:
        return f"{m:d}m{s:02d}s"
    return f"{s:d}s"


def fmt_hm(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    return f"{h:d}h{m:02d}m" if h else f"{m:d}m"


def alloc(items, width):
    """Split `width` columns among (key, secs) proportionally (largest remainder)."""
    total = sum(max(0.0, s) for _, s in items)
    if total <= 0 or width <= 0:
        return []
    raw = [(k, width * s / total) for k, s in items]
    widths = [int(x) for _, x in raw]
    rem = width - sum(widths)
    order = sorted(range(len(raw)), key=lambda i: -(raw[i][1] - int(raw[i][1])))
    for i in range(rem):
        widths[order[i % len(order)]] += 1
    return [(raw[i][0], widths[i]) for i in range(len(raw))]


def blend(hex_color, factor):
    """Lighten a #rrggbb color toward white by `factor` in [0, 1]."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    r = int(r + (255 - r) * factor)
    g = int(g + (255 - g) * factor)
    b = int(b + (255 - b) * factor)
    return f"#{r:02x}{g:02x}{b:02x}"


PALETTE = ["#0063cc", "#22c55e", "#eab308", "#ef4444", "#a855f7",
           "#06b6d4", "#f97316", "#84cc16", "#ec4899", "#14b8a6"]
WINDOW_LABELS = {"today": "Today", "7d": "Last 7 days", "all": "All time"}
WINDOW_ORDER = ["today", "7d", "all"]


# --------------------------------------------------------------------------- #
# Modal prompt
# --------------------------------------------------------------------------- #
class PromptScreen(ModalScreen):
    """Single-line input modal. Returns the string, or None if cancelled."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, prompt, prefill=""):
        super().__init__()
        self.prompt_text = prompt
        self.prefill = prefill

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self.prompt_text, id="dialog-label")
            yield Input(value=self.prefill, id="dialog-input")

    def on_mount(self) -> None:
        self.query_one("#dialog-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)


# --------------------------------------------------------------------------- #
# Textual application
# --------------------------------------------------------------------------- #
class TimeTrackApp(App):
    CSS = """
    Screen { layout: vertical; }

    #status {
        height: 3;
        padding: 0 1;
        background: $boost;
        border: round $primary;
    }
    #status.running { color: $success; border: round $success; }
    #status.paused  { color: $text-muted; }

    #body { height: 1fr; }

    #left  { width: 42%; }
    #right { width: 1fr; }

    #projects-box { height: 1fr; border: round $primary; padding: 0 1; }
    #tasks-box    { height: 1fr; border: round $secondary; padding: 0 1; }
    #stats-box    { height: 1fr; border: round $accent; padding: 0 1; }
    #flame-box    { height: 16; border: round $warning; padding: 0 1; }

    .panel-title { text-style: bold; }
    DataTable { height: 1fr; }

    #dialog {
        align: center middle;
        width: 60; height: auto;
        padding: 1 2;
        background: $surface;
        border: thick $primary;
    }
    #dialog-label { margin-bottom: 1; }
    """

    BINDINGS = [
        Binding("tab", "toggle_panel", "Panel"),
        Binding("s", "start_project", "Start"),
        Binding("p", "pause", "Pause"),
        Binding("a", "add_project", "Add proj"),
        Binding("n", "add_task", "Add task"),
        Binding("space", "toggle_done", "Done", show=False),
        Binding("c", "toggle_done", "Done"),
        Binding("t", "tags", "Tags"),
        Binding("r", "rename", "Rename"),
        Binding("d", "delete_project", "Del proj"),
        Binding("x", "delete_task", "Del task"),
        Binding("f", "filter", "Filter"),
        Binding("w", "window", "Window"),
        Binding("g", "flame_mode", "Flame mode"),
        Binding("q", "quit", "Quit"),
    ] + [Binding(str(n), f"quick({n})", show=False) for n in range(1, 10)]

    window = reactive("today")
    tag_filter = reactive(None)
    flame_mode = reactive("size")

    def __init__(self, tracker: TimeTracker):
        super().__init__()
        self.tk = tracker
        self.notice = "Press a number to start a project, 'a' to add one, 'n' for a task."
        self._color_map = {}
        self._last_proj_sel = None

    # ---- layout ----------------------------------------------------------- #
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("", id="status")
        with Horizontal(id="body"):
            with Vertical(id="left"):
                with Vertical(id="projects-box"):
                    yield Label("PROJECTS", classes="panel-title")
                    yield DataTable(id="projects", cursor_type="row",
                                    zebra_stripes=True)
                with Vertical(id="tasks-box"):
                    yield Label("TASKS", id="tasks-title", classes="panel-title")
                    yield DataTable(id="tasks", cursor_type="row",
                                    zebra_stripes=True)
            with Vertical(id="right"):
                with Vertical(id="stats-box"):
                    yield Static("", id="stats")
                with Vertical(id="flame-box"):
                    yield Label("FLAME GRAPH", id="flame-title",
                                classes="panel-title")
                    yield Static("", id="flame")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "timetrack"
        pt = self.query_one("#projects", DataTable)
        pt.add_columns("#", "Project", "Tags", "Focus")
        tt = self.query_one("#tasks", DataTable)
        tt.add_columns("", "Task", "Focus")
        pt.focus()
        self.refresh_view()
        self.set_interval(1.0, self._tick)

    # ---- selection helpers ----------------------------------------------- #
    def _panel(self):
        return "tasks" if self.focused is self.query_one("#tasks") else "projects"

    def selected_project(self):
        t = self.query_one("#projects", DataTable)
        row = t.cursor_row
        if 0 <= row < len(self.tk.projects):
            return self.tk.projects[row]["name"]
        return None

    def selected_task(self):
        proj = self.selected_project()
        if not proj:
            return None
        tasks = self.tk.tasks_of(proj)
        t = self.query_one("#tasks", DataTable)
        row = t.cursor_row
        if 0 <= row < len(tasks):
            return tasks[row]["name"]
        return None

    def _color_for(self, project):
        if project not in self._color_map:
            self._color_map[project] = PALETTE[len(self._color_map) % len(PALETTE)]
        return self._color_map[project]

    # ---- rendering -------------------------------------------------------- #
    def refresh_view(self) -> None:
        now = time.time()
        self._render_status(now)
        self._render_projects(now)
        self._render_tasks(now)
        self._render_stats(now)
        self._render_flame(now)

    def _tick(self) -> None:
        # Per-second update: only refresh dynamic TEXT and in-place cell
        # values. Never clear/rebuild the tables here (that resets the cursor
        # and makes navigation feel frozen).
        now = time.time()
        self._render_status(now)
        self._render_stats(now)
        self._render_flame(now)
        self._update_focus_cells(now)

    def _update_focus_cells(self, now) -> None:
        try:
            pt = self.query_one("#projects", DataTable)
            focus = self.tk.focus_by_project(self.window, now)
            for i, p in enumerate(self.tk.projects):
                pt.update_cell_at(Coordinate(i, 3), fmt_hm(focus.get(p["name"], 0.0)))
            proj = self.selected_project()
            if proj:
                tt = self.query_one("#tasks", DataTable)
                tf = self.tk.focus_by_task(proj, self.window, now)
                for i, task in enumerate(self.tk.tasks_of(proj)):
                    tt.update_cell_at(Coordinate(i, 2), fmt_hm(tf.get(task["name"], 0.0)))
        except Exception:
            pass  # table mid-rebuild; the next tick will catch up

    def _render_status(self, now):
        s = self.query_one("#status", Static)
        if self.tk.is_running():
            ap, at = self.tk.active_project(), self.tk.active_task()
            label = f"{ap}" + (f"  \u25b8 {at}" if at else "")
            s.update(f"[b]RUNNING[/b]  {label}   [b]{fmt_hms(self.tk.active_elapsed(now))}[/b]"
                     f"\n[dim]{self.notice}[/dim]")
            s.set_class(True, "running"); s.set_class(False, "paused")
        else:
            s.update("[b]PAUSED[/b]  press a number or 's' to start"
                     f"\n[dim]{self.notice}[/dim]")
            s.set_class(True, "paused"); s.set_class(False, "running")

    def _render_projects(self, now):
        t = self.query_one("#projects", DataTable)
        focus = self.tk.focus_by_project(self.window, now)
        saved = t.cursor_row
        t.clear()
        for i, p in enumerate(self.tk.projects):
            name = p["name"]
            running = self.tk.is_running() and self.tk.active_project() == name
            marker = "[b green]\u25b6[/b green]" if running else (f"{i+1}" if i < 9 else "")
            disp = f"[b green]{name}[/b green]" if running else name
            tags = ", ".join(p["tags"])
            t.add_row(marker, disp, tags or "[dim]-[/dim]",
                      fmt_hm(focus.get(name, 0.0)))
        if self.tk.projects:
            t.move_cursor(row=min(max(saved, 0), len(self.tk.projects) - 1))

    def _render_tasks(self, now):
        t = self.query_one("#tasks", DataTable)
        title = self.query_one("#tasks-title", Label)
        proj = self.selected_project()
        title.update(f"TASKS \u2014 {proj}" if proj else "TASKS")
        focus = self.tk.focus_by_task(proj, self.window, now) if proj else {}
        saved = t.cursor_row
        t.clear()
        tasks = self.tk.tasks_of(proj) if proj else []
        for i, task in enumerate(tasks):
            name = task["name"]
            box = "[green]\u2714[/green]" if task["done"] else "\u25a1"
            active = (self.tk.is_running()
                      and self.tk.active_project() == proj
                      and self.tk.active_task() == name)
            disp = f"[b green]{name}[/b green]" if active else (
                f"[strike dim]{name}[/strike dim]" if task["done"] else name)
            t.add_row(box, disp, fmt_hm(focus.get(name, 0.0)))
        if tasks:
            t.move_cursor(row=min(max(saved, 0), len(tasks) - 1))

    def _render_stats(self, now):
        stats = self.query_one("#stats", Static)
        win = WINDOW_LABELS[self.window]
        total = self.tk.total_focus(self.window, now)
        switches = self.tk.context_switches(self.window, now)
        by_proj = self.tk.focus_by_project(self.window, now)
        by_tag = self.tk.focus_by_tag(self.window, now)

        out = []
        head = f"STATISTICS \u2014 {win}"
        if self.tag_filter:
            head += f"  \u2022 #{self.tag_filter}"
        out.append(f"[b cyan]{head}[/b cyan]\n")
        out.append(f"Total focus       : [b]{fmt_hms(total)}[/b]")
        out.append(f"Context switches  : [b]{switches}[/b]")
        evs = self.tk.switch_events(self.window, now)
        if evs:
            ts, frm, to = evs[-1]
            when = datetime.fromtimestamp(ts).strftime("%H:%M")
            out.append(f"Last switch       : {when}  {frm} \u2192 {to}")
        if by_proj:
            top = max(by_proj, key=by_proj.get)
            out.append(f"Top project       : {top} ([b]{fmt_hm(by_proj[top])}[/b])")
        if self.tag_filter:
            secs = self.tk.focus_for_tag(self.tag_filter, self.window, now)
            out.append(f"[b magenta]Focus on #{self.tag_filter} : {fmt_hms(secs)}[/b magenta]")
        out.append("")
        out.append("[b cyan]By tag[/b cyan]")
        if not by_tag:
            out.append("  [dim](no tagged time yet)[/dim]")
        else:
            for tag, secs in sorted(by_tag.items(), key=lambda kv: -kv[1]):
                out.append(f"  [magenta]#{tag:<12.12}[/magenta] {fmt_hm(secs):>7}  "
                           f"{self._bar(secs, total)}")
        stats.update("\n".join(out))

    @staticmethod
    def _bar(value, total, width=12):
        if total <= 0:
            return ""
        n = int(round(width * value / total))
        return "[green]" + "\u2588" * n + "[/green][dim]" + "\u00b7" * (width - n) + "[/dim]"

    def _render_flame(self, now):
        flame = self.query_one("#flame", Static)
        title = self.query_one("#flame-title", Label)
        width = max(20, flame.size.width or 50)
        if self.flame_mode == "timeline":
            title.update("FLAME GRAPH \u2014 timeline (ordered by time)   [g] toggle")
            self._render_flame_timeline(now, flame, width)
            return
        title.update("FLAME GRAPH \u2014 aggregated (by size)   [g] toggle")
        self._render_flame_aggregated(now, flame, width)

    def _render_flame_aggregated(self, now, flame, width):
        by_proj = self.tk.focus_by_project(self.window, now)
        if not by_proj:
            flame.update("[dim](nothing tracked in this window yet)[/dim]")
            return
        projs = sorted(by_proj.items(), key=lambda kv: -kv[1])
        total = sum(v for _, v in projs)

        # Root layer
        root = f"[b on $primary] root  {fmt_hm(total)} ".ljust(width) + "[/]"
        lines = [root[:width + 20]]

        # Project layer
        proj_alloc = alloc(projs, width)
        lines.append(self._flame_line(
            [(name, w, self._color_for(name), 0.0) for name, w in proj_alloc]))

        # Task layer: subdivide each project's width by its tasks
        task_cells = []
        for name, w in proj_alloc:
            if w <= 0:
                continue
            tasks = self.tk.focus_by_task(name, self.window, now)
            items = sorted(tasks.items(), key=lambda kv: -kv[1])
            sub = alloc(items, w)
            base = self._color_for(name)
            for j, (tname, sw) in enumerate(sub):
                shade = 0.18 * (j % 4) + (0.0 if tname != NO_TASK else 0.55)
                task_cells.append((tname, sw, base, shade))
        lines.append(self._flame_line(task_cells))

        # Legend
        legend = "  ".join(
            f"[{self._color_for(n)}]\u2588[/] {n} {fmt_hm(s)}" for n, s in projs[:6])
        lines.append("")
        lines.append(legend)
        flame.update("\n".join(lines))

    @staticmethod
    def _flame_line(cells):
        parts = []
        for label, w, color, shade in cells:
            if w <= 0:
                continue
            bg = blend(color, shade)
            text = (" " + label)[:w].ljust(w) if w > 1 else " " * w
            parts.append(f"[black on {bg}]{text}[/]")
        return "".join(parts) if parts else "[dim](no data)[/dim]"

    def _display_bounds(self, now):
        if self.window == "all":
            starts = [s["start"] for s in self.tk.sessions]
            if self.tk.active:
                starts.append(self.tk.active["start"])
            return (min(starts) if starts else now - 3600), now
        return self.tk._window_bounds(self.window, now)

    def _render_flame_timeline(self, now, flame, width):
        w0, w1 = self._display_bounds(now)
        span = max(1.0, w1 - w0)
        proj_cols = [None] * width
        task_cols = [None] * width
        first_seen = {}
        for proj, task, s0, s1 in self.tk._iter_sessions(now):
            a, b = max(s0, w0), min(s1, w1)
            if b <= a:
                continue
            first_seen.setdefault(proj, a)
            cs = int((a - w0) / span * width)
            ce = min(width, max(int((b - w0) / span * width), cs + 1))
            for c in range(cs, ce):
                proj_cols[c] = proj
                task_cols[c] = (proj, task or NO_TASK)
        if not any(proj_cols):
            flame.update("[dim](nothing tracked in this window yet)[/dim]")
            return

        def proj_label(key):
            return key, self._color_for(key), 0.0

        def task_label(key):
            proj, task = key
            return task, self._color_for(proj), (0.5 if task == NO_TASK else 0.25)

        lines = [self._timeline_row(proj_cols, proj_label),
                 self._timeline_row(task_cols, task_label)]
        left = datetime.fromtimestamp(w0).strftime("%H:%M")
        right = datetime.fromtimestamp(w1).strftime("%H:%M")
        axis = left + " " * max(1, width - len(left) - len(right)) + right
        lines.append(f"[dim]{axis[:width]}[/dim]")
        order = sorted(first_seen, key=lambda p: first_seen[p])
        lines.append("  ".join(f"[{self._color_for(p)}]\u2588[/] {p}" for p in order[:6]))
        flame.update("\n".join(lines))

    @staticmethod
    def _timeline_row(cols, labeler):
        out, i, n = [], 0, len(cols)
        while i < n:
            key = cols[i]
            j = i
            while j < n and cols[j] == key:
                j += 1
            w = j - i
            if key is None:
                out.append(f"[dim]{DOT * w}[/dim]")
            else:
                label, color, shade = labeler(key)
                bg = blend(color, shade)
                text = (" " + label)[:w].ljust(w) if w > 1 else " " * w
                out.append(f"[black on {bg}]{text}[/]")
            i = j
        return "".join(out) if out else "[dim](no data)[/dim]"

    # ---- events ----------------------------------------------------------- #
    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "projects":
            self._start(self.selected_project(), None)
        elif event.data_table.id == "tasks":
            proj, task = self.selected_project(), self.selected_task()
            if proj and task:
                self._start(proj, task)

    def on_data_table_row_highlighted(self, event) -> None:
        # Only rebuild the Tasks panel when the selected PROJECT actually
        # changes. Never trigger a full refresh from a highlight event, or
        # cursor movement feeds back into table rebuilds and stalls the UI.
        if event.data_table.id == "projects":
            sel = self.selected_project()
            if sel != self._last_proj_sel:
                self._last_proj_sel = sel
                now = time.time()
                self._render_tasks(now)

    # ---- actions ---------------------------------------------------------- #
    def action_toggle_panel(self):
        if self._panel() == "projects":
            self.query_one("#tasks", DataTable).focus()
        else:
            self.query_one("#projects", DataTable).focus()

    def _start(self, name, task):
        if name is None:
            return
        switched = self.tk.start(name, task)
        label = name + (f" \u25b8 {task}" if task else "")
        self.notice = (f"Switched to '{label}' (context switch logged)."
                       if switched else f"Tracking '{label}'.")
        self.refresh_view()

    def action_start_project(self):
        self._start(self.selected_project(), None)

    def action_quick(self, n: int):
        idx = n - 1
        if self._panel() == "tasks":
            proj = self.selected_project()
            tasks = self.tk.tasks_of(proj) if proj else []
            if idx < len(tasks):
                self.query_one("#tasks", DataTable).move_cursor(row=idx)
                self._start(proj, tasks[idx]["name"])
        else:
            if idx < len(self.tk.projects):
                self.query_one("#projects", DataTable).move_cursor(row=idx)
                self._start(self.tk.projects[idx]["name"], None)

    def action_pause(self):
        self.notice = "Paused." if self.tk.stop() else "Nothing running."
        self.refresh_view()

    def action_add_project(self):
        def got_name(name):
            if not name:
                self.notice = "Cancelled."; self.refresh_view(); return

            def got_tags(tags):
                taglist = [t.strip() for t in (tags or "").split(",") if t.strip()]
                ok = self.tk.add_project(name, taglist)
                self.notice = f"Added '{name}'." if ok else "Name empty or duplicate."
                self.refresh_view()

            self.push_screen(PromptScreen("Tags (comma separated, optional):"), got_tags)

        self.push_screen(PromptScreen("New project name:"), got_name)

    def action_add_task(self):
        proj = self.selected_project()
        if not proj:
            self.notice = "Select a project first."; return

        def got(name):
            if not name:
                self.notice = "Cancelled."
            else:
                ok = self.tk.add_task(proj, name)
                self.notice = (f"Added task '{name}' to '{proj}'." if ok
                               else "Task empty or duplicate.")
            self.refresh_view()

        self.push_screen(PromptScreen(f"New task for '{proj}':"), got)

    def action_toggle_done(self):
        proj, task = self.selected_project(), self.selected_task()
        if proj and task:
            self.tk.toggle_task(proj, task)
            self.notice = f"Toggled '{task}'."
            self.refresh_view()

    def action_delete_task(self):
        proj, task = self.selected_project(), self.selected_task()
        if not (proj and task):
            return

        def got(ans):
            if ans == "yes":
                self.tk.delete_task(proj, task)
                self.notice = f"Deleted task '{task}'."
            else:
                self.notice = "Cancelled (type 'yes')."
            self.refresh_view()

        self.push_screen(PromptScreen(f"Delete task '{task}'? Type 'yes':"), got)

    def action_tags(self):
        name = self.selected_project()
        if not name:
            return
        current = ", ".join(self.tk.tags_of(name))

        def got(tags):
            if tags is None:
                self.notice = "Cancelled."
            else:
                self.tk.set_tags(name, [t.strip() for t in tags.split(",") if t.strip()])
                self.notice = f"Tags updated for '{name}'."
            self.refresh_view()

        self.push_screen(PromptScreen(f"Tags for '{name}':", current), got)

    def action_rename(self):
        name = self.selected_project()
        if not name:
            return

        def got(new):
            if not new:
                self.notice = "Cancelled."
            elif self.tk.rename_project(name, new):
                self.notice = f"Renamed to '{new}'."
            else:
                self.notice = "Rename failed (empty or duplicate)."
            self.refresh_view()

        self.push_screen(PromptScreen(f"Rename '{name}' to:", name), got)

    def action_delete_project(self):
        name = self.selected_project()
        if not name:
            return

        def got(ans):
            if ans == "yes":
                self.tk.delete_project(name)
                self.notice = f"Deleted '{name}'."
            else:
                self.notice = "Cancelled (type 'yes')."
            self.refresh_view()

        self.push_screen(PromptScreen(f"Delete project '{name}' and its tasks? Type 'yes':"), got)

    def action_filter(self):
        tags = self.tk.all_tags()
        hint = "/".join(tags) if tags else "no tags yet"

        def got(val):
            self.tag_filter = val or None
            self.notice = f"Filtering stats by #{val}." if val else "Filter cleared."
            self.refresh_view()

        self.push_screen(PromptScreen(f"Filter by tag ({hint}); empty clears:"), got)

    def action_flame_mode(self):
        self.flame_mode = "timeline" if self.flame_mode == "size" else "size"
        self.notice = f"Flame graph mode: {self.flame_mode}."
        self.refresh_view()

    def action_window(self):
        i = WINDOW_ORDER.index(self.window)
        self.window = WINDOW_ORDER[(i + 1) % len(WINDOW_ORDER)]
        self.notice = f"Stats window: {WINDOW_LABELS[self.window]}."
        self.refresh_view()

    def action_quit(self):
        self.tk.save()
        self.exit()


def main():
    tracker = TimeTracker()
    TimeTrackApp(tracker).run()
    if tracker.is_running():
        print(f"Still tracking '{tracker.active_project()}' "
              f"({fmt_hms(tracker.active_elapsed())}); it will resume next launch.")
    print(f"Data saved to {tracker.path}")


if __name__ == "__main__":
    main()
