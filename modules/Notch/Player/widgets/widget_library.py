import os
import hashlib
import random
import re
import threading
from collections import namedtuple

from gi.repository import Gdk, Gio, GLib, Gtk
from mutagen import File as MutagenFile

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow

import services.icons as icons
from .widget_player import _fex, _hover


_TRACK_NUM_RE = re.compile(r"^(\d+[\s.\-_]+)+")

_AUDIO_EXTS = frozenset({
    ".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac",
    ".wma", ".opus", ".ape", ".alac",
})

_MUSIC_DIR = (
    GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_MUSIC)
    or os.path.join(GLib.get_home_dir(), "Music")
)

_COVER_DIR = os.path.join(GLib.get_user_cache_dir(), "vidgex-shell", "covers")

_TrackRow = namedtuple(
    "_TrackRow",
    ("btn", "search_str", "path", "artist", "title", "album", "length_us", "art_url"),
)

_UNKNOWN_ARTISTS = frozenset({"unknown", "unknown artist"})

_TAG_ARTIST = ("TPE1", "artist", "©ART", "Author")
_TAG_TITLE = ("TIT2", "title", "©nam", "Title")
_TAG_ALBUM = ("TALB", "album", "©alb", "WM/AlbumTitle")

def _get_tag_text(tags, keys):
    for key in keys:
        val = tags.get(key)
        if val is None:
            continue
        if isinstance(val, list):
            return str(val[0]) if val else ""
        return str(val)
    return ""


def _extract_cover(audio, filepath):
    if not audio:
        return ""

    md5 = hashlib.md5(filepath.encode()).hexdigest()
    cpath = os.path.join(_COVER_DIR, f"loc_{md5}.png")
    if _fex(cpath):
        return GLib.filename_to_uri(cpath, None)

    art_data = None
    try:
        tags = getattr(audio, "tags", None)
        if tags:
            for key, tag in tags.items():
                if key.startswith("APIC"):
                    art_data = tag.data
                    break

        if not art_data:
            pictures = getattr(audio, "pictures", None)
            if pictures:
                art_data = pictures[0].data

        if not art_data and tags:
            covr = tags.get("covr")
            if covr:
                art_data = covr[0]

        if art_data:
            with open(cpath, "wb") as f:
                f.write(art_data)
            return GLib.filename_to_uri(cpath, None)
    except Exception:
        pass
    return ""


def _get_metadata(filepath, filename):
    artist = title = album = art_url = ""
    secs = 0

    try:
        audio = MutagenFile(filepath)
        if audio:
            info = getattr(audio, "info", None)
            if info:
                secs = int(getattr(info, "length", 0))

            art_url = _extract_cover(audio, filepath)

            tags = getattr(audio, "tags", None) or {}
            artist = _get_tag_text(tags, _TAG_ARTIST)
            title = _get_tag_text(tags, _TAG_TITLE)
            album = _get_tag_text(tags, _TAG_ALBUM)
    except Exception:
        pass

    if not artist or not title:
        clean = _TRACK_NUM_RE.sub("", filename).strip()
        if " - " in clean:
            fa, ft = clean.split(" - ", 1)
        elif "-" in clean:
            fa, ft = clean.split("-", 1)
        else:
            fa, ft = "Unknown", clean

        if not artist:
            artist = fa.strip()
        if not title:
            title = ft.strip()

    if not title:
        title = filename

    if secs > 0:
        m, s = divmod(secs, 60)
        duration_str = f"{m}:{s:02d}"
    else:
        duration_str = "--:--"

    return artist, title, album, duration_str, secs * 1_000_000, art_url


