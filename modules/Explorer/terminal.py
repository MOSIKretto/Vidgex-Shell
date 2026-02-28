import os
import shutil
import uuid
import urllib.parse
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Gtk, GLib, Vte, Pango, Gdk

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.eventbox import EventBox
from fabric.widgets.label import Label
from fabric.widgets.image import Image


class TerminalMixin:
    _PALETTE_COLORS = (
        "#45475a", "#f38ba8", "#a6e3a1", "#f9e2af",
        "#89b4fa", "#f5c2e7", "#94e2d5", "#bac2de",
        "#585b70", "#f38ba8", "#a6e3a1", "#f9e2af",
        "#89b4fa", "#f5c2e7", "#94e2d5", "#a6adc8",
    )
    _BG_COLOR = "#000000"
    _FG_COLOR = "#cdd6f4"
    _FONT = "Monospace 11"
    _SCROLLBACK_LINES = 10000
    _TAB_SCROLL_STEP = 30
    _TAB_SCROLL_PADDING = 20

    def _build_terminal_view(self):
        self.terminals = {}
        self.active_terminal_id = None
        self.home_term_id = None
        self._tab_counter = 0

        self.terminals_stack = Gtk.Stack()
        self.terminals_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.terminals_stack.set_transition_duration(200)
        self.terminals_stack.set_hexpand(True)
        self.terminals_stack.set_vexpand(True)
        return self.terminals_stack

    def _build_terminal_tab_bar(self):
        self.tabs_box = Box(orientation="h", spacing=4)

        self.tabs_scroll = Gtk.ScrolledWindow()
        self.tabs_scroll.set_name("explorer-terminal-tabs-scroll")
        self.tabs_scroll.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
        self.tabs_scroll.set_hexpand(True)
        self.tabs_scroll.set_propagate_natural_width(False)

        viewport = Gtk.Viewport()
        viewport.set_shadow_type(Gtk.ShadowType.NONE)
        viewport.add(self.tabs_box)
        self.tabs_scroll.add(viewport)

        scroll_event_box = Gtk.EventBox()
        scroll_event_box.add(self.tabs_scroll)
        scroll_event_box.add_events(
            Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK,
        )
        scroll_event_box.connect("scroll-event", self._on_tabs_scroll)

        add_btn = Button(
            child=Image(icon_name="list-add-symbolic", icon_size=16),
            on_clicked=lambda _: self._add_terminal_tab(),
        )
        add_btn.set_can_focus(False)
        add_btn.get_style_context().add_class("terminal-add-btn")

        self.terminal_tab_bar = Box(
            name="explorer-terminal-tab-bar",
            orientation="h",
            spacing=8,
            children=[scroll_event_box, add_btn],
        )
        return self.terminal_tab_bar

    def _create_terminal_widget(self):
        term = Vte.Terminal()
        term.set_name("explorer-terminal")
        term.set_mouse_autohide(True)
        term.set_scrollback_lines(self._SCROLLBACK_LINES)
        term.set_hexpand(True)
        term.set_vexpand(True)
        term.set_can_focus(True)
        term.set_font(Pango.FontDescription.from_string(self._FONT))

        bg = Gdk.RGBA()
        bg.parse(self._BG_COLOR)
        fg = Gdk.RGBA()
        fg.parse(self._FG_COLOR)

        palette = []
        for hex_color in self._PALETTE_COLORS:
            color = Gdk.RGBA()
            color.parse(hex_color)
            palette.append(color)

        term.set_colors(fg, bg, palette)
        return term

    @staticmethod
    def _resolve_shell():
        return shutil.which("fish") or os.environ.get("SHELL", "/bin/bash")

    def _spawn_shell(self, term, working_dir):
        shell = self._resolve_shell()
        try:
            term.spawn_async(
                Vte.PtyFlags.DEFAULT,
                working_dir,
                [shell],
                GLib.get_environ(),
                GLib.SpawnFlags.DEFAULT,
                None, None, -1, None, None,
            )
        except Exception as exc:
            print(f"Error spawning terminal: {exc}")

    def _add_terminal_tab(self, cwd=None, force_name=None):
        term_id = str(uuid.uuid4())
        is_home = not self.terminals

        if not is_home:
            self._tab_counter += 1

        tab_name = "Home" if is_home else (force_name or f"Terminal {self._tab_counter}")

        if is_home:
            self.home_term_id = term_id

        term = self._create_terminal_widget()
        term.connect("child-exited", lambda _t, _s, tid=term_id: self._on_child_exited(tid))
        term.connect("button-press-event", self._on_terminal_click)
        term.connect("current-directory-uri-changed", self._on_dir_changed, term_id)
        term.connect("window-title-changed", self._on_title_changed, term_id)

        scroll = Gtk.ScrolledWindow()
        scroll.set_name("explorer-terminal-scroll")
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(term)

        term_box = Box(
            name="explorer-terminal-box",
            orientation="v",
            h_expand=True,
            v_expand=True,
            children=[scroll],
        )
        term_box.show_all()

        label, entry, name_stack = self._create_tab_name_widget(tab_name, term_id)

        tab_content = Box(orientation="h", spacing=6)
        tab_content.pack_start(name_stack, True, True, 0)

        if not is_home:
            close_btn = Button(
                child=Image(icon_name="window-close-symbolic", icon_size=16),
            )
            close_btn.set_can_focus(False)
            close_btn.get_style_context().add_class("terminal-close-btn")
            close_btn.connect("clicked", lambda _b, tid=term_id: self._close_terminal_tab(tid))
            tab_content.pack_start(close_btn, False, False, 0)

        tab_event_box = EventBox()
        tab_event_box.set_can_focus(False)
        tab_event_box.add(tab_content)
        tab_event_box.get_style_context().add_class("terminal-tab-btn")
        tab_event_box.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        tab_event_box.connect("button-press-event", self._on_tab_click, term_id)

        self.terminals[term_id] = {
            "vte": term,
            "box": term_box,
            "tab": tab_event_box,
            "name_stack": name_stack,
            "label": label,
            "entry": entry,
        }

        self.terminals_stack.add_named(term_box, term_id)
        self.tabs_box.pack_start(tab_event_box, False, False, 0)
        self.tabs_box.show_all()

        work_dir = str(Path.home()) if is_home else (str(cwd) if cwd else str(self._current_path))
        self._spawn_shell(term, work_dir)
        self._switch_to_terminal_tab(term_id)
        GLib.idle_add(self._scroll_tabs_to_end)

    def _close_terminal_tab(self, term_id):
        if term_id not in self.terminals or term_id == self.home_term_id:
            return

        keys = list(self.terminals.keys())
        idx = keys.index(term_id)

        info = self.terminals.pop(term_id)
        info["vte"].destroy()
        info["box"].destroy()
        info["tab"].destroy()

        if not self.terminals:
            self.active_terminal_id = None
            self._switch_to_files()
            return

        remaining = list(self.terminals.keys())
        next_id = remaining[min(idx, len(remaining) - 1)]
        self._switch_to_terminal_tab(next_id)

    def _switch_to_terminal_tab(self, term_id):
        if term_id not in self.terminals:
            return

        self.active_terminal_id = term_id
        self.terminals_stack.set_visible_child_name(term_id)

        for tid, info in self.terminals.items():
            ctx = info["tab"].get_style_context()
            if tid == term_id:
                ctx.add_class("active")
            else:
                ctx.remove_class("active")

        self._update_terminal_placeholder()
        self._scroll_to_active_tab(term_id)
        self._grab_terminal_focus(term_id)

    def _create_tab_name_widget(self, tab_name, term_id):
        label = Label(label=tab_name)
        label.set_name("explorer-tab-label")

        entry = Gtk.Entry(text=tab_name)
        entry.get_style_context().add_class("terminal-tab-entry")
        entry.set_width_chars(max(8, len(tab_name) + 1))
        entry.set_alignment(0.5)

        entry.connect(
            "changed",
            lambda e: e.set_width_chars(max(8, len(e.get_text()) + 1)),
        )
        entry.connect(
            "activate",
            lambda e, tid=term_id: self._finish_tab_rename(e, tid),
        )
        entry.connect(
            "focus-out-event",
            lambda e, _ev, tid=term_id: self._finish_tab_rename(e, tid),
        )
        entry.connect("key-press-event", self._on_tab_entry_key, term_id)

        name_stack = Gtk.Stack()
        name_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        name_stack.set_transition_duration(100)
        name_stack.add_named(label, "label")
        name_stack.add_named(entry, "entry")

        return label, entry, name_stack

    def _terminal_prev_tab(self):
        self._switch_terminal_tab_by_offset(-1)

    def _terminal_next_tab(self):
        self._switch_terminal_tab_by_offset(1)

    def _switch_terminal_tab_by_offset(self, offset):
        if not self.terminals:
            return
        keys = list(self.terminals.keys())
        idx = keys.index(self.active_terminal_id)
        self._switch_to_terminal_tab(keys[(idx + offset) % len(keys)])

    def _terminal_home_tab(self):
        if self.home_term_id and self.home_term_id in self.terminals:
            self._switch_to_terminal_tab(self.home_term_id)

    def _start_tab_rename(self, term_id):
        if term_id == self.home_term_id:
            return
        info = self.terminals.get(term_id)
        if not info:
            return

        if self.active_terminal_id != term_id:
            self._switch_to_terminal_tab(term_id)

        current_name = info["label"].get_label()
        entry = info["entry"]
        entry.set_text(current_name)
        entry.set_width_chars(max(8, len(current_name) + 1))
        info["name_stack"].set_visible_child_name("entry")
        self._set_keyboard_interactive(True)

        def focus_entry():
            try:
                self.present()
                self.set_focus(entry)
                entry.grab_focus()
                entry.select_region(0, -1)
            except Exception:
                pass
            return False

        GLib.idle_add(focus_entry)
        GLib.timeout_add(100, focus_entry)

    def _finish_tab_rename(self, entry, term_id, cancel=False):
        info = self.terminals.get(term_id)
        if not info:
            return False

        name_stack = info["name_stack"]
        if name_stack.get_visible_child_name() == "label":
            return False

        if not cancel:
            new_name = entry.get_text().strip()
            if new_name:
                info["label"].set_label(new_name)

        name_stack.set_visible_child_name("label")
        self._grab_terminal_focus(term_id)
        return False

    def _do_terminal_search(self, text):
        if not self.active_terminal_id or self.active_terminal_id not in self.terminals:
            return
        term = self.terminals[self.active_terminal_id]["vte"]
        if not text:
            term.search_set_regex(None, 0)
            return
        try:
            regex = Vte.Regex.new_for_search(text, len(text), 0x00000008)
            term.search_set_regex(regex, 0)
            term.search_find_previous()
        except Exception as exc:
            print(f"VTE search regex error: {exc}")

    def _terminal_search_next(self):
        if not self.active_terminal_id or self.active_terminal_id not in self.terminals:
            return
        self.terminals[self.active_terminal_id]["vte"].search_find_previous()

    def _grab_terminal_focus(self, term_id=None):
        tid = term_id or self.active_terminal_id
        if not tid or tid not in self.terminals:
            return

        self._set_keyboard_interactive(True)
        info = self.terminals[tid]
        term = info["vte"]

        def do_focus():
            try:
                name_stack = info.get("name_stack")
                if name_stack and name_stack.get_visible_child_name() == "entry":
                    return False
                self.present()
                self.set_focus(term)
                term.grab_focus()
            except Exception:
                pass
            return False

        GLib.idle_add(do_focus)
        GLib.timeout_add(100, do_focus)

    def _update_terminal_placeholder(self):
        if not self.active_terminal_id or self.active_terminal_id not in self.terminals:
            return
        term = self.terminals[self.active_terminal_id]["vte"]
        label = self._directory_display_name(
            term.get_current_directory_uri(),
            term.get_window_title(),
        )
        if label:
            self.folder_label.set_label(label)

    @staticmethod
    def _directory_display_name(uri, title):
        if uri:
            path_str = urllib.parse.unquote(uri[7:]) if uri.startswith("file://") else uri
            return os.path.basename(path_str.rstrip("/")) or "Root"
        if title:
            parts = title.split()
            path_part = parts[-1] if parts else "Terminal"
            return os.path.basename(path_part.rstrip("/")) or path_part
        return None

    def _scroll_to_active_tab(self, term_id):
        if term_id not in self.terminals:
            return
        tab_widget = self.terminals[term_id]["tab"]

        def do_scroll():
            adj = self.tabs_scroll.get_hadjustment()
            if not adj:
                return False
            alloc = tab_widget.get_allocation()
            padding = self._TAB_SCROLL_PADDING
            scroll_val = adj.get_value()
            page_size = adj.get_page_size()
            upper = adj.get_upper()
            elem_end = alloc.x + alloc.width

            if alloc.x < scroll_val:
                adj.set_value(max(0, alloc.x - padding))
            elif elem_end > scroll_val + page_size:
                adj.set_value(min(upper - page_size, elem_end - page_size + padding))
            return False

        GLib.timeout_add(50, do_scroll)

    def _scroll_tabs_to_end(self):
        adj = self.tabs_scroll.get_hadjustment()
        if adj:
            adj.set_value(adj.get_upper() - adj.get_page_size())
        return False

    def _is_terminal_open(self):
        return hasattr(self, "stack") and self.stack.get_visible_child_name() == "terminal"

    def _open_terminal(self, cwd=None):
        self.stack.set_visible_child_name("terminal")
        self.bottom_bar_stack.set_visible_child_name("terminal")
        self.path_bar.hide()
        self.btn_hidden.hide()
        self.btn_up.hide()

        self.btn_back.set_sensitive(True)
        self.btn_forward.set_sensitive(True)
        self.btn_home.set_sensitive(True)

        self.search_entry.set_text("")
        self.btn_terminal.get_style_context().add_class("active")
        self._cancel_pending_hide()

        if not self.terminals:
            self._add_terminal_tab()

        if cwd is not None:
            folder_name = os.path.basename(str(cwd).rstrip("/")) or "Terminal"
            self._add_terminal_tab(cwd=cwd, force_name=folder_name)
        elif self.active_terminal_id:
            self._update_terminal_placeholder()
            self._scroll_to_active_tab(self.active_terminal_id)
            self._grab_terminal_focus(self.active_terminal_id)

    def _switch_to_files(self):
        if not hasattr(self, "stack") or self.stack.get_visible_child_name() != "terminal":
            return

        self.stack.set_visible_child_name("files")
        self.bottom_bar_stack.set_visible_child_name("files")
        self.path_bar.show()
        self.btn_hidden.show()
        self.btn_up.show()

        idx = getattr(self, "_history_index", 0)
        hist_len = len(getattr(self, "_history", []))
        self.btn_back.set_sensitive(idx > 0)
        self.btn_forward.set_sensitive(idx < hist_len - 1)
        self.btn_home.set_sensitive(True)

        folder_name = "Trash" if self._is_in_trash() else (self._current_path.name or "Root")
        self.folder_label.set_label(folder_name)
        self.search_entry.set_text("")

        if self.active_terminal_id and self.active_terminal_id in self.terminals:
            self.terminals[self.active_terminal_id]["vte"].search_set_regex(None, 0)

        self.btn_terminal.get_style_context().remove_class("active")
        self._set_keyboard_interactive(False)
        self.set_focus(None)
        self._load_directory()

    def _on_tabs_scroll(self, _widget, event):
        adj = self.tabs_scroll.get_hadjustment()
        if not adj:
            return False

        step = self._TAB_SCROLL_STEP
        direction = event.direction

        if direction in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.LEFT):
            delta = -step
        elif direction in (Gdk.ScrollDirection.DOWN, Gdk.ScrollDirection.RIGHT):
            delta = step
        elif direction == Gdk.ScrollDirection.SMOOTH:
            _, dx, dy = event.get_scroll_deltas()
            delta = int((dx or dy) * step)
        else:
            return False

        if delta:
            new_val = adj.get_value() + delta
            adj.set_value(max(adj.get_lower(), min(new_val, adj.get_upper() - adj.get_page_size())))
            return True
        return False

    def _on_tab_click(self, _widget, event, term_id):
        if event.button != 1:
            return False

        info = self.terminals.get(term_id)
        if info and info["name_stack"].get_visible_child_name() == "entry":
            return False

        if event.type == Gdk.EventType.BUTTON_PRESS:
            self._switch_to_terminal_tab(term_id)
        elif event.type == Gdk.EventType.DOUBLE_BUTTON_PRESS and term_id != self.home_term_id:
            self._start_tab_rename(term_id)
        return False

    def _on_tab_entry_key(self, entry, event, term_id):
        if event.keyval == Gdk.KEY_Escape:
            self._finish_tab_rename(entry, term_id, cancel=True)
            return True
        return False

    def _on_child_exited(self, term_id):
        if term_id == self.home_term_id:
            term = self.terminals[term_id]["vte"]
            term.reset(True, True)
            self._spawn_shell(term, str(Path.home()))
            self._switch_to_files()
        else:
            self._close_terminal_tab(term_id)

    def _on_terminal_click(self, _widget, event):
        if event.type == Gdk.EventType.BUTTON_PRESS and event.button == 1:
            self._cancel_pending_hide()
            self._grab_terminal_focus()
        return False

    def _on_dir_changed(self, _term, term_id):
        if self.active_terminal_id == term_id:
            self._update_terminal_placeholder()

    def _on_title_changed(self, _term, term_id):
        if self.active_terminal_id == term_id:
            self._update_terminal_placeholder()

    def _on_terminal_clicked(self, _btn):
        if self._pending_drop_source:
            return
        if self._is_terminal_open():
            self._switch_to_files()
        else:
            self._open_terminal()