"""Tkinter desktop UI for the Smart Form Filler."""
from __future__ import annotations

import ctypes
import os
import re
import sys
import threading
import time
import tkinter as tk
import traceback
import urllib.parse
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Dict, List, Optional, Sequence

from .cdp import BrowserSession, CDPError, PageTarget
from .engine import FieldMatch, FormEngine
from .fields import FIELD_DEFS, GROUP_ORDER, FieldDef, human_key, normalize
from .storage import Storage

APP_TITLE = "智填 · 网页表单自动填写"
APP_VERSION = "1.0.1"

NAVY = "#101b33"
NAVY_SOFT = "#1b2a4a"
ACCENT = "#2563eb"
ACCENT_HOVER = "#1d4ed8"
BG = "#f3f6fb"
CARD = "#ffffff"
INK = "#17233c"
MUTED = "#6b7a93"
LINE = "#dce4f0"
SOFT = "#eef3fa"
GREEN = "#16a34a"
RED = "#dc2626"
AMBER = "#d97706"


def resource_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / relative


def enable_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class ScrollableFrame(ttk.Frame):
    """A vertically scrollable white panel used by the profile tabs."""

    def __init__(self, parent: tk.Misc, **kwargs: Any) -> None:
        super().__init__(parent, **kwargs)
        self.canvas = tk.Canvas(self, bg=CARD, highlightthickness=0, bd=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas, style="Card.TFrame")
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)

    def _on_inner_configure(self, _event: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self._window, width=max(1, event.width))

    def _bind_wheel(self, _event: tk.Event) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self, _event: tk.Event) -> None:
        self.canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event: tk.Event) -> None:
        delta = int(-1 * (event.delta / 120)) if event.delta else 0
        self.canvas.yview_scroll(delta, "units")


class SmartFormFillerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        # Fonts are measured in points and Tk already scales them with the
        # monitor DPI.  Layout dimensions are pixels, so scale those too;
        # otherwise a 1280px window looks cramped on a 150%/200% display.
        try:
            tk_scaling = float(root.tk.call("tk", "scaling"))
        except Exception:
            tk_scaling = 1.3333
        self.scale = max(1.0, min(2.0, tk_scaling / 1.3333))
        self.storage = Storage()
        self.settings = self.storage.settings
        self.browser = BrowserSession(
            self.storage.home,
            self.settings,
            headless=False,
        )
        self.engine = FormEngine(self.browser)

        self.vars: Dict[str, tk.StringVar] = {}
        self.matches: List[FieldMatch] = []
        self.host = ""
        self.current_url = ""
        self.current_title = ""
        self.busy = False
        self.ui_logs: List[str] = []
        self._busy_buttons: List[ttk.Button] = []
        self._mapping_keys: List[Optional[str]] = []
        self._mapping_labels: List[str] = []

        self._configure_window()
        self._configure_style()
        self._build_menu()
        self._build_ui()
        self._refresh_mapping_choices()
        self._load_profile_into_ui()
        self._load_last_url()
        self._update_connection_state()
        self._sync_logs()
        if getattr(self.storage, "used_fallback", False):
            self._log_ui(
                "系统不允许写入 AppData，已自动改用可写数据目录："
                + str(self.storage.home)
            )
        self._log_ui("就绪。资料仅保存在本机：" + str(self.storage.home))

    # ------------------------------------------------------------------ setup
    def _configure_window(self) -> None:
        self.root.title(f"{APP_TITLE}  v{APP_VERSION}")
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        min_w = int(1060 * self.scale)
        min_h = int(680 * self.scale)
        self.root.minsize(min(min_w, screen_w - 40), min(min_h, screen_h - 60))
        width = min(int(1280 * self.scale), max(min_w, screen_w - 80))
        height = min(int(800 * self.scale), max(min_h, screen_h - 210))
        x = max(10, (screen_w - width) // 2)
        y = max(10, (screen_h - height) // 2 - int(60 * self.scale))
        geometry = str(self.settings.get("window_geometry") or "")
        match = re.match(r"^(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$", geometry)
        if match:
            w, h, gx, gy = (int(v) for v in match.groups())
            if 900 <= w <= screen_w and 640 <= h <= screen_h and 0 <= gx < screen_w - 100 and 0 <= gy < screen_h - 100:
                width = min(w, screen_w - 80)
                height = min(h, screen_h - 210)
                x, y = gx, min(gy, screen_h - height - 150)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.configure(bg=BG)
        icon = resource_path("assets/icon.ico")
        if icon.is_file():
            try:
                self.root.iconbitmap(default=str(icon))
            except tk.TclError:
                pass
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.report_callback_exception = self._report_callback_exception

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        font = ("Microsoft YaHei UI", 10)
        small = ("Microsoft YaHei UI", 9)
        style.configure(".", font=font, background=BG, foreground=INK)
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("Soft.TFrame", background=SOFT)
        style.configure("Header.TFrame", background=NAVY)
        style.configure("TLabel", background=BG, foreground=INK, font=font)
        style.configure("Card.TLabel", background=CARD, foreground=INK, font=font)
        style.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=small)
        style.configure("HeaderTitle.TLabel", background=NAVY, foreground="#ffffff",
                        font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("HeaderSub.TLabel", background=NAVY, foreground="#a9b9d8", font=small)
        style.configure("Section.TLabel", background=CARD, foreground=INK,
                        font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("FormLabel.TLabel", background=CARD, foreground="#34435d",
                        font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Hint.TLabel", background=CARD, foreground=MUTED, font=("Microsoft YaHei UI", 8))
        style.configure("Status.TLabel", background="#e8eef8", foreground="#4a5a75", font=small)
        style.configure("TNotebook", background=CARD, borderwidth=0, tabmargins=(0, 4, 0, 0))
        style.configure("TNotebook.Tab", background="#e8eef8", foreground="#53627c",
                        padding=(9, 6), font=("Microsoft YaHei UI", 9, "bold"), borderwidth=0)
        style.map("TNotebook.Tab",
                  background=[("selected", CARD)],
                  foreground=[("selected", ACCENT)],
                  expand=[("selected", (0, 0, 0, 2))])
        style.configure("TEntry", padding=(8, 6), fieldbackground="#fbfdff",
                        borderwidth=1, relief="solid")
        style.map("TEntry", fieldbackground=[("focus", "#ffffff")])
        style.configure("TCombobox", padding=(6, 5))
        style.configure("TCheckbutton", background=CARD, foreground="#43516b", font=small)
        style.map("TCheckbutton", background=[("active", CARD)])
        style.configure("Soft.TCheckbutton", background=SOFT, foreground="#43516b", font=small)
        style.map("Soft.TCheckbutton", background=[("active", SOFT)])
        style.configure("Primary.TButton", background=ACCENT, foreground="#ffffff",
                        borderwidth=0, padding=(14, 8), focusthickness=0,
                        font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Primary.TButton",
                  background=[("active", ACCENT_HOVER), ("disabled", "#9db9ee")],
                  foreground=[("disabled", "#ffffff")])
        style.configure("Ghost.TButton", background=CARD, foreground="#33415c",
                        borderwidth=1, relief="solid", padding=(11, 7), font=small)
        style.map("Ghost.TButton", background=[("active", "#f1f6ff")])
        style.configure("Tiny.TButton", background=CARD, foreground="#3b4b66",
                        borderwidth=0, padding=(6, 4), font=small)
        style.map("Tiny.TButton", background=[("active", "#eef4ff")])
        style.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=INK,
                        rowheight=int(30 * self.scale), borderwidth=0, font=("Microsoft YaHei UI", 9))
        style.configure("Treeview.Heading", background=SOFT, foreground="#455573",
                        relief="flat", font=("Microsoft YaHei UI", 9, "bold"), padding=(6, 6))
        style.map("Treeview.Heading", background=[("active", "#e3ecf9")])
        style.map("Treeview", background=[("selected", "#dbeafe")],
                  foreground=[("selected", "#173a7a")])
        style.configure("TScrollbar", width=11, arrowsize=11)
        style.configure("TPanedwindow", background=BG)

    def _build_menu(self) -> None:
        menu = tk.Menu(self.root, tearoff=0, bg="#ffffff", fg=INK,
                       activebackground="#eaf2ff", activeforeground=ACCENT)
        file_menu = tk.Menu(menu, tearoff=0, bg="#ffffff", fg=INK,
                            activebackground="#eaf2ff", activeforeground=ACCENT)
        file_menu.add_command(label="导入资料…", command=self.import_profile, accelerator="Ctrl+O")
        file_menu.add_command(label="导出资料…", command=self.export_profile, accelerator="Ctrl+E")
        file_menu.add_separator()
        file_menu.add_command(label="打开数据目录", command=self.open_data_dir)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self._on_close)
        menu.add_cascade(label="文件", menu=file_menu)

        browser_menu = tk.Menu(menu, tearoff=0, bg="#ffffff", fg=INK,
                               activebackground="#eaf2ff", activeforeground=ACCENT)
        browser_menu.add_command(label="重新连接浏览器", command=self.reconnect)
        browser_menu.add_command(label="选择标签页…", command=self.choose_tab)
        browser_menu.add_command(label="刷新当前页面", command=self.reload_page)
        browser_menu.add_separator()
        browser_menu.add_command(label="关闭已启动的浏览器", command=self.close_browser)
        browser_menu.add_command(label="清除本站识别规则", command=self.clear_site_rules)
        menu.add_cascade(label="浏览器", menu=browser_menu)

        tools_menu = tk.Menu(menu, tearoff=0, bg="#ffffff", fg=INK,
                             activebackground="#eaf2ff", activeforeground=ACCENT)
        tools_menu.add_command(label="设置…", command=self.open_settings)
        tools_menu.add_command(label="导入示例页面", command=self.load_welcome_page)
        menu.add_cascade(label="工具", menu=tools_menu)

        help_menu = tk.Menu(menu, tearoff=0, bg="#ffffff", fg=INK,
                            activebackground="#eaf2ff", activeforeground=ACCENT)
        help_menu.add_command(label="使用说明", command=self.show_help)
        help_menu.add_command(label="关于", command=self.show_about)
        menu.add_cascade(label="帮助", menu=help_menu)
        self.root.config(menu=menu)

        self.root.bind("<Control-o>", lambda _e: self.import_profile())
        self.root.bind("<Control-e>", lambda _e: self.export_profile())
        self.root.bind("<F5>", lambda _e: self.scan_page())
        self.root.bind("<F6>", lambda _e: self.fill_page())
        self.root.bind("<F1>", lambda _e: self.show_help())

    def _build_ui(self) -> None:
        self._build_header()
        self._build_browser_bar()
        # Pack the fixed bottom bars before the expanding body so they can
        # never be pushed outside the window on shorter screens.
        self._build_status_bar()
        self._build_log_panel()
        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True, padx=14, pady=(2, 4))
        body.columnconfigure(0, weight=0, minsize=int(450 * self.scale))
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        self._build_profile_panel(body)
        self._build_results_panel(body)

    def _build_header(self) -> None:
        header = tk.Frame(self.root, bg=NAVY, height=int(82 * self.scale))
        header.pack(fill="x")
        header.pack_propagate(False)
        left = tk.Frame(header, bg=NAVY)
        left.pack(side="left", padx=20, pady=14)
        badge = tk.Label(left, text="智", bg=ACCENT, fg="#ffffff",
                         font=("Microsoft YaHei UI", 19, "bold"), width=2, height=1)
        badge.pack(side="left", padx=(0, 12))
        text = tk.Frame(left, bg=NAVY)
        text.pack(side="left")
        tk.Label(text, text="智填 · 网页表单自动填写", bg=NAVY, fg="#ffffff",
                 font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w")
        tk.Label(text, text="自动识别页面字段 · 一键填表 · 本地运行 · 数据不上传",
                 bg=NAVY, fg="#9fb3d6", font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(3, 0))
        right = tk.Frame(header, bg=NAVY)
        right.pack(side="right", padx=20, pady=18)
        self.conn_label = tk.Label(right, text="● 浏览器未连接", bg=NAVY, fg="#91a4c6",
                                   font=("Microsoft YaHei UI", 9, "bold"))
        self.conn_label.pack(anchor="e")
        self.page_label = tk.Label(right, text="尚未打开页面", bg=NAVY, fg="#7186a8",
                                   font=("Microsoft YaHei UI", 8))
        self.page_label.pack(anchor="e", pady=(4, 0))

    def _build_browser_bar(self) -> None:
        bar = ttk.Frame(self.root, style="Card.TFrame", padding=(14, 10))
        bar.pack(fill="x", padx=14, pady=(10, 6))
        bar.columnconfigure(1, weight=1)
        ttk.Label(bar, text="网址", style="Card.TLabel",
                  font=("Microsoft YaHei UI", 9, "bold")).grid(row=0, column=0, padx=(2, 8), sticky="w")
        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(bar, textvariable=self.url_var, font=("Consolas", 10))
        self.url_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.url_entry.bind("<Return>", lambda _e: self.open_url())
        self.open_btn = ttk.Button(bar, text="打开并连接", style="Primary.TButton", command=self.open_url)
        self.open_btn.grid(row=0, column=2, padx=(0, 6))
        self.reload_btn = ttk.Button(bar, text="刷新", style="Ghost.TButton", command=self.reload_page)
        self.reload_btn.grid(row=0, column=3, padx=(0, 6))
        self.connect_btn = ttk.Button(bar, text="重新连接", style="Ghost.TButton", command=self.reconnect)
        self.connect_btn.grid(row=0, column=4, padx=(0, 6))
        self.settings_btn = ttk.Button(bar, text="设置", style="Ghost.TButton", command=self.open_settings)
        self.settings_btn.grid(row=0, column=5)
        self._busy_buttons.extend([self.open_btn, self.reload_btn, self.connect_btn])

    def _build_profile_panel(self, parent: ttk.Frame) -> None:
        wrapper = ttk.Frame(parent, width=int(450 * self.scale), height=1)
        wrapper.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        wrapper.grid_propagate(False)
        wrapper.pack_propagate(False)
        card = ttk.Frame(wrapper, style="Card.TFrame", padding=(14, 12))
        card.pack(fill="both", expand=True)
        card.columnconfigure(0, weight=1)
        card.rowconfigure(1, weight=1)
        head = ttk.Frame(card, style="Card.TFrame")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(head, text="我的资料", style="Section.TLabel").pack(side="left")
        self.save_btn = ttk.Button(head, text="保存资料", style="Tiny.TButton", command=self.save_profile)
        self.save_btn.pack(side="right")
        self.profile_note = ttk.Label(head, text="填写一次，重复使用", style="Muted.TLabel")
        self.profile_note.pack(side="right", padx=(0, 8))

        self.notebook = ttk.Notebook(card)
        self.notebook.grid(row=1, column=0, sticky="nsew")
        defs = self.storage.field_defs()
        for group in GROUP_ORDER:
            fields = [fd for fd in defs if not fd.custom and fd.group == group]
            if not fields:
                continue
            tab = ttk.Frame(self.notebook, style="Card.TFrame")
            self.notebook.add(tab, text=group)
            scroll = ScrollableFrame(tab)
            scroll.pack(fill="both", expand=True, padx=2, pady=(8, 2))
            self._build_field_rows(scroll.inner, fields)
        custom_tab = ttk.Frame(self.notebook, style="Card.TFrame")
        self.notebook.add(custom_tab, text="自定义")
        self._build_custom_tab(custom_tab)

        footer = ttk.Frame(card, style="Card.TFrame")
        footer.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(footer, text="导入资料", style="Ghost.TButton",
                   command=self.import_profile).pack(side="left")
        ttk.Button(footer, text="导出资料", style="Ghost.TButton",
                   command=self.export_profile).pack(side="left", padx=(6, 0))
        ttk.Label(footer, text="支持 JSON 备份", style="Muted.TLabel").pack(side="right")

    def _build_field_rows(self, parent: ttk.Frame, fields: Sequence[FieldDef]) -> None:
        parent.columnconfigure(0, weight=1)
        for index, fd in enumerate(fields):
            row = ttk.Frame(parent, style="Card.TFrame")
            row.grid(row=index, column=0, sticky="ew", pady=(0, 10))
            row.columnconfigure(0, weight=1)
            ttk.Label(row, text=fd.label, style="FormLabel.TLabel").grid(row=0, column=0, sticky="w")
            var = tk.StringVar()
            self.vars[fd.key] = var
            if fd.key in ("gender", "degree", "agree"):
                values = {
                    "gender": ["", "男", "女", "其他", "保密"],
                    "degree": ["", "高中", "大专", "本科", "硕士", "博士"],
                    "agree": ["", "是", "否"],
                }[fd.key]
                widget: tk.Widget = ttk.Combobox(row, textvariable=var, values=values,
                                                 state="normal", font=("Microsoft YaHei UI", 9))
            else:
                widget = ttk.Entry(row, textvariable=var, show="●" if fd.key == "password" else "",
                                   font=("Microsoft YaHei UI", 9))
            widget.grid(row=1, column=0, sticky="ew", pady=(4, 0))
            if fd.key in ("id_number", "password", "agree") and fd.hint:
                ttk.Label(row, text=fd.hint, style="Hint.TLabel").grid(
                    row=2, column=0, sticky="w", pady=(3, 0)
                )

    def _build_custom_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        info = ttk.Label(parent, text="用于识别标准资料之外的字段，例如：期望薪资、车牌号、客户编号。",
                         style="Muted.TLabel", wraplength=int(380 * self.scale), justify="left")
        info.grid(row=0, column=0, sticky="w", padx=4, pady=(10, 6))
        wrap = ttk.Frame(parent, style="Card.TFrame")
        wrap.grid(row=1, column=0, sticky="nsew", padx=2)
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)
        self.custom_tree = ttk.Treeview(wrap, columns=("label", "value"), show="headings", height=9)
        self.custom_tree.heading("label", text="字段名")
        self.custom_tree.heading("value", text="内容")
        self.custom_tree.column("label", width=int(150 * self.scale), anchor="w")
        self.custom_tree.column("value", width=int(210 * self.scale), anchor="w")
        self.custom_tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(wrap, orient="vertical", command=self.custom_tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.custom_tree.configure(yscrollcommand=scroll.set)
        self.custom_tree.bind("<Double-1>", lambda _e: self.edit_custom_field())
        buttons = ttk.Frame(parent, style="Card.TFrame")
        buttons.grid(row=2, column=0, sticky="ew", padx=2, pady=(8, 8))
        ttk.Button(buttons, text="添加字段", style="Ghost.TButton",
                   command=self.add_custom_field).pack(side="left")
        ttk.Button(buttons, text="编辑", style="Ghost.TButton",
                   command=self.edit_custom_field).pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text="删除", style="Ghost.TButton",
                   command=self.delete_custom_field).pack(side="left", padx=(6, 0))
        self.custom_count_label = ttk.Label(buttons, text="", style="Muted.TLabel")
        self.custom_count_label.pack(side="right")

    def _build_results_panel(self, parent: ttk.Frame) -> None:
        card = ttk.Frame(parent, style="Card.TFrame", padding=(14, 12))
        card.grid(row=0, column=1, sticky="nsew")
        card.columnconfigure(0, weight=1)
        card.rowconfigure(2, weight=1)

        head = ttk.Frame(card, style="Card.TFrame")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        head.columnconfigure(0, weight=1)
        title_wrap = ttk.Frame(head, style="Card.TFrame")
        title_wrap.grid(row=0, column=0, sticky="w")
        ttk.Label(title_wrap, text="页面字段识别", style="Section.TLabel").pack(side="left")
        self.scan_info = ttk.Label(title_wrap, text="尚未扫描", style="Muted.TLabel")
        self.scan_info.pack(side="left", padx=(10, 0), pady=(3, 0))
        actions = ttk.Frame(head, style="Card.TFrame")
        actions.grid(row=0, column=1, sticky="e")
        self.scan_btn = ttk.Button(actions, text="扫描页面  F5", style="Primary.TButton",
                                   command=self.scan_page)
        self.scan_btn.pack(side="left", padx=(0, 6))
        self.fill_btn = ttk.Button(actions, text="智能填写  F6", style="Primary.TButton",
                                   command=self.fill_page)
        self.fill_btn.pack(side="left")
        self._busy_buttons.extend([self.scan_btn, self.fill_btn])

        options = ttk.Frame(card, style="Card.TFrame")
        options.grid(row=1, column=0, sticky="w", pady=(0, 8))
        self.fill_empty_var = tk.BooleanVar(value=bool(self.settings.get("fill_empty_only", True)))
        self.auto_scan_var = tk.BooleanVar(value=bool(self.settings.get("auto_scan", True)))
        self.remember_var = tk.BooleanVar(value=bool(self.settings.get("remember_rules", True)))
        ttk.Checkbutton(options, text="仅填写空白字段", variable=self.fill_empty_var,
                        command=self._save_option_flags).pack(side="left")
        ttk.Checkbutton(options, text="打开页面后自动扫描", variable=self.auto_scan_var,
                        command=self._save_option_flags).pack(side="left", padx=(12, 0))

        tree_wrap = ttk.Frame(card, style="Card.TFrame")
        tree_wrap.grid(row=2, column=0, sticky="nsew")
        tree_wrap.columnconfigure(0, weight=1)
        tree_wrap.rowconfigure(0, weight=1)
        columns = ("field", "mapped", "confidence", "status", "value")
        self.tree = ttk.Treeview(tree_wrap, columns=columns, show="headings", selectmode="browse")
        headings = {
            "field": ("页面字段", 260),
            "mapped": ("识别为", 110),
            "confidence": ("置信度", 72),
            "status": ("状态", 84),
            "value": ("页面当前值", 150),
        }
        for col, (text, width) in headings.items():
            self.tree.heading(col, text=text)
            self.tree.column(col, width=int(width * self.scale), anchor="w",
                             stretch=(col in ("field", "value")))
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        hscroll = ttk.Scrollbar(tree_wrap, orient="horizontal", command=self.tree.xview)
        hscroll.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=scroll.set, xscrollcommand=hscroll.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.tag_configure("matched", foreground=INK)
        self.tree.tag_configure("unmatched", foreground="#97a3b6")
        self.tree.tag_configure("filled", foreground=GREEN)
        self.tree.tag_configure("failed", foreground=RED)
        self.tree.tag_configure("ignored", foreground="#a5afbf")

        self.empty_hint = ttk.Label(
            tree_wrap,
            text="还没有扫描页面\n\n先在左侧填写资料，点击「扫描页面」\n确认识别结果后，再点击「智能填写」",
            style="Muted.TLabel", justify="center", anchor="center",
        )
        self.empty_hint.place(relx=0.5, rely=0.42, anchor="center")

        detail = ttk.Frame(card, style="Soft.TFrame", padding=(10, 8))
        detail.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        detail.columnconfigure(0, weight=1)
        self.detail_label = ttk.Label(detail, text="选中一行查看识别依据，也可以手动改映射。",
                                      style="Muted.TLabel", background=SOFT,
                                      wraplength=int(690 * self.scale), justify="left")
        self.detail_label.grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(detail, text="映射为", background=SOFT, foreground="#35445f",
                  font=("Microsoft YaHei UI", 9, "bold")).grid(row=1, column=0, sticky="w", pady=(7, 0))
        self.mapping_combo = ttk.Combobox(detail, state="readonly", width=26,
                                          font=("Microsoft YaHei UI", 9))
        self.mapping_combo.grid(row=1, column=1, sticky="w", padx=(8, 6), pady=(7, 0))
        self.apply_map_btn = ttk.Button(detail, text="应用映射", style="Ghost.TButton",
                                        command=self.apply_mapping)
        self.apply_map_btn.grid(row=1, column=2, padx=(0, 6), pady=(7, 0))
        self.ignore_btn = ttk.Button(detail, text="忽略此字段", style="Ghost.TButton",
                                     command=self.ignore_field)
        self.ignore_btn.grid(row=1, column=3, pady=(7, 0))
        ttk.Checkbutton(detail, text="记住本站规则", variable=self.remember_var,
                        style="Soft.TCheckbutton",
                        command=self._save_option_flags).grid(row=2, column=1, sticky="w",
                                                             padx=(8, 0), pady=(4, 0))
        self._set_detail_enabled(False)

    def _build_log_panel(self) -> None:
        frame = ttk.Frame(self.root, style="Card.TFrame", padding=(12, 8))
        frame.pack(side="bottom", fill="x", padx=14, pady=(0, 6))
        head = ttk.Frame(frame, style="Card.TFrame")
        head.pack(fill="x")
        ttk.Label(head, text="运行日志", style="Card.TLabel",
                  font=("Microsoft YaHei UI", 9, "bold")).pack(side="left")
        ttk.Button(head, text="清空", style="Tiny.TButton",
                   command=self._clear_logs).pack(side="right")
        self.log_text = tk.Text(frame, height=5, bg="#0c1526", fg="#cfe0ff", relief="flat",
                                font=("Consolas", 9), padx=10, pady=6, wrap="none",
                                insertbackground="#ffffff")
        self.log_text.pack(fill="x", pady=(5, 0))
        self.log_text.configure(state="disabled")

    def _build_status_bar(self) -> None:
        bar = tk.Frame(self.root, bg="#e8eef8", height=27)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)
        self.status_var = tk.StringVar(value="就绪")
        tk.Label(bar, textvariable=self.status_var, bg="#e8eef8", fg="#40516d",
                 font=("Microsoft YaHei UI", 9), anchor="w").pack(side="left", padx=14)
        tk.Label(bar, text="本地保存 · F5 扫描 · F6 填写", bg="#e8eef8", fg="#7787a1",
                 font=("Microsoft YaHei UI", 8)).pack(side="right", padx=14)

    # ------------------------------------------------------------------ data / UI state
    def _load_profile_into_ui(self) -> None:
        for key, var in self.vars.items():
            var.set(self.storage.get_value(key))
        self._refresh_custom_tree()

    def _refresh_custom_tree(self) -> None:
        for item in self.custom_tree.get_children():
            self.custom_tree.delete(item)
        for item in self.storage.custom_fields:
            self.custom_tree.insert(
                "", "end", iid=item["key"],
                values=(item["label"], self.storage.get_value(item["key"])),
            )
        count = len(self.storage.custom_fields)
        self.custom_count_label.configure(text=f"共 {count} 个" if count else "暂无扩展字段")
        self.mapping_combo.configure(values=self._mapping_labels or ["— 不填写 —"])

    def _collect_profile(self) -> Dict[str, Any]:
        values: Dict[str, Any] = {}
        for key, var in self.vars.items():
            values[key] = var.get().strip()
        # Standardize a birthday typed as 1990/5/20, which makes date inputs work.
        birthday = values.get("birthday", "")
        match = re.match(r"^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?$", birthday)
        if match:
            values["birthday"] = f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
        if values.get("id_number"):
            values["id_number"] = values["id_number"].upper()
        return values

    def save_profile(self) -> None:
        self.storage.set_values(self._collect_profile())
        self._log_ui("资料已保存")
        self.status_var.set("资料已保存")

    def _save_option_flags(self) -> None:
        self.settings["fill_empty_only"] = bool(self.fill_empty_var.get())
        self.settings["auto_scan"] = bool(self.auto_scan_var.get())
        self.settings["remember_rules"] = bool(self.remember_var.get())
        self.storage.save_settings()

    def _load_last_url(self) -> None:
        last = str(self.settings.get("last_url") or "").strip()
        welcome = resource_path("assets/welcome.html")
        if not last and welcome.is_file():
            last = welcome.as_uri()
        self.url_var.set(last)

    def load_welcome_page(self) -> None:
        welcome = resource_path("assets/welcome.html")
        if not welcome.is_file():
            messagebox.showerror("示例页面缺失", "安装包中缺少 assets/welcome.html。")
            return
        self.url_var.set(welcome.as_uri())
        self.open_url()

    # ------------------------------------------------------------------ connection
    def _update_connection_state(self) -> None:
        if self.browser.connected:
            name = Path(self.browser.browser_path or "浏览器").stem
            name = "Edge" if "msedge" in name.lower() else ("Chrome" if "chrome" in name.lower() else name)
            self.conn_label.configure(text=f"● 已连接 {name}", fg="#5ee095")
            display = self.current_title or self.current_url or "尚未打开页面"
            self.page_label.configure(text=display[:74])
        else:
            self.conn_label.configure(text="● 浏览器未连接", fg="#91a4c6")
            self.page_label.configure(text="尚未打开页面")

    def _normalize_url(self, raw: str) -> str:
        raw = (raw or "").strip().strip('"')
        if not raw:
            raise ValueError("请输入要打开的网址")
        lower = raw.lower()
        if lower.startswith(("http://", "https://", "file://", "about:")):
            return raw
        path = Path(raw).expanduser()
        if path.is_file():
            return path.resolve().as_uri()
        if re.match(r"^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}([/:?#].*)?$", raw):
            return "https://" + raw
        raise ValueError("网址格式不正确，请输入例如 https://example.com")

    def open_url(self) -> None:
        try:
            url = self._normalize_url(self.url_var.get())
        except ValueError as exc:
            messagebox.showwarning("网址无效", str(exc))
            return
        self.url_var.set(url)
        self._save_option_flags()
        self.save_profile()
        self.settings["last_url"] = url
        self.storage.save_settings()

        def task() -> str:
            if not self.browser.connected:
                self.browser.start(url)
                current = self.browser.current_url()
                if not current or current in ("about:blank", "chrome://newtab/") or (
                    current.rstrip("/") != url.rstrip("/") and url.startswith(("http://", "https://"))
                ):
                    self.browser.navigate(url)
            else:
                self.browser.navigate(url)
            self.engine.refresh_page_meta()
            return self.browser.current_url() or url

        def done(current: str) -> None:
            self.current_url = current
            self.current_title = self.engine.page_title
            self.url_var.set(current)
            self._update_connection_state()
            self._log_ui(f"已打开：{current}")
            self.status_var.set("页面已打开")
            if self.auto_scan_var.get():
                self.scan_page()

        self._run("正在启动浏览器并打开页面…", task, done)

    def reconnect(self, scan_after: bool = False) -> None:
        def task() -> str:
            self.browser.start()
            self.engine.refresh_page_meta()
            return self.browser.current_url()

        def done(url: str) -> None:
            self.current_url = url
            self.current_title = self.engine.page_title
            self._update_connection_state()
            if url:
                self.url_var.set(url)
            self._log_ui(f"浏览器已连接：{url or '当前标签页'}")
            self.status_var.set("浏览器已连接")
            if scan_after and self.auto_scan_var.get():
                self.scan_page()

        self._run("正在连接浏览器…", task, done)

    def reload_page(self) -> None:
        if self.busy:
            return

        def task() -> str:
            if not self.browser.connected:
                self.browser.start(self.url_var.get())
            else:
                self.browser.reload()
            self.engine.refresh_page_meta()
            return self.browser.current_url()

        def done(url: str) -> None:
            self.current_url = url
            self.current_title = self.engine.page_title
            self.url_var.set(url)
            self._update_connection_state()
            self._log_ui("页面已刷新")
            if self.auto_scan_var.get():
                self.scan_page()

        self._run("正在刷新页面…", task, done)

    def choose_tab(self) -> None:
        if not self.browser.connected:
            messagebox.showinfo("尚未连接", "请先打开网页或点击“重新连接”。")
            return
        tabs = self.browser.list_tabs()
        if not tabs:
            messagebox.showinfo("没有标签页", "没有读取到浏览器标签页。")
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("选择浏览器标签页")
        dialog.configure(bg=CARD)
        dialog.geometry(f"{int(720 * self.scale)}x{int(400 * self.scale)}")
        dialog.transient(self.root)
        dialog.grab_set()
        ttk.Label(dialog, text="选择要操作的标签页（扫描和填写只作用于当前标签页）",
                  style="Card.TLabel", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", padx=16, pady=(14, 8))
        listbox = tk.Listbox(dialog, bg="#ffffff", fg=INK, font=("Microsoft YaHei UI", 9),
                             selectbackground="#dbeafe", selectforeground="#173a7a",
                             relief="flat", highlightthickness=1, highlightbackground=LINE)
        listbox.pack(fill="both", expand=True, padx=16)
        for tab in tabs:
            listbox.insert("end", f"{tab.title or '(无标题)'}    —    {tab.url}")
        if tabs:
            listbox.selection_set(0)
        buttons = ttk.Frame(dialog, style="Card.TFrame")
        buttons.pack(fill="x", padx=16, pady=12)
        result: Dict[str, Optional[PageTarget]] = {"target": None}

        def confirm() -> None:
            selection = listbox.curselection()
            if selection:
                result["target"] = tabs[selection[0]]
            dialog.destroy()

        ttk.Button(buttons, text="取消", style="Ghost.TButton",
                   command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="连接此标签页", style="Primary.TButton",
                   command=confirm).pack(side="right", padx=(0, 8))
        listbox.bind("<Double-1>", lambda _e: confirm())
        self.root.wait_window(dialog)
        if not result["target"]:
            return
        target = result["target"]

        def task() -> str:
            self.browser.attach_tab(target)
            self.engine.refresh_page_meta()
            return self.browser.current_url()

        def done(url: str) -> None:
            self.current_url = url
            self.current_title = self.engine.page_title
            self.url_var.set(url)
            self._update_connection_state()
            self._log_ui(f"已切换标签页：{url}")
            if self.auto_scan_var.get():
                self.scan_page()

        self._run("正在切换标签页…", task, done)

    def close_browser(self) -> None:
        if messagebox.askyesno("关闭浏览器", "确定关闭由本程序启动的浏览器吗？\n手动打开的浏览器不会被关闭。"):
            try:
                self.browser.close()
            except Exception:
                pass
            self.engine.refresh_page_meta()
            self._update_connection_state()
            self._log_ui("浏览器已关闭")
            self.status_var.set("浏览器已关闭")

    def clear_site_rules(self) -> None:
        host = self.engine.host or self.host
        if not host:
            messagebox.showinfo("提示", "请先扫描一个页面。")
            return
        if not messagebox.askyesno("清除规则", f"确定清除 {host} 下保存的全部字段映射规则吗？"):
            return
        self.storage.clear_rules(host)
        self._log_ui(f"已清除 {host} 的识别规则")
        self.status_var.set("识别规则已清除")

    # ------------------------------------------------------------------ scan
    def scan_page(self, after_scan: Optional[Callable[[], None]] = None) -> None:
        if self.busy:
            return
        self.save_profile()
        if not self.browser.connected:
            self._log_ui("尚未连接浏览器，正在尝试连接…")
            self.reconnect(scan_after=True)
            return

        def task() -> List[FieldMatch]:
            url = self.browser.current_url()
            parsed = urllib.parse.urlparse(url)
            host = parsed.hostname or ("local-file" if parsed.scheme == "file" else "unknown")
            rules = self.storage.get_rules_for(host)
            return self.engine.scan(self.storage.values, self.storage.field_defs(), rules)

        def done(matches: List[FieldMatch]) -> None:
            self.matches = matches
            self.host = self.engine.host
            self.current_url = self.engine.page_url
            self.current_title = self.engine.page_title
            self._render_matches()
            self._update_connection_state()
            matched = sum(1 for m in matches if m.key)
            if self.engine.page_title:
                self.page_label.configure(text=self.engine.page_title[:74])
            self.scan_info.configure(
                text=f"{len(matches)} 个字段 · 识别 {matched} 个 · {self.engine.scan_time:.1f}s"
            )
            self.status_var.set(f"扫描完成：识别 {matched}/{len(matches)} 个字段")
            if not matches:
                messagebox.showinfo("没有发现字段", "当前页面没有找到可填写的表单字段。\n"
                                                    "如果表单在弹窗或新标签页中，请先打开它再扫描。")
            elif matched == 0:
                self._log_ui("没有识别出字段，请在下方手动选择映射，或为字段添加自定义资料。")
            if after_scan is not None and matches:
                after_scan()

        self._run("正在扫描页面字段…", task, done)

    def _render_matches(self, keep_selection: bool = False) -> None:
        selected = self.tree.selection()
        selected_index = selected[0] if selected else None
        for item in self.tree.get_children():
            self.tree.delete(item)
        for index, match in enumerate(self.matches):
            tags = ["matched" if match.key else "unmatched"]
            if match.status == "已填写":
                tags = ["filled"]
            elif match.status in ("填写失败", "页面已变化", "未执行"):
                tags = ["failed"]
            elif match.ignored:
                tags = ["ignored"]
            label = match.field_label
            if match.is_iframe:
                label = "⧉ " + label
            values = (
                label,
                self.engine.field_label(match.key) if match.key else "—",
                f"{int(match.confidence * 100)}%" if match.key else "—",
                match.status,
                match.page_value[:60],
            )
            self.tree.insert("", "end", iid=str(index), values=values, tags=tuple(tags))
            if match.ambiguous and match.key:
                self.tree.set(str(index), "confidence", f"{int(match.confidence * 100)}% ?")
        if self.matches:
            self.empty_hint.place_forget()
        else:
            self.empty_hint.place(relx=0.5, rely=0.42, anchor="center")
        target = selected_index if keep_selection and selected_index in self.tree.get_children() else None
        if target is None:
            for index, match in enumerate(self.matches):
                if match.key and match.status in ("待填写", "已有内容", "资料为空"):
                    target = str(index)
                    break
            if target is None and self.matches:
                target = "0"
        if target:
            self.tree.selection_set(target)
            self.tree.focus(target)
            self.tree.see(target)

    def _on_tree_select(self, _event: Optional[tk.Event] = None) -> None:
        selection = self.tree.selection()
        if not selection:
            self._set_detail_enabled(False)
            return
        try:
            index = int(selection[0])
        except ValueError:
            return
        if not (0 <= index < len(self.matches)):
            return
        match = self.matches[index]
        frame = f" · {match.frame_path}" if match.is_iframe else ""
        self.detail_label.configure(
            text=f"{match.reason or '手动设置'}   置信度 {int(match.confidence * 100)}%{frame}   "
                 f"规则签名 {match.signature}",
        )
        self._set_detail_enabled(True)
        if match.ignored or not match.key:
            self.mapping_combo.current(0)
        else:
            label = self.engine.field_label(match.key)
            try:
                self.mapping_combo.current(self._mapping_labels.index(label))
            except ValueError:
                self.mapping_combo.current(0)

    def _set_detail_enabled(self, enabled: bool) -> None:
        state = "readonly" if enabled else "disabled"
        self.mapping_combo.configure(state=state)
        self.apply_map_btn.configure(state="normal" if enabled else "disabled")
        self.ignore_btn.configure(state="normal" if enabled else "disabled")

    def _refresh_mapping_choices(self) -> None:
        defs = self.storage.field_defs()
        self._mapping_labels = ["— 不填写 —"] + [fd.label for fd in defs]
        self._mapping_keys = [None] + [fd.key for fd in defs]
        self.mapping_combo.configure(values=self._mapping_labels)
        if self.mapping_combo.current() < 0:
            self.mapping_combo.current(0)

    def apply_mapping(self) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        index = int(selection[0])
        if not (0 <= index < len(self.matches)):
            return
        if not self._mapping_keys:
            self._refresh_mapping_choices()
        choice = self.mapping_combo.current()
        if choice < 0:
            return
        key = self._mapping_keys[choice]
        match = self.matches[index]
        self._set_match_key(match, key)
        if self.remember_var.get() and self.engine.host:
            self.storage.set_rule(self.engine.host, match.signature, key or "__ignore__")
            self._log_ui(f"已记住本站规则：{match.field_label} → {self.engine.field_label(key) if key else '忽略'}")
        self._render_matches(keep_selection=True)
        self.status_var.set("映射已更新")

    def _set_match_key(self, match: FieldMatch, key: Optional[str]) -> None:
        key = key or None
        match.key = key
        match.ignored = key is None
        match.confidence = 1.0 if key else 0.0
        match.source = "manual"
        match.reason = "手动映射" if key else "手动忽略"
        if key:
            from .matcher import value_for
            match.value = value_for(key, self.storage.values, self.storage.field_defs())
            existing = human_key(match.candidate.get("value"))
            if not match.value:
                match.status = "资料为空"
            elif existing:
                match.status = "已填写" if normalize(existing) == normalize(match.value) else "已有内容"
            else:
                match.status = "待填写"
        else:
            match.value = ""
            match.status = "已忽略"

    def ignore_field(self) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        index = int(selection[0])
        match = self.matches[index]
        match.ignored = True
        match.key = None
        match.confidence = 0.0
        match.status = "已忽略"
        match.reason = "手动忽略"
        if self.remember_var.get() and self.engine.host:
            self.storage.set_rule(self.engine.host, match.signature, "__ignore__")
        self._render_matches(keep_selection=True)
        self.status_var.set("已忽略该字段")

    # ------------------------------------------------------------------ fill
    def fill_page(self) -> None:
        if self.busy:
            return
        self.save_profile()
        if not self.browser.connected:
            messagebox.showinfo("尚未连接", "请先打开网页并扫描字段。")
            return
        if not self.matches:
            self.scan_page(after_scan=self._do_fill)
            return
        self._do_fill()

    def _do_fill(self) -> None:
        self.engine.refresh_values(self.matches, self.storage.values)
        fill_empty = bool(self.fill_empty_var.get())

        def task() -> Dict[str, Any]:
            return self.engine.fill(self.matches, fill_empty_only=fill_empty)

        def done(summary: Dict[str, Any]) -> None:
            self._render_matches(keep_selection=True)
            total = summary.get("total", 0)
            filled = summary.get("filled", 0)
            skipped = summary.get("skipped", 0)
            failed = summary.get("failed", 0)
            if total == 0:
                messagebox.showinfo("没有可填写的内容",
                                    "没有可填写的字段。\n请在左侧补充资料，或先扫描页面并确认映射。")
                self.status_var.set("没有可填写的内容")
                return
            self.status_var.set(
                f"填写完成：成功 {filled}，已有内容 {skipped}，失败 {failed}"
            )
            self.scan_info.configure(text=f"本次填写：成功 {filled} · 跳过 {skipped} · 失败 {failed}")
            if failed:
                self._log_ui(f"{failed} 个字段填写失败，可在表格中查看状态；必要时重新扫描。")

        self._run("正在填写页面…", task, done)

    # ------------------------------------------------------------------ settings
    def open_settings(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("设置")
        dialog.configure(bg=CARD)
        dialog.geometry(f"{int(520 * self.scale)}x{int(470 * self.scale)}")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        ttk.Label(dialog, text="浏览器与填写选项", style="Card.TLabel",
                  font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", padx=18, pady=(16, 12))
        form = ttk.Frame(dialog, style="Card.TFrame", padding=(18, 0))
        form.pack(fill="both", expand=True)
        form.columnconfigure(1, weight=1)

        browser_labels = {
            "自动检测（推荐）": "auto",
            "Microsoft Edge": "edge",
            "Google Chrome": "chrome",
            "自定义路径": "custom",
        }
        current_choice = str(self.settings.get("browser") or "auto")
        current_label = next((k for k, v in browser_labels.items() if v == current_choice), "自动检测（推荐）")
        choice_var = tk.StringVar(value=current_label)
        path_var = tk.StringVar(value=str(self.settings.get("browser_path") or ""))
        fill_var = tk.BooleanVar(value=bool(self.settings.get("fill_empty_only", True)))
        auto_var = tk.BooleanVar(value=bool(self.settings.get("auto_scan", True)))
        remember_var = tk.BooleanVar(value=bool(self.settings.get("remember_rules", True)))
        close_var = tk.BooleanVar(value=bool(self.settings.get("close_browser_on_exit", False)))

        ttk.Label(form, text="浏览器", style="FormLabel.TLabel").grid(row=0, column=0, sticky="w", pady=8)
        combo = ttk.Combobox(form, textvariable=choice_var, state="readonly",
                             values=list(browser_labels.keys()), font=("Microsoft YaHei UI", 9))
        combo.grid(row=0, column=1, sticky="ew", pady=8)
        ttk.Label(form, text="程序会使用一个独立的浏览器配置目录，登录状态可以保留。",
                  style="Hint.TLabel", wraplength=int(430 * self.scale)).grid(row=1, column=0, columnspan=2, sticky="w")

        ttk.Label(form, text="程序路径", style="FormLabel.TLabel").grid(row=2, column=0, sticky="w", pady=(12, 0))
        path_row = ttk.Frame(form, style="Card.TFrame")
        path_row.grid(row=2, column=1, sticky="ew", pady=(12, 0))
        path_row.columnconfigure(0, weight=1)
        path_entry = ttk.Entry(path_row, textvariable=path_var, font=("Microsoft YaHei UI", 9))
        path_entry.grid(row=0, column=0, sticky="ew")
        browse_btn = ttk.Button(path_row, text="浏览", style="Ghost.TButton",
                                command=lambda: self._browse_browser_path(path_var))
        browse_btn.grid(row=0, column=1, padx=(6, 0))

        checks = ttk.Frame(dialog, style="Card.TFrame", padding=(18, 12))
        checks.pack(fill="x")
        ttk.Checkbutton(checks, text="只填写页面中原本为空的字段（不覆盖已有内容）",
                        variable=fill_var).pack(anchor="w", pady=3)
        ttk.Checkbutton(checks, text="打开页面后自动扫描字段", variable=auto_var).pack(anchor="w", pady=3)
        ttk.Checkbutton(checks, text="手动调整映射后记住本站规则", variable=remember_var).pack(anchor="w", pady=3)
        ttk.Checkbutton(checks, text="退出程序时关闭由本程序启动的浏览器",
                        variable=close_var).pack(anchor="w", pady=3)

        buttons = ttk.Frame(dialog, style="Card.TFrame", padding=(18, 12))
        buttons.pack(fill="x", side="bottom")
        ttk.Button(buttons, text="取消", style="Ghost.TButton", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="保存设置", style="Primary.TButton",
                   command=lambda: save()).pack(side="right", padx=(0, 8))

        def save() -> None:
            browser = browser_labels.get(choice_var.get(), "auto")
            if browser == "custom" and not Path(path_var.get()).is_file():
                messagebox.showwarning("路径无效", "请选择一个存在的 Chrome/Edge 可执行文件。", parent=dialog)
                return
            self.settings["browser"] = browser
            self.settings["browser_path"] = path_var.get().strip()
            self.settings["fill_empty_only"] = bool(fill_var.get())
            self.settings["auto_scan"] = bool(auto_var.get())
            self.settings["remember_rules"] = bool(remember_var.get())
            self.settings["close_browser_on_exit"] = bool(close_var.get())
            self.storage.save_settings()
            self.fill_empty_var.set(bool(fill_var.get()))
            self.auto_scan_var.set(bool(auto_var.get()))
            self.remember_var.set(bool(remember_var.get()))
            dialog.destroy()
            self._log_ui("设置已保存")
            self.status_var.set("设置已保存")

        def update_path_state(_event: Optional[tk.Event] = None) -> None:
            state = "normal" if browser_labels.get(choice_var.get()) == "custom" else "disabled"
            path_entry.configure(state=state)
            browse_btn.configure(state=state)

        combo.bind("<<ComboboxSelected>>", update_path_state)
        update_path_state()

    def _browse_browser_path(self, var: tk.StringVar) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="选择 Chrome 或 Edge",
            filetypes=[("浏览器程序", "*.exe"), ("所有文件", "*.*")],
        )
        if path:
            var.set(path)

    # ------------------------------------------------------------------ custom fields
    def add_custom_field(self) -> None:
        self._custom_field_dialog(None)

    def edit_custom_field(self) -> None:
        selection = self.custom_tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选择一个自定义字段。")
            return
        self._custom_field_dialog(selection[0])

    def _custom_field_dialog(self, key: Optional[str]) -> None:
        existing = None
        if key:
            existing = next((x for x in self.storage.custom_fields if x["key"] == key), None)
        dialog = tk.Toplevel(self.root)
        dialog.title("编辑自定义字段" if key else "添加自定义字段")
        dialog.configure(bg=CARD)
        dialog.geometry(f"{int(440 * self.scale)}x{int(250 * self.scale)}")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        ttk.Label(dialog, text="字段名会作为识别依据，请与网页上的标签保持一致。",
                  style="Muted.TLabel", wraplength=int(390 * self.scale)).pack(anchor="w", padx=18, pady=(16, 10))
        form = ttk.Frame(dialog, style="Card.TFrame", padding=(18, 0))
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        label_var = tk.StringVar(value=existing["label"] if existing else "")
        value_var = tk.StringVar(value=self.storage.get_value(key) if key else "")
        ttk.Label(form, text="字段名", style="FormLabel.TLabel").grid(row=0, column=0, sticky="w", pady=7)
        ttk.Entry(form, textvariable=label_var).grid(row=0, column=1, sticky="ew", pady=7)
        ttk.Label(form, text="内容", style="FormLabel.TLabel").grid(row=1, column=0, sticky="w", pady=7)
        ttk.Entry(form, textvariable=value_var).grid(row=1, column=1, sticky="ew", pady=7)
        buttons = ttk.Frame(dialog, style="Card.TFrame", padding=(18, 10))
        buttons.pack(fill="x", side="bottom")

        def save() -> None:
            label = label_var.get().strip()
            value = value_var.get().strip()
            if not label:
                messagebox.showwarning("缺少字段名", "请输入字段名。", parent=dialog)
                return
            if key:
                self.storage.update_custom_field(key, label, value)
            else:
                self.storage.add_custom_field(label, value)
            self._refresh_custom_tree()
            dialog.destroy()
            self._log_ui(f"自定义字段已保存：{label}")
            self.status_var.set("自定义字段已保存")

        ttk.Button(buttons, text="取消", style="Ghost.TButton", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="保存", style="Primary.TButton", command=save).pack(side="right", padx=(0, 8))
        self.root.wait_window(dialog)

    def delete_custom_field(self) -> None:
        selection = self.custom_tree.selection()
        if not selection:
            return
        key = selection[0]
        label = next((x["label"] for x in self.storage.custom_fields if x["key"] == key), key)
        if not messagebox.askyesno("删除字段", f"确定删除自定义字段“{label}”及其内容吗？"):
            return
        self.storage.remove_custom_field(key)
        self._refresh_custom_tree()
        self._log_ui(f"已删除自定义字段：{label}")
        self.status_var.set("自定义字段已删除")

    # ------------------------------------------------------------------ import / export
    def import_profile(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="导入资料",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            count = self.storage.import_profile(Path(path))
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc))
            return
        self._load_profile_into_ui()
        self._refresh_mapping_choices()
        self._log_ui(f"已导入 {count} 个字段：{path}")
        self.status_var.set("资料导入成功")

    def export_profile(self) -> None:
        self.save_profile()
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="导出资料",
            defaultextension=".json",
            initialfile="我的资料.json",
            filetypes=[("JSON 文件", "*.json")],
        )
        if not path:
            return
        try:
            self.storage.export_profile(Path(path))
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))
            return
        self._log_ui(f"资料已导出：{path}")
        self.status_var.set("资料已导出")

    def open_data_dir(self) -> None:
        try:
            os.startfile(str(self.storage.home))  # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showerror("无法打开", f"无法打开数据目录：{exc}")

    # ------------------------------------------------------------------ logs / threading
    def _log_ui(self, message: str) -> None:
        self.ui_logs.append(f"[{time.strftime('%H:%M:%S')}] {message}")
        if len(self.ui_logs) > 200:
            del self.ui_logs[:-200]
        self._sync_logs()

    def _sync_logs(self) -> None:
        lines = list(self.ui_logs[-8:])
        if self.engine.logs:
            lines.extend(self.engine.logs[-12:])
        if self.browser.logs:
            lines.extend(self.browser.logs[-8:])
        # Preserve order while removing duplicates and keep the newest lines.
        unique: List[str] = []
        for line in lines:
            if line not in unique:
                unique.append(line)
        text = "\n".join(unique[-24:])
        if not hasattr(self, "log_text"):
            return
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.insert("1.0", text)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_logs(self) -> None:
        self.ui_logs.clear()
        self.engine.logs.clear()
        self._sync_logs()

    def _run(self, label: str, task: Callable[[], Any], on_done: Callable[[Any], None]) -> None:
        if self.busy:
            return
        self.busy = True
        self.status_var.set(label)
        self.root.configure(cursor="watch")
        for button in self._busy_buttons:
            try:
                button.configure(state="disabled")
            except tk.TclError:
                pass
        self._sync_logs()

        def worker() -> None:
            result: Any = None
            error: Optional[BaseException] = None
            try:
                result = task()
            except BaseException as exc:
                error = exc
            self.root.after(0, lambda: self._finish_task(error, result, on_done))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_task(
        self,
        error: Optional[BaseException],
        result: Any,
        on_done: Callable[[Any], None],
    ) -> None:
        self.busy = False
        self.root.configure(cursor="")
        for button in self._busy_buttons:
            try:
                button.configure(state="normal")
            except tk.TclError:
                pass
        if error is not None:
            self._sync_logs()
            self._update_connection_state()
            message = str(error).strip() or error.__class__.__name__
            self.status_var.set("操作失败")
            self._log_ui("操作失败：" + message.replace("\n", " / "))
            messagebox.showerror("操作失败", message)
            return
        try:
            on_done(result)
        except Exception as exc:
            self._log_ui("界面更新失败：" + str(exc))
            messagebox.showerror("界面错误", str(exc))
        finally:
            self._sync_logs()
            self._update_connection_state()

    def _report_callback_exception(self, exc_type: Any, exc: BaseException, tb: Any) -> None:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            path = self.storage.home / "runtime-error.log"
            path.write_text(text, encoding="utf-8")
        except Exception:
            path = None
        try:
            messagebox.showerror(
                "程序错误",
                f"界面操作出现错误：{exc}\n\n"
                + (f"详细信息已写入：{path}" if path else "请重启程序后再试。"),
            )
        except Exception:
            pass

    # ------------------------------------------------------------------ help / close
    def show_help(self) -> None:
        messagebox.showinfo(
            "使用说明",
            "1. 在左侧填写你的常用资料，点击「保存资料」。\n"
            "2. 在网址栏输入目标网页，点击「打开并连接」。\n"
            "3. 点击「扫描页面」，程序会识别页面上的输入框、下拉框、单选框和复选框。\n"
            "4. 检查「识别为」列；如有错误，选中该行，在下方重新选择并点击「应用映射」。\n"
            "5. 点击「智能填写」，完成填写。\n\n"
            "提示：\n"
            "· F5 扫描，F6 填写。\n"
            "· 只填空白字段默认开启，不会覆盖页面上已有内容。\n"
            "· 手动调整映射后勾选「记住本站规则」，下次会自动沿用。\n"
            "· 支持 iframe 和 Shadow DOM；跨域 iframe 需要浏览器允许访问。\n"
            "· 资料保存在本机，不会上传。",
        )

    def show_about(self) -> None:
        messagebox.showinfo(
            "关于",
            f"{APP_TITLE}\n版本 {APP_VERSION}\n\n"
            "一个本地运行的网页表单自动识别与填写工具。\n"
            "通过标签、占位提示、name/id、autocomplete 和控件类型综合识别字段，\n"
            "并使用原生事件写入，兼容 React / Vue 等框架。\n\n"
            "浏览器控制基于 Chrome DevTools Protocol。\n"
            f"数据目录：{self.storage.home}",
        )

    def _on_close(self) -> None:
        try:
            self.storage.set_values(self._collect_profile())
            self.storage.save_settings()
            self.settings["window_geometry"] = self.root.geometry()
            self.storage.save_settings()
        except Exception:
            pass
        try:
            if self.settings.get("close_browser_on_exit"):
                self.browser.close()
            else:
                self.browser.close_ws()
        except Exception:
            pass
        self.root.destroy()
