"""Lazy Tk loading, DPI scaling and opacity conversion."""

tk = None
ttk = None
messagebox = None
tkfont = None


def _load_tk():
    global tk, ttk, messagebox, tkfont
    if tk is not None:
        return
    import tkinter as _tk
    from tkinter import font as _tkfont
    from tkinter import messagebox as _messagebox
    from tkinter import ttk as _ttk

    tk = _tk
    ttk = _ttk
    messagebox = _messagebox
    tkfont = _tkfont


def dpi_scale(root):
    """Tk scaling from the actual screen DPI, clamped for readable layouts."""
    try:
        factor = float(root.winfo_fpixels("1i")) / 72.0
    except Exception:
        return 1.0
    return max(1.0, min(2.5, factor))


def _apply_dpi_scaling(root):
    factor = dpi_scale(root)
    try:
        root.tk.call("tk", "scaling", factor)
    except Exception:
        pass
    return factor


def _alpha_value(transparency):
    """50-100 -> 0.5-1.0, clamped."""
    try:
        value = int(transparency)
    except (TypeError, ValueError):
        value = 95
    value = max(50, min(100, value))
    return value / 100.0
