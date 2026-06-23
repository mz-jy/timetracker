# ⭘ timetrack

> *Know where your time goes — before the week ends.*

**timetrack** is a keyboard-driven time tracker that lives in your terminal. No browser tabs, no Electron memory leaks, no subscription fees. Just you, your projects, and a beautiful TUI that gets out of your way.

---

### Why timetrack?

Most time trackers ask you to remember what you did *after the fact*. timetrack flips the model: **start tracking with a single keypress**, switch contexts when you switch tasks, and let the tool accumulate the data. Review your day — or your week — in a live flame graph that shows exactly where your focus went.

---

## What it looks like

```
 ⭘                                timetrack                            14:20:26
╭──────────────────────────────────────────────────────────────────────────────╮
│ ▶ RUNNING  datadog — Wakam · write a post-mortem                00:12:34     │
╰──────────────────────────────────────────────────────────────────────────────╯
╭───────────────────────────────╮╭─────────────────────────────────────────────╮
│ PROJECTS                      ││ STATISTICS — Today                          │
│  #  Project                   ││  Focus time   3h 22m                        │
│  1  datadog — Wakam        ▶  ││  Sessions         7                         │
│  2  personal — ibkr           ││  Context switches  2                        │
│  3  planning                  │╰─────────────────────────────────────────────╯
│  4  comply-advantage — Wakam  │╭─────────────────────────────────────────────╮
╰───────────────────────────────╯│ FLAME GRAPH — aggregated by size            │
╭───────────────────────────────╮│                                              │
│ TASKS — datadog — Wakam       ││ ██████████ comply-advantage  2h 06m         │
│     Task                      ││ ████ datadog — Wakam         47m            │
│  ☑  fix the pipeline       ✓  ││ ██ planning                  22m            │
│  ▶  write a post-mortem       ││ █ personal — ibkr             7m            │
│  □  update runbooks           │╰─────────────────────────────────────────────╯
╰───────────────────────────────╯
 s Start  p Pause  a Add project  n Add task  c Done  t Tags  r Rename
```

## Features

- **One-key start / pause.** Hit `s` to start tracking, `p` to pause. Context switches happen naturally — just start a different project.
- **Per-project task lists.** Every project holds its own tasks. Mark them done with `space`. Track time against a specific task or let it roll up to the project.
- **Live flame graph.** See where your day went at a glance. An icicle view sizes each project by focus time, with tasks stacked on top. Resizes in real time as you work.
- **Tag filtering.** Tag projects (e.g. `client`, `deep-work`, `meetings`) and filter the stats panel to a single tag. Answer "how much deep work did I get done this week?" in one keystroke.
- **Time windows.** Cycle the stats view between *today*, *last 7 days*, and *all time* with `w`.
- **Resume-safe.** Quit (`q`) and the running session is saved. Launch again and it picks up right where you left off — timer still counting.
- **Zero-config.** Data lives in `~/.timetrack.json`. Point it elsewhere with `$TIMETRACK_FILE`. That's it.

## Install

```bash
uv tool install git+https://github.com/mz-jy/timetracker
```

Or clone and install locally:

```bash
git clone https://github.com/mz-jy/timetracker
cd timetracker
uv tool install .
```

Requires Python ≥ 3.12. Dependencies: [Textual](https://textual.textualize.io/).

## Run

```bash
timetrack
```

That's it. The TUI opens in your terminal.

## Keys

| Key | Action |
|---|---|
| `s` | Start / resume the selected project |
| `p` | Pause (stop the running timer) |
| `1`–`9` | Jump to project/task N (projects start instantly) |
| `enter` | Act on the highlighted row |
| `a` | Add a project |
| `n` | Add a task to the selected project |
| `space` / `c` | Toggle task done / not done |
| `t` | Edit tags of the selected project |
| `r` | Rename the selected project |
| `d` | Delete the selected project |
| `x` | Delete the selected task |
| `f` | Set / clear a tag filter for stats |
| `w` | Cycle stats window (today → 7 days → all time) |
| `tab` | Switch focus between Projects and Tasks panels |
| `↑` `↓` | Move selection in the focused panel |
| `q` | Quit (running session is saved) |

## How it thinks about time

- **Sessions** are continuous stretches of work on a project (optionally scoped to a task). Start → pause → start again = two sessions.
- **Context switches** are counted only when you switch to a *different project*. Switching tasks inside the same project, or pausing and resuming, is not a context switch — it's the same focus stream.
- **Tags** let you aggregate across projects. Tag everything `client` or `deep-work` and filter to see your billable hours or focused time in one view.

---

*Built with [Textual](https://textual.textualize.io/). Single Python file. No database, no daemon, no cloud.*
