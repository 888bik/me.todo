# Changelog

## 3.4.10
- Measure the left navigation width with Tk font metrics for the widest filter/view labels, two-digit counts, and the `逾期 99 · 今日到期 99` hint; recompute after construction, font changes, and navigation/count updates while retaining the DPI-scaled 160px minimum.
- Scale the window minimum/default dimensions and the details sidebar width by the startup DPI factor. Clamp physical window geometry to the available screen size.
- Store `window_width` / `window_height` as logical pixels and convert to physical pixels when applying geometry; convert back when saving. Window position remains in physical pixels.
- Add DPI layout, detail-panel fallback/restore, minimum-size, screen-clamp, and logical-geometry round-trip regression tests.

## 3.4.9
- Declare Windows process DPI awareness before WinShun initialization can create the sticky-note Tk root: prefer `DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2` and fall back to `SetProcessDPIAware`; non-Windows and unsupported Windows versions remain non-fatal. Existing Tk DPI scaling is unchanged.
- Add a two-second monotonic watchdog while the visible note is locked. If the low-level mouse hook dies, reinstall it; if reinstall fails, explicitly restore mouse input and show the `Ctrl+Alt+U` emergency-unlock hint.
- Use the Windows system double-click rectangle (`SM_CXDOUBLECLK` / `SM_CYDOUBLECLK`) independently for horizontal and vertical distance checks, with the previous 10×10-pixel fallback when unavailable.
- Slow the Tk command pump from 40ms to 100ms while visible and from 500ms to 1000ms while hidden. The 30-second reminder scheduler is unchanged.
- Add regression tests for DPI-awareness ordering/fallback/once-only behavior, hook watchdog recovery/fallback, system double-click metrics, and pump intervals.

## 3.4.8
- Window-resize drag no longer rebuilds every visible row on each canvas `<Configure>`: width/scrollregion updates stay immediate, the full row pass is debounced by 40ms and old pending passes are cancelled. A width-only change just re-places the existing cards.
- `_update_visible_rows` is re-entrancy guarded, so the scrollregion -> yscroll cascade during a rebuild cannot start another full pass.
- Row due/remind labels are cached by task data plus minute, so resizing/scrolling no longer reparses dates on every visible row.

## 3.4.7
- Showing the note now lifts it above other windows and applies a temporary 200ms topmost flag before restoring the normal unlocked state. This fixes newly added todos appearing behind the browser while keeping focus off the note (`focus=False`); locked windows stay topmost.
- The delayed restore is cancellable: consecutive show commands cancel the old timer before scheduling a new one, so a stale timer cannot clear topmost early or after locking.

## 3.4.6
- Import now reloads `todos.json` into `App.todos` before rendering, so imported entries are visible and the next add/toggle cannot overwrite them with the stale in-memory list. Duplicate ids are still skipped.
- The mouse-hook message pump wraps `TranslateMessage`/`DispatchMessageW` per message: a dispatch exception is logged and the pump keeps running; it still exits only on `GetMessageW <= 0` and unhooks normally. Ctrl+double-click no longer dies with the pump.
- Repeat reminders parse anchors with `store.parse_dt` (naive strings count as local time); if no next occurrence can be calculated the todo is marked reminded once instead of toasting every 30 seconds. Imported `remind_at`/`repeat_base` are normalized to aware ISO, and unparsable times are cleared with a log.
- The Tk command pump polls every 500ms while the note is withdrawn/iconic and returns to 40ms as soon as a show command runs; hidden queues are still drained.
- README privacy wording now says only files chosen for import/export are read/written.
- Confirmed the mouse-hook callback is already minimal (two comparisons plus `CallNextHookEx` for ordinary mouse messages); kept as an observation item, no code change beyond the pump guard.

## 3.4.5
- Restore the 18px child-row indent that the 3.4.0 virtual-list rewrite dropped: child task cards are placed at x=18 with width reduced by 18, while top-level rows stay at x=0. The reused row pool recomputes the indent from the current task on every placement.
- The inline new-subtask entry uses the same 18px indent so it lines up with the child rows it creates.

## 3.4.4
- Row action icons (`＋ ⏰ ··· ✕`) now use the same font size as the task title (previously title − 1) and have `width=3` plus `padx=2`, so they are visibly larger and easier to click without changing row height.

