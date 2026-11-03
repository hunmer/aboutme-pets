"""Transparent Video Maker — colours and small Tk widget factories."""
import os
import re
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser

BG      = "#0d0f14"
PANEL   = "#141720"
PANEL2  = "#1a1e2a"
BORDER  = "#1e2330"
ACCENT  = "#e8622a"
ACCENT2 = "#f0a05a"
TEXT    = "#e8e8e8"
SUBTEXT = "#7a8099"
SUCCESS = "#3ecf8e"
WARNING = "#f0c040"
ERROR   = "#e85050"


def make_btn(parent, text, cmd, small=False, danger=False, accent=False):
    fg = ERROR if danger else (SUCCESS if accent else ACCENT)
    bg = "#1a0f0f" if danger else ("#0f1a12" if accent else "#1a1208")
    f  = ("Consolas", 9) if small else ("Consolas", 10, "bold")
    b  = tk.Button(parent, text=text, command=cmd, font=f,
                   fg=fg, bg=bg, activeforeground=TEXT,
                   activebackground=BORDER, relief="flat", bd=0,
                   cursor="hand2", highlightthickness=1,
                   highlightbackground=fg, padx=8, pady=4 if small else 6)
    b.bind("<Enter>", lambda e: b.config(bg=fg, fg=BG))
    b.bind("<Leave>", lambda e: b.config(bg=bg, fg=fg))
    return b


def make_section(parent, title, pady_top=14):
    f = tk.Frame(parent, bg=BG)
    f.pack(fill="x", padx=24, pady=(pady_top, 4))
    tk.Label(f, text=title, font=("Consolas", 10, "bold"),
             fg=ACCENT, bg=BG).pack(side="left")
    tk.Frame(f, bg=BORDER, height=1).pack(
        side="left", fill="x", expand=True, padx=10)


def make_entry_row(parent, label, var, browse_cb=None, lw=14):
    row = tk.Frame(parent, bg=BG)
    row.pack(fill="x", padx=24, pady=5)
    tk.Label(row, text=label, width=lw, anchor="w",
             font=("Consolas", 10), fg=SUBTEXT, bg=BG).pack(side="left")
    e = tk.Entry(row, textvariable=var, font=("Consolas", 10),
                 bg=PANEL, fg=TEXT, insertbackground=TEXT, relief="flat",
                 bd=0, highlightthickness=1,
                 highlightbackground=BORDER, highlightcolor=ACCENT)
    e.pack(side="left", fill="x", expand=True, ipady=5,
           padx=(0, 6 if browse_cb else 0))
    if browse_cb:
        make_btn(row, "Browse", browse_cb, small=True).pack(side="left")


def make_log(parent):
    frame = tk.Frame(parent, bg=PANEL, padx=2, pady=2,
                     highlightthickness=1, highlightbackground=BORDER)
    frame.pack(fill="both", expand=True, padx=24, pady=(0, 16))
    log = tk.Text(frame, font=("Consolas", 9), bg=PANEL, fg=TEXT,
                  insertbackground=TEXT, relief="flat", height=12,
                  state="disabled", wrap="word")
    sb = tk.Scrollbar(frame, command=log.yview)
    log.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    log.pack(fill="both", expand=True, padx=6, pady=4)
    for tag, col in [("ok", SUCCESS), ("err", ERROR),
                     ("warn", WARNING), ("head", ACCENT2)]:
        log.tag_configure(tag, foreground=col)
    return log


def scrollable(parent):
    canvas = tk.Canvas(parent, bg=BG, highlightthickness=0)
    sb     = tk.Scrollbar(parent, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    canvas.pack(fill="both", expand=True)
    inner  = tk.Frame(canvas, bg=BG)
    wid    = canvas.create_window((0, 0), window=inner, anchor="nw")
    inner.bind("<Configure>",
               lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>",
                lambda e: canvas.itemconfig(wid, width=e.width))
    inner.bind_all("<MouseWheel>",
                   lambda e: canvas.yview_scroll(-1*(e.delta//120), "units"))
    return inner
