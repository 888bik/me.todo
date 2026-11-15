import ast
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent

MODULES = [
    "sticky_window_theme",
    "sticky_window_dpi",
    "sticky_window_utils",
    "sticky_window_row",
    "sticky_window_details",
    "sticky_window_dialogs",
    "sticky_window_nav",
    "sticky_window_list",
    "sticky_window_calendar",
    "sticky_window_lock",
    "sticky_window_actions",
    "sticky_window",
]

PUBLIC_NAMES = [
    "show", "hide", "refresh", "is_visible", "apply_settings", "apply_appearance",
    "set_locked", "set_compact", "show_stats", "export_todos", "import_todos",
    "_StickyWindow", "_TaskRow", "_DetailsPanel", "_FontSettings",
    "_ReminderPicker", "_StatsDialog", "_load_tk", "dpi_scale", "_alpha_value",
    "_color", "_set_palette", "_month_grid", "_dialog_position", "_needs_scroll",
    "_short_when", "size_for_view", "BG", "CARD", "BORDER", "TEXT", "ACCENT",
    "PRIORITY_COLORS", "PRIORITY_LABELS", "FONT_FAMILY", "tk", "tkfont",
    "_set_click_through", "_hide_from_taskbar", "_current_appearance",
    "_current_theme",
]


class StructureTests(unittest.TestCase):
    def test_every_module_imports_standalone(self):
        for module in MODULES:
            with self.subTest(module=module):
                result = subprocess.run(
                    [sys.executable, "-c", "import %s" % module],
                    cwd=HERE, capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_module_imports_tkinter_at_top_level(self):
        for module in MODULES:
            tree = ast.parse((HERE / (module + ".py")).read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                self.assertFalse(
                    any(name == "tkinter" or name.startswith("tkinter.")
                        for name in names),
                    "%s imports tkinter at module level" % module,
                )

    def test_facade_keeps_public_api_and_classes(self):
        import sticky_window

        for name in PUBLIC_NAMES:
            self.assertTrue(hasattr(sticky_window, name), name)

    def test_facade_class_is_built_from_all_six_mixins(self):
        import sticky_window

        base_names = {base.__name__ for base in sticky_window._StickyWindow.__mro__}
        self.assertTrue({
            "DetailsMixin", "NavMixin", "ListMixin", "CalendarMixin",
            "LockMixin", "ActionsMixin",
        }.issubset(base_names))