## 3.4.3
- Detail sidebar priority selection now saves immediately via `<<ComboboxSelected>>` (无=0, 低=1, 中=2, 高=3); only `priority` is sent, so unsaved title/tags/note are not overwritten.
- Pinned checkbox saves immediately the same way.
- Save/delete/complete buttons moved out of the scrolling body into a fixed footer below the canvas; the footer appears only in detail mode and its complete/cancel-complete label follows the task state.

## 3.4.2
- Detail sidebar scrolling is smooth again: wheel events are bound to every field/label/button (not just the canvas), and the canvas scrolls in 1px units with proportional high-resolution deltas.

## 3.4.1
- The gear “界面设置” dialog now has a 50–100% window transparency slider. The value is saved to `config.json` `transparency`, applied immediately, and restored on the next launch; clamping matches the host setting.

## 3.4.0
- Virtual list: only visible rows plus a 4-row buffer exist in the Tk tree; scrolling moves and reuses the same row widgets instead of building every task.
- Row height is measured from a template row (compact/font changes remeasure) and the canvas scrollregion is set from “items × height”; `bbox("all")` is no longer the source of truth.
- The row pool swaps title/meta/checkbox/actions callbacks per task, so hover, focus, clicks, priority pills, overdue/reminder colors and subtasks keep working without stale tasks.
- Scroll position survives toggle, delete, add, search, filter and selection; selecting a row only repaints visible highlights. The subtask input keeps its slot and focus while scrolling.
- Bench baseline (Windows, Python 3.12, Tk default DPI, 720×520): 2000 rows startup/refresh/search all around 0.1s, down from 19.2s/73.2s/66.0s in 3.3.1.

## 3.3.1
- `store.occurrences()` now jumps straight to the requested range instead of walking every occurrence from `repeat_base`; daily/weekly/monthly/weekdays keep the same dates, completed-repeat cutoff and 10,000-step guard.
- Reminder firing batches its persistence: `mark_reminded` / `reschedule_repeat` accept `save=False`, and `fire_reminders` writes `todos.json` once after the whole batch.
- The remaining dialogs are single-instance: font/compact settings, the date/reminder picker and completion stats focus their existing window instead of stacking another; closing clears the reference.
- Add a local `bench.py` performance baseline (not used by the plugin at runtime).

## 3.3.0
- Task details moved from a reusable Toplevel into a 300px right-hand sidebar. Clicking a row selects it and shows its details; ✕ collapses the sidebar and restores the previous window width.
- The sidebar keeps title, deadline, reminder, repeat, tags, priority, pin, note, subtasks, complete and delete controls; saving still uses the existing controller calls.
- Month view day cells (and “+N 更多”) now open that day’s task list in the sidebar; clicking an entry switches the same sidebar to its details. The old “more” and details Toplevels are gone.
- Sidebar width is added to the window only while open, capped by the screen width; window size memory stores the width without the sidebar.

## 3.2.2
- Quick input accepts Chinese numbers for N days later (`三天后`, `两天后`, `十天后`); composite numbers such as `十一天后` still fall back to normal text.
- Strip the indicator words `这 / 这个 / 那 / 那个 / 下个` before weekday expressions (`这个周五` = this Friday, `下个周五` = next Friday) instead of leaving them in the title.
- Recognize repeating quick input: `每天`, `工作日` / `每个工作日`, `每周X`, `每周`, `每月X号`, `每月`; preview and stored todos get the `repeat` rule and `repeat_base`, and finishing one spawns the next through the existing repeat logic.
- Bare period words (`早上/上午/中午/下午/晚上/凌晨/半夜`) default to 09:00 / 12:00 / 15:00 / 19:00 / 06:00 / 00:00 when they are at the start, after a date, or at the end of the title; an explicit hour still wins.

## 3.2.1
- Append a non-selectable keyboard-navigation hint to every WinShun query result: “Ctrl+N 向下 · Ctrl+P 向上 · Ctrl+1~9 直达第几行” / “回车确认当前选中行”.
- Adding a todo from WinShun now also brings the sticky note forward with `focus=False`, so the new item is visible without stealing the keyboard focus; the “已添加” notification stays.