class TrackList(Box):
    __slots__ = (
        "_rows",
        "_path_map",
        "_playing_btn",
        "_search_entry",
        "_search_overlay",
        "_search_placeholder",
        "_list_box",
        "_count_lbl",
        "_mon",
        "_pend_id",
        "_dead",
        "local_player",
        "media_player_ref",
        "_current_path",
    )

    def __init__(self, local_player, media_player_ref):
        super().__init__(
            name="track-list",
            orientation="v",
            spacing=4,
            h_align="fill",
            v_align="fill",
            h_expand=False,
            v_expand=True,
        )
        self.set_size_request(400, -1)

        self._rows: list[_TrackRow] = []
        self._path_map: dict[str, int] = {}
        self._playing_btn = None
        self._current_path = None
        self._mon = None
        self._pend_id = 0
        self._dead = False

        self.local_player = local_player
        self.media_player_ref = media_player_ref

        local_player.on_next_cb = lambda: self._play_adjacent(1)
        local_player.on_prev_cb = lambda: self._play_adjacent(-1)

        count_lbl = Label(
            name="track-count",
            label="scanning...",
            h_align="end",
            h_expand=True,
        )
        self._count_lbl = count_lbl

        header = Box(
            name="track-header",
            orientation="h",
            spacing=8,
            h_expand=True,
            h_align="fill",
            children=(
                Label(name="track-icon", markup=icons.disc),
                Label(name="track-title", label="Music Library", h_align="start"),
                count_lbl,
            ),
        )

        search_entry = Gtk.SearchEntry(name="track-search")
        search_entry.set_hexpand(True)
        search_entry.set_halign(Gtk.Align.FILL)
        search_entry.set_alignment(0.0)
        search_entry.set_placeholder_text("")
        search_entry.connect("search-changed", self._on_search)
        search_entry.connect("key-press-event", self._on_search_key_press)
        self._search_entry = search_entry

        search_placeholder = Gtk.Label(name="track-search-placeholder")
        search_placeholder.set_label("Search tracks...")
        search_placeholder.set_halign(Gtk.Align.START)
        search_placeholder.set_valign(Gtk.Align.CENTER)
        self._search_placeholder = search_placeholder

        search_overlay = Gtk.Overlay()
        search_overlay.add(search_entry)
        search_overlay.add_overlay(search_placeholder)
        search_overlay.set_overlay_pass_through(search_placeholder, True)
        self._search_overlay = search_overlay

        list_box = Box(
            name="track-content",
            orientation="v",
            spacing=2,
            h_expand=True,
            v_expand=False,
        )
        self._list_box = list_box

        sw = ScrolledWindow(
            name="track-scrolled",
            child=list_box,
            h_expand=True,
            v_expand=True,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_width=False,
            propagate_height=False,
            min_content_size=(150, 100),
        )
        sw.set_overlay_scrolling(False)

        self.add(header)
        self.add(search_overlay)
        self.add(sw)

        threading.Thread(target=self._scan, daemon=True).start()
        self._watch()

    @staticmethod
    def _make_centered_state(icon: str, text: str, name: str) -> Box:
        inner = Box(
            orientation="v",
            h_align="center",
            v_align="center",
            spacing=12,
            children=[
                Image(icon_name=icon, icon_size=48, name="explorer-empty-icon"),
                Label(name="explorer-empty-label", label=text),
            ],
        )
        wrapper = Box(
            name=name,
            orientation="v",
            h_expand=True,
            v_expand=True,
            h_align="fill",
            v_align="fill",
        )
        wrapper.pack_start(inner, True, False, 0)
        return wrapper

    def _emit_nav(self, has_visible: bool):
        lp = self.local_player
        lp.can_go_previous = has_visible
        lp.can_go_next = has_visible
        lp.emit("changed")

    def _on_search_key_press(self, _widget, event):
        if event.keyval != Gdk.KEY_Escape:
            return False
        self._search_entry.set_text("")
        toplevel = self.get_toplevel()
        if isinstance(toplevel, Gtk.Window):
            toplevel.set_focus(None)
        return True

    def _is_click_on_search(self, root_widget, event_x, event_y):
        try:
            search = self._search_entry
            ok, sx, sy = root_widget.translate_coordinates(
                search, int(event_x), int(event_y),
            )
            if ok:
                alloc = search.get_allocation()
                return 0 <= sx <= alloc.width and 0 <= sy <= alloc.height
        except Exception:
            pass
        return False

    def _on_search(self, entry):
        query = entry.get_text().lower().strip()
        self._search_placeholder.set_visible(not query)

        visible_count = 0
        for row in self._rows:
            visible = not query or query in row.search_str
            row.btn.set_visible(visible)
            visible_count += visible

        total = len(self._rows)
        self._count_lbl.set_text(
            f"{visible_count}/{total}" if query else f"{total} tracks",
        )
        self._emit_nav(visible_count > 0)

    def _watch(self):
        if not os.path.isdir(_MUSIC_DIR):
            return
        try:
            gfile = Gio.File.new_for_path(_MUSIC_DIR)
            self._mon = gfile.monitor_directory(Gio.FileMonitorFlags.NONE, None)
            self._mon.connect("changed", self._on_fs_changed)
        except GLib.Error:
            pass

    def _on_fs_changed(self, *_args):
        if self._dead:
            return
        if self._pend_id:
            GLib.source_remove(self._pend_id)
        self._pend_id = GLib.timeout_add(1000, self._deferred_rescan)

    def _deferred_rescan(self):
        self._pend_id = 0
        threading.Thread(target=self._scan, daemon=True).start()
        return False

    def _scan(self):
        tracks = []
        if os.path.isdir(_MUSIC_DIR):
            get_meta = _get_metadata
            append = tracks.append
            join = os.path.join
            splitext = os.path.splitext
            exts = _AUDIO_EXTS
            for root, dirs, files in os.walk(_MUSIC_DIR):
                dirs.sort()
                files.sort()
                for fname in files:
                    name, ext = splitext(fname)
                    if ext.lower() not in exts:
                        continue
                    full = join(root, fname)
                    a, t, al, ds, lu, au = get_meta(full, name)
                    append((a, t, al, ds, f"{a} {t} {al}".lower(), full, lu, au))

        GLib.idle_add(self._populate, tracks)

    def _populate(self, tracks):
        list_box = self._list_box

        for child in list_box.get_children():
            child.destroy()

        self._rows.clear()
        self._path_map.clear()

        if not tracks:
            list_box.add(
                self._make_centered_state(
                    "folder-open-symbolic",
                    "Folder music is empty",
                    "explorer-empty-state",
                ),
            )
            self._count_lbl.set_text("0 tracks")
            list_box.show_all()
            return

        rows = self._rows
        path_map = self._path_map
        list_add = list_box.add
        on_clicked = self._on_row_clicked
        escape = GLib.markup_escape_text
        unknown = _UNKNOWN_ARTISTS

        for artist, title, album, dur_str, search_str, full, length_us, art_url in tracks:
            t_esc = escape(title, -1)
            if artist and artist.lower() not in unknown:
                a_esc = escape(artist, -1)
                display = f"<b>{a_esc}</b>  <span alpha='50%'>—</span>  {t_esc}"
            else:
                display = t_esc

            info_lbl = Label(
                name="track-name",
                markup=display,
                h_expand=True,
                h_align="start",
                v_align="center",
                ellipsization="end",
                max_chars_width=50,
            )
            dur_lbl = Label(
                name="track-duration",
                label=dur_str,
                h_expand=False,
                h_align="end",
                v_align="center",
            )
            btn = Button(
                name="track-row",
                child=Box(orientation="h", h_expand=True, children=[info_lbl, dur_lbl]),
                h_expand=True,
                h_align="fill",
                tooltip_text=full,
            )
            btn.connect("clicked", on_clicked, full)
            _hover(btn)

            path_map[full] = len(rows)
            rows.append(
                _TrackRow(btn, search_str, full, artist, title, album, length_us, art_url),
            )
            list_add(btn)

        self._count_lbl.set_text(f"{len(tracks)} tracks")
        list_box.show_all()
        self._emit_nav(any(r.btn.get_visible() for r in self._rows))

    def _on_row_clicked(self, _btn, path):
        self._play_by_path(path)

    def _stop_current(self):
        self.local_player.stop()
        self._current_path = None
        btn = self._playing_btn
        if btn:
            btn.remove_style_class("playing")
            self._playing_btn = None
        self._emit_nav(any(r.btn.get_visible() for r in self._rows))

    def _play_by_path(self, path, force=False):
        idx = self._path_map.get(path)
        if idx is None:
            return

        if self._current_path == path and not force:
            self._stop_current()
            return

        row = self._rows[idx]
        self._current_path = path

        old_btn = self._playing_btn
        if old_btn:
            old_btn.remove_style_class("playing")

        new_btn = row.btn
        new_btn.add_style_class("playing")
        self._playing_btn = new_btn

        self.media_player_ref.switch_to_local()
        self._emit_nav(True)
        self.local_player.play_file(
            path, row.artist, row.title, row.album, row.length_us, row.art_url,
        )

    def _play_adjacent(self, direction: int):
        current = self._current_path
        vis = []
        current_idx = -1
        for row in self._rows:
            if row.btn.get_visible():
                if row.path == current:
                    current_idx = len(vis)
                vis.append(row)

        if not vis:
            return

        lp = self.local_player

        if lp.loop_status == "Track":
            target = current_idx if current_idx != -1 else (0 if direction > 0 else -1)
        elif lp.shuffle:
            target = random.randrange(len(vis))
        elif current_idx != -1:
            target = current_idx + direction
        else:
            target = 0 if direction > 0 else -1

        self._play_by_path(vis[target % len(vis)].path, force=True)

    def cleanup(self):
        self._dead = True

        if mon := self._mon:
            try:
                mon.cancel()
            except Exception:
                pass
            self._mon = None

        if pend_id := self._pend_id:
            try:
                GLib.source_remove(pend_id)
            except Exception:
                pass
            self._pend_id = 0

        self._stop_current()
        self._rows.clear()
        self._path_map.clear()