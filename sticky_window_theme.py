"""Colors, palettes and font tuples for the sticky note."""

__all__ = ['BG', 'CARD', 'BORDER', 'HOVER', 'TEXT', 'MUTED', 'ACCENT', 'ACCENT_DARK', 'CLOCK', 'DEADLINE_FG', 'DANGER', 'TODAY_BG', 'ACCENT_FILL', 'ACCENT_TEXT', 'PRIORITY_COLORS', 'PRIORITY_LABELS', 'FONT_FAMILY', '_LIGHT_PALETTE', '_DARK_PALETTE', '_PALETTE_KEYS', '_current_appearance', '_current_theme', '_color', '_palette_snapshot', '_set_palette', '_recolor_widget_tree', '_font']

_palette_targets = []


def register_palette_module(namespace):
    """Let modules using ``from theme import *`` follow runtime palette swaps."""
    if namespace not in _palette_targets:
        _palette_targets.append(namespace)


BG = "#FFFDF8"
CARD = "#FFFFFF"
BORDER = "#ECE6D8"
HOVER = "#DDD5C2"
TEXT = "#2E2A24"
MUTED = "#9AA0A6"
ACCENT = "#4C6FFF"
ACCENT_DARK = "#3B5AE0"
CLOCK = "#D97706"
DEADLINE_FG = "#2E2A24"
DANGER = "#DC2626"
TODAY_BG = "#EEF1FF"
ACCENT_FILL = "#4C6FFF"
ACCENT_TEXT = "#FFFFFF"
PRIORITY_COLORS = {1: "#9AA0A6", 2: "#D97706", 3: "#DC2626"}
PRIORITY_LABELS = {1: "低", 2: "中", 3: "高"}
FONT_FAMILY = "Microsoft YaHei"
_LIGHT_PALETTE = {
    "BG": "#FFFDF8", "CARD": "#FFFFFF", "BORDER": "#ECE6D8", "HOVER": "#DDD5C2",
    "TEXT": "#2E2A24", "MUTED": "#9AA0A6", "ACCENT": "#4C6FFF",
    "ACCENT_DARK": "#3B5AE0", "CLOCK": "#D97706", "DEADLINE_FG": "#2E2A24",
    "DANGER": "#DC2626", "TODAY_BG": "#EEF1FF", "ACCENT_FILL": "#4C6FFF",
    "ACCENT_TEXT": "#FFFFFF",
}
_DARK_PALETTE = {
    "BG": "#202124", "CARD": "#292A2D", "BORDER": "#3A3B40", "HOVER": "#4A4B52",
    "TEXT": "#E8EAED", "MUTED": "#A1A6AD", "ACCENT": "#8AB4F8",
    "ACCENT_DARK": "#669DF6", "CLOCK": "#E2A44F", "DEADLINE_FG": "#D9A665",
    "DANGER": "#F28B82", "TODAY_BG": "#303D57", "ACCENT_FILL": "#8AB4F8",
    "ACCENT_TEXT": "#202124",
}
_PALETTE_KEYS = tuple(_LIGHT_PALETTE)
_current_appearance = {}
_current_theme = "light"


def _color(value, fallback, backdrop):
    """Convert Win顺 #AARRGGBB / Tk #RRGGBB colors to opaque Tk colors."""
    if not isinstance(value, str):
        return fallback
    value = value.strip()
    if len(value) == 7 and value.startswith("#"):
        return value.upper()
    if len(value) == 9 and value.startswith("#"):
        try:
            alpha = int(value[1:3], 16) / 255.0
            foreground = tuple(int(value[index:index + 2], 16) for index in (3, 5, 7))
            background = tuple(int(backdrop[index:index + 2], 16) for index in (1, 3, 5))
            mixed = tuple(round(fg * alpha + bg * (1.0 - alpha)) for fg, bg in zip(foreground, background))
            return "#%02X%02X%02X" % mixed
        except (TypeError, ValueError):
            return fallback
    return fallback


def _palette_snapshot():
    return {key: globals()[key] for key in _PALETTE_KEYS}


def _set_palette(appearance=None, theme="light"):
    """Use the host's nativeLook colors when available; otherwise use a safe palette."""
    global BG, CARD, BORDER, HOVER, TEXT, MUTED, ACCENT, ACCENT_DARK
    global CLOCK, DEADLINE_FG, DANGER, TODAY_BG, ACCENT_FILL, ACCENT_TEXT
    global _current_appearance, _current_theme

    _current_appearance = dict(appearance or {})
    _current_theme = "dark" if str(theme).lower() == "dark" else "light"
    base = dict(_DARK_PALETTE if _current_theme == "dark" else _LIGHT_PALETTE)
    native = _current_appearance.get("nativeLook")
    if not isinstance(native, dict):
        native = {}

    # Resolve the base surface first, then composite translucent native colors
    # over it. Tk widgets expect opaque RGB values.
    base_bg = _color(native.get("background"), base["BG"], base["BG"])
    mapping = {
        "BG": "background", "CARD": "layer", "BORDER": "divider", "HOVER": "hover",
        "TEXT": "text", "MUTED": "subtext", "ACCENT": "accent", "ACCENT_DARK": "pressed",
        "DANGER": "danger", "TODAY_BG": "tagFill", "ACCENT_FILL": "accentFill",
        "ACCENT_TEXT": "accentText",
    }
    base["BG"] = base_bg
    for key, native_key in mapping.items():
        if key == "BG":
            continue
        base[key] = _color(native.get(native_key), base[key], base_bg)

    # nativeLook does not define warning/deadline colors; keep these legible
    # in both themes while using the host's danger color for overdue items.
    base["CLOCK"] = _color(native.get("accent"), base["CLOCK"], base_bg)
    base["DEADLINE_FG"] = base["MUTED"] if native else base["DEADLINE_FG"]

    for key, value in base.items():
        globals()[key] = value
    for target in _palette_targets:
        target.update(base)


def _recolor_widget_tree(widget, color_map):
    """Remap existing Tk widgets in-place so an appearance change is lossless."""
    options = (
        "background", "bg", "foreground", "fg", "activebackground", "activeforeground",
        "highlightbackground", "highlightcolor", "insertbackground", "selectbackground",
        "selectforeground", "disabledforeground",
    )
    try:
        for option in options:
            try:
                current = widget.cget(option)
            except Exception:
                continue
            replacement = color_map.get(str(current).upper())
            if replacement:
                try:
                    widget.configure(**{option: replacement})
                except Exception:
                    pass
        children = widget.winfo_children()
    except Exception:
        return
    for child in children:
        _recolor_widget_tree(child, color_map)


def _font(size, bold=False, overstrike=False):
    """Every label in the note derives from the one font size the gear sets."""
    styles = []
    if bold:
        styles.append("bold")
    if overstrike:
        styles.append("overstrike")
    return (FONT_FAMILY, int(size)) + tuple(styles)