## 3.2.0
- New two-pane shell: 160px navigation rail plus main area; default 720×520, minimum 600×420, and saved widths below 600 are widened on show.
- The old segmented view switch is gone. Navigation drives the six filters (today / overdue / all / next 7 days / no date / done) and the week/month calendars; today shows its count and a “overdue X · due today Y” hint.
- Task rows are simplified: checkbox, title, deadline, tags and a priority pill by default; edit/reminder/subtask/delete appear on hover or keyboard focus. Double-click opens the shared details window.
- Unified colors: action blue `#4C6FFF`, overdue red `#DC2626`, reminder orange `#D97706`, secondary gray `#9AA0A6`; completed rows keep the gray strikethrough.
- Compact mode (config `compact`, tray menu and settings checkbox) switches rows to one line, smaller font and 2px padding; comfortable mode keeps two lines and 6px padding.
- High-DPI groundwork: Tk scaling is derived from the screen DPI (`winfo_fpixels("1i") / 72`, clamped 1.0–2.5); all sizing still derives from point fonts.

## 3.1.0
- Smart quick input: `db` text now recognizes relative dates, weekdays, month days, times, `#tags` and `!`/`紧急` priority, shows a preview row, and adds it only when Enter confirms. `quickparse.py` is a pure, offline parser; the original text row stays as a correction path.
- Advanced filtering: the note gets a filter row (all / today / overdue / next 7 days / no date / done), persisted as `config.json` `filter`, applied to list and today views together with search.
- Search operators shared by WinShun search and in-note search: `is:done`, `is:open`, `due:today` / `tomorrow` / `overdue` / `week` / `none`, and `priority:high` / `medium` / `low` / `none` / `0..3`.

## 3.0.8
- Track the hook thread and a per-install generation: `stop()` waits for the old thread, and a late `finally` can no longer unhook or clear a newer generation’s hook.
- Add regression tests for stop/join, the stop→restart race, and five lock/unlock cycles keeping exactly one live hook.
- A finished repeating task now appears in the calendar/today view only through its completion date; later days belong to the spawned instance. `repeat` and `repeat_base` stay on the old record.

## 3.0.7
- Complete 64-bit `argtypes`/`restype` for every Win32 call the mouse hook uses, including `UnhookWindowsHookEx` and `PostThreadMessageW`, so hook handles are never truncated.
- Keep the ctypes hook callback referenced for the whole process; `stop()` no longer releases it while a callback may still be running.
- Add regression tests for hook signatures, dedicated install thread, Tk-free callback, fallback `CallNextHookEx`, callback lifetime, and the cached window rectangle.
- Unchecking a parent task now reopens only the parent; completed children keep `done` and their `completed_at`. Checking a parent still completes the whole group.

## 3.0.6
- Reuse a single task-details window; repeated clicks focus it and selecting another task updates the same window.
- Make the month-view “more” dialog genuinely scrollable with mouse-wheel support; clicking a task closes the dialog, switches to the main list, and scrolls/highlights that task instead of toggling completion.
- Remove forced topmost behavior from the unlocked sticky note and child dialogs; the main note is topmost only while visible and locked.
- Avoid reapplying transparency on every show, restore normal z-order before hiding, and close transient calendar/details dialogs with the owner to reduce Windows repaint flashes and orphan windows.

## 3.0.5
- Preserve manually resized window dimensions while switching between list, today, week, and month views; restore the last normal window size after restart.
- Make view navigation clearer with a segmented control and a compact task-count pill.
- Typing only `db` / `todo` now displays the current todo list directly in Win顺, plus an action to open the full desktop window.
- Keep the full-list action available alongside text search results.

## 3.0.4
- Add global emergency unlock hotkey `Ctrl+Alt+U`, independent of the show/hide hotkey.
- Only enable click-through while the low-level mouse hook is confirmed active.
- Restore normal mouse input and stop the hook when the note is hidden; reinstall the hook before restoring click-through when shown again.
- Keep lock/unlock instructions visible while locked.

## 3.0.3
- Follow Win顺 light/dark appearance.
- Debounce in-note search and add a clear button.
- Highlight the active view switch.

## 3.0.1
- Mo