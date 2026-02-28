import os
import shutil
import uuid
import urllib.parse
from pathlib import Path
from typing import Optional, List, Tuple

import gi
gi.require_version("Gtk", "3.0")
gi.require_version('Vte', '2.91')
from gi.repository import Gtk, GLib, Vte, Pango, Gdk

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.eventbox import EventBox
from fabric.widgets.label import Label
from fabric.widgets.image import Image


class TerminalMixin:
    
    @staticmethod
    def _algo_wrap_index(index: int, length: int, delta: int) -> Optional[int]:
        return (index + delta) % length if length > 0 else None

    @staticmethod
    def _algo_next_after_remove(keys: List[str], removed_index: int) -> Optional[str]:
        if not keys: return None
        return keys[removed_index] if removed_index < len(keys) else keys[-1]

    @staticmethod
    def _algo_scroll_into_view(elem_x: int, elem_w: int, scroll_val: float, page_size: float, upper: float, padding: int = 20) -> Optional[float]:
        if elem_x < scroll_val:
            return max(0.0, float(elem_x - padding))
        if (elem_x + elem_w) > (scroll_val + page_size):
            return min(upper - page_size, float((elem_x + elem_w) - page_size + padding))
        return None

    @staticmethod
    def _algo_scroll_delta(direction: Gdk.ScrollDirection, dx: float = 0.0, dy: float = 0.0, step: int = 30) -> int:
        if direction in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.LEFT): return -step
        if direction in (Gdk.ScrollDirection.DOWN, Gdk.ScrollDirection.RIGHT): return step
        if direction == Gdk.ScrollDirection.SMOOTH: return int((dx if dx != 0 else dy) * step)
        return 0

    @staticmethod
    def _algo_dir_label(uri: str, title: str) -> Optional[str]:
        if uri:
            path_str = urllib.parse.unquote(uri[7:]) if uri.startswith("file://") else uri
            return Path(path_str).name or "Root"
        if title:
            parts = title.split()
            path_part = parts[-1] if parts else "Terminal"
            return Path(path_part).name or path_part
        return None

    @staticmethod
    def _algo_tab_name(counter: int, is_home: bool, force_name: str = None) -> str:
        if is_home: return "Home"
        return force_name or f"Terminal {counter}"

    @staticmethod
    def _algo_resolve_shell() -> str:
        env_shell = os.environ.get("SHELL")
        if env_shell and os.access(env_shell, os.X_OK):
            return env_shell
            
        for shell in ("zsh", "fish", "bash", "sh"):
            path = shutil.which(shell)
            if path:
                return path
                
        return "/bin/bash"

    @staticmethod
    def _algo_clamp_scroll(value: float, lower: float, upper: float, page: float) -> float:
        return max(lower, min(value, upper - page))

    def _build_terminal_view(self):
        self.terminals = {}
        self.active_terminal_id = None
        self.home_term_id = None
        self._tab_counter = 0

        self.terminals_stack = Gtk.Stack()
        self.terminals_stack.set_homogeneous(False)
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

        scroll_eb = Gtk.EventBox()
        scroll_eb.add(self.tabs_scroll)
        scroll_eb.add_events(Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        scroll_eb.connect("scroll-event", self._h_tabs_scroll)

        add_btn = Button(
            child=Image(icon_name="list-add-symbolic", icon_size=16),
            on_clicked=lambda _: self._add_terminal_tab()
        )
        add_btn.set_can_focus(False)
        add_btn.get_style_context().add_class("terminal-add-btn")

        self.terminal_tab_bar = Box(
            name="explorer-terminal-tab-bar",
            orientation="h", spacing=8,
            children=[scroll_eb, add_btn]
        )
        return self.terminal_tab_bar

    def _vte_create(self) -> Vte.Terminal:
        term = Vte.Terminal()
        term.set_name("explorer-terminal")
        term.set_size_request(10, 10) 
        term.set_mouse_autohide(True)
        term.set_scrollback_lines(10000)
        term.set_hexpand(True)
        term.set_vexpand(True)
        term.set_can_focus(True)

        term.set_font(Pango.FontDescription.from_string("Monospace 11"))

        bg = Gdk.RGBA()
        bg.parse("#000000")
        bg.alpha = 1.0  
        
        fg = Gdk.RGBA()
        fg.parse("#cdd6f4")

        palette = []
        for hx in (
            "#45475a", "#f38ba8", "#a6e3a1", "#f9e2af",
            "#89b4fa", "#f5c2e7", "#94e2d5", "#bac2de",
            "#585b70", "#f38ba8", "#a6e3a1", "#f9e2af",
            "#89b4fa", "#f5c2e7", "#94e2d5", "#a6adc8",
        ):
            c = Gdk.RGBA()
            c.parse(hx)
            palette.append(c)

        term.set_colors(fg, bg, palette)
        return term

    def _spawn_shell(self, term: Vte.Terminal, working_dir: str):
        shell = self._algo_resolve_shell()
        try:
            term.spawn_async(
                Vte.PtyFlags.DEFAULT, working_dir, [shell],
                GLib.get_environ(), GLib.SpawnFlags.DEFAULT,
                None, None, -1, None, None,
            )
        except Exception as e:
            print(f"Error spawning terminal: {e}")

    def _add_terminal_tab(self, cwd: str = None, force_name: str = None):
        term_id = str(uuid.uuid4())
        is_home = len(self.terminals) == 0

        if not is_home:
            self._tab_counter += 1

        tab_name = self._algo_tab_name(self._tab_counter, is_home, force_name)

        if is_home:
            self.home_term_id = term_id

        term = self._vte_create()
        term.connect("child-exited", lambda t, s: self._h_child_exited(term_id))
        term.connect("button-press-event", self._h_terminal_click)
        term.connect("current-directory-uri-changed", self._h_dir_changed, term_id)
        term.connect("window-title-changed", self._h_title_changed, term_id)

        scroll = Gtk.ScrolledWindow()
        scroll.set_name("explorer-terminal-scroll")
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(term)

        term_box = Box(
            name="explorer-terminal-box",
            orientation="v", h_expand=True, v_expand=True,
            children=[scroll]
        )
        term_box.show_all()

        lbl, entry, name_stack = self._tab_build_name_widget(tab_name, term_id)

        tab_content = Box(orientation="h", spacing=6)
        tab_content.pack_start(name_stack, True, True, 0)

        if not is_home:
            close_btn = Button(child=Image(icon_name="window-close-symbolic", icon_size=16))
            close_btn.set_can_focus(False)
            close_btn.get_style_context().add_class("terminal-close-btn")
            close_btn.connect("clicked", lambda _: self._close_terminal_tab(term_id))
            tab_content.pack_start(close_btn, False, False, 0)

        tab_eb = EventBox()
        tab_eb.set_can_focus(False)
        tab_eb.add(tab_content)
        tab_eb.get_style_context().add_class("terminal-tab-btn")
        tab_eb.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        tab_eb.connect("button-press-event", self._h_tab_click, term_id)

        self.terminals[term_id] = {
            'vte': term, 'box': term_box, 'tab': tab_eb, 
            'name_stack': name_stack, 'lbl': lbl, 'entry': entry,
        }

        self.terminals_stack.add_named(term_box, term_id)
        self.tabs_box.pack_start(tab_eb, False, False, 0)
        self.tabs_box.show_all()

        work_dir = str(Path.home()) if is_home else (cwd or str(self._current_path))
        self._spawn_shell(term, work_dir)
        
        self._switch_to_terminal_tab(term_id)
        GLib.idle_add(self._scroll_tabs_to_end)

    def _close_terminal_tab(self, term_id: str):
        if term_id not in self.terminals or term_id == self.home_term_id:
            return

        keys = list(self.terminals.keys())
        idx = keys.index(term_id)

        tinfo = self.terminals.pop(term_id)
        tinfo['vte'].destroy()
        tinfo['box'].destroy()
        tinfo['tab'].destroy()

        if not self.terminals:
            self.active_terminal_id = None
            self._switch_to_files()
            return

        next_id = self._algo_next_after_remove(list(self.terminals.keys()), idx)
        if next_id:
            self._switch_to_terminal_tab(next_id)

    def _switch_to_terminal_tab(self, term_id: str):
        if term_id not in self.terminals:
            return

        self.active_terminal_id = term_id
        self.terminals_stack.set_visible_child_name(term_id)

        for tid, tinfo in self.terminals.items():
            ctx = tinfo['tab'].get_style_context()
            if tid == term_id:
                ctx.add_class("active")
            else:
                ctx.remove_class("active")

        self._update_terminal_placeholder()
        self._scroll_to_active_tab(term_id)
        self._grab_terminal_focus(term_id)

    def _tab_build_name_widget(self, tab_name: str, term_id: str) -> Tuple[Label, Gtk.Entry, Gtk.Stack]:
        lbl = Label(label=tab_name)
        lbl.set_name("explorer-tab-label")

        entry = Gtk.Entry(text=tab_name)
        entry.get_style_context().add_class("terminal-tab-entry")
        entry.set_width_chars(max(8, len(tab_name) + 1))
        entry.set_alignment(0.5)

        entry.connect("changed", lambda e: e.set_width_chars(max(8, len(e.get_text()) + 1)))
        entry.connect("activate", lambda e: self._finish_tab_rename(e, term_id))
        entry.connect("focus-out-event", lambda e, ev: self._finish_tab_rename(e, term_id))
        entry.connect("key-press-event", self._h_tab_entry_key, term_id)

        name_stack = Gtk.Stack()
        name_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        name_stack.set_transition_duration(100)
        name_stack.add_named(lbl, "label")
        name_stack.add_named(entry, "entry")

        return lbl, entry, name_stack

    def _terminal_prev_tab(self):
        if not self.terminals: return
        keys = list(self.terminals.keys())
        idx = keys.index(self.active_terminal_id)
        new = self._algo_wrap_index(idx, len(keys), -1)
        if new is not None: self._switch_to_terminal_tab(keys[new])

    def _terminal_next_tab(self):
        if not self.terminals: return
        keys = list(self.terminals.keys())
        idx = keys.index(self.active_terminal_id)
        new = self._algo_wrap_index(idx, len(keys), +1)
        if new is not None: self._switch_to_terminal_tab(keys[new])

    def _terminal_home_tab(self):
        if self.home_term_id and self.home_term_id in self.terminals:
            self._switch_to_terminal_tab(self.home_term_id)

    def _start_tab_rename(self, term_id: str):
        if term_id == self.home_term_id: return
        tinfo = self.terminals.get(term_id)
        if not tinfo: return

        if self.active_terminal_id != term_id:
            self._switch_to_terminal_tab(term_id)

        lbl, entry, name_stack = tinfo['lbl'], tinfo['entry'], tinfo['name_stack']
        
        current_name = lbl.get_label()
        entry.set_text(current_name)
        entry.set_width_chars(max(8, len(current_name) + 1))
        name_stack.set_visible_child_name("entry")
        
        self._set_keyboard_interactive(True)

        def force_entry_focus():
            try:
                self.present()
                self.set_focus(entry)
                entry.grab_focus()
                entry.select_region(0, -1)
            except Exception:
                pass
            return False

        GLib.idle_add(force_entry_focus)

    def _finish_tab_rename(self, entry: Gtk.Entry, term_id: str, cancel: bool = False) -> bool:
        tinfo = self.terminals.get(term_id)
        if not tinfo: return False

        name_stack = tinfo['name_stack']
        if name_stack.get_visible_child_name() == "label": return False

        if not cancel:
            new_name = entry.get_text().strip()
            if new_name:
                tinfo['lbl'].set_label(new_name)

        name_stack.set_visible_child_name("label")
        self._grab_terminal_focus(term_id)
        return False
    
    def _do_terminal_search(self, text: str):
        if not self.active_terminal_id or self.active_terminal_id not in self.terminals: return
            
        term = self.terminals[self.active_terminal_id]['vte']
        if not text:
            term.search_set_regex(None, 0)
            return
            
        try:
            regex = Vte.Regex.new_for_search(text, len(text), 0x00000008)
            term.search_set_regex(regex, 0)
            term.search_find_previous()
        except Exception as e:
            print(f"VTE Search Regex error: {e}")

    def _terminal_search_next(self):
        if self.active_terminal_id and self.active_terminal_id in self.terminals:
            self.terminals[self.active_terminal_id]['vte'].search_find_previous()

    def _grab_terminal_focus(self, term_id: str = None):
        tid = term_id or self.active_terminal_id
        if not tid or tid not in self.terminals: return

        tinfo = self.terminals[tid]
        term = tinfo['vte']

        def force_focus():
            if self.active_terminal_id != tid:
                return False
                
            ns = tinfo.get('name_stack')
            if ns and ns.get_visible_child_name() == "entry": 
                return False
                
            try:
                self._set_keyboard_interactive(True)
                self.present()
                self.set_focus(term)
                term.grab_focus()
            except Exception: pass
            return False

        GLib.idle_add(force_focus)
        GLib.timeout_add(100, force_focus)

    def _update_terminal_placeholder(self):
        if not self.active_terminal_id or self.active_terminal_id not in self.terminals: return
            
        term = self.terminals[self.active_terminal_id]['vte']
        label = self._algo_dir_label(term.get_current_directory_uri(), term.get_window_title())
        if label: self.folder_label.set_label(label)

    def _scroll_to_active_tab(self, term_id: str):
        if term_id not in self.terminals: return
        tab_widget = self.terminals[term_id]['tab']

        def do_scroll():
            adj = self.tabs_scroll.get_hadjustment()
            if not adj: return False
                
            alloc = tab_widget.get_allocation()
            new_val = self._algo_scroll_into_view(alloc.x, alloc.width, adj.get_value(), adj.get_page_size(), adj.get_upper())
            if new_val is not None: adj.set_value(new_val)
            return False

        GLib.idle_add(do_scroll)

    def _scroll_tabs_to_end(self):
        try:
            adj = self.tabs_scroll.get_hadjustment()
            if adj: adj.set_value(adj.get_upper() - adj.get_page_size())
        except Exception: pass
        return False

    def _is_terminal_open(self) -> bool:
        return hasattr(self, 'stack') and self.stack.get_visible_child_name() == "terminal"

    def _open_terminal(self, cwd: str = None):
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
        
        if hasattr(self, '_cancel_pending_hide'): self._cancel_pending_hide()

        if not self.terminals:
            self._add_terminal_tab()

        if cwd is not None:
            folder_name = Path(cwd).name or "Terminal"
            self._add_terminal_tab(cwd=cwd, force_name=folder_name)
        elif self.active_terminal_id:
            self._update_terminal_placeholder()
            self._scroll_to_active_tab(self.active_terminal_id)
            self._grab_terminal_focus(self.active_terminal_id)

    def _switch_to_files(self):
        if not self._is_terminal_open(): return

        self.stack.set_visible_child_name("files")
        self.bottom_bar_stack.set_visible_child_name("files")
        self.path_bar.show()
        self.btn_hidden.show()
        self.btn_up.show()

        idx = getattr(self, '_history_index', 0)
        hist_len = len(getattr(self, '_history', []))
        self.btn_back.set_sensitive(idx > 0)
        self.btn_forward.set_sensitive(idx < hist_len - 1)
        self.btn_home.set_sensitive(True)

        folder_name = "Trash" if getattr(self, '_is_in_trash', lambda: False)() else (self._current_path.name or "Root")
        self.folder_label.set_label(folder_name)
        self.search_entry.set_text("")

        if self.active_terminal_id and self.active_terminal_id in self.terminals:
            self.terminals[self.active_terminal_id]['vte'].search_set_regex(None, 0)

        self.btn_terminal.get_style_context().remove_class("active")
        self._set_keyboard_interactive(False)
        self.set_focus(None)
        
        if hasattr(self, '_load_directory'):
            self._load_directory()

    def _h_tabs_scroll(self, widget, event) -> bool:
        adj = self.tabs_scroll.get_hadjustment()
        if not adj: return False

        dx, dy = 0.0, 0.0
        if event.direction == Gdk.ScrollDirection.SMOOTH: _, dx, dy = event.get_scroll_deltas()

        delta = self._algo_scroll_delta(event.direction, dx, dy)
        if delta:
            adj.set_value(self._algo_clamp_scroll(adj.get_value() + delta, adj.get_lower(), adj.get_upper(), adj.get_page_size()))
            return True
        return False

    def _h_tab_click(self, widget, event, term_id: str) -> bool:
        if event.button != 1: return False
        tinfo = self.terminals.get(term_id)
        if tinfo and tinfo['name_stack'].get_visible_child_name() == "entry": return False

        if event.type == Gdk.EventType.BUTTON_PRESS: self._switch_to_terminal_tab(term_id)
        elif event.type == Gdk.EventType.DOUBLE_BUTTON_PRESS:
            if term_id != self.home_term_id: self._start_tab_rename(term_id)
        return False

    def _h_tab_entry_key(self, entry: Gtk.Entry, event, term_id: str) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self._finish_tab_rename(entry, term_id, cancel=True)
            return True
        return False

    def _h_child_exited(self, term_id: str):
        if term_id == self.home_term_id:
            term = self.terminals[term_id]['vte']
            term.reset(True, True)
            self._spawn_shell(term, str(Path.home()))
            self._switch_to_files()
        else:
            self._close_terminal_tab(term_id)

    def _h_terminal_click(self, widget, event) -> bool:
        if event.type == Gdk.EventType.BUTTON_PRESS and event.button == 1:
            if hasattr(self, '_cancel_pending_hide'): self._cancel_pending_hide()
            self._grab_terminal_focus()
        return False

    def _h_dir_changed(self, term: Vte.Terminal, term_id: str):
        if self.active_terminal_id == term_id: self._update_terminal_placeholder()

    def _h_title_changed(self, term: Vte.Terminal, term_id: str):
        if self.active_terminal_id == term_id: self._update_terminal_placeholder()

    def _on_terminal_clicked(self, btn):
        if getattr(self, '_pending_drop_source', None): return
        if self._is_terminal_open(): self._switch_to_files()
        else: self._open_terminal()