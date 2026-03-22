from gi.repository import GLib


class Visibility:
    HIDE_DELAY_MS = 350
    SHOW_DELAY_MS = 0
    POLL_INTERVAL_MS = 500
    ACTIVATOR_HEIGHT = 12

    def __init__(self, dock):
        self._dock = dock

        self._mouse_over  = False
        self._is_hidden   = False
        self._drag_active = False

        self._dock_width  = 0
        self._dock_height = 0

        self._hide_timer = None
        self._show_timer = None
        self._poll_timer = None

    def start(self):
        self._stop_poll()
        self._poll_timer = GLib.timeout_add(
            self.POLL_INTERVAL_MS, self._on_poll
        )

    def stop(self):
        self._cancel_hide()
        self._cancel_show()
        self._stop_poll()

    def mouse_enter(self):
        self._mouse_over = True
        self._cancel_hide()
        self._request_show()

    def mouse_leave(self):
        self._mouse_over = False
        self._request_hide()

    def set_drag(self, active: bool):
        self._drag_active = active
        if active:
            self._cancel_hide()
        else:
            self._request_hide()

    def update_size(self, width: int, height: int):
        if not self._is_hidden and width > 10:
            self._dock_width  = width
            self._dock_height = height

    def check_now(self, clients=None):
        self._evaluate(clients=clients)

    @property
    def is_hidden(self):
        return self._is_hidden

    @property
    def mouse_over(self):
        return self._mouse_over

    @property
    def drag_active(self):
        return self._drag_active

    def _request_show(self):
        self._cancel_hide()
        if not self._is_hidden:
            return
        if self.SHOW_DELAY_MS > 0:
            if self._show_timer is None:
                self._show_timer = GLib.timeout_add(
                    self.SHOW_DELAY_MS, self._do_show
                )
        else:
            self._do_show()

    def _request_hide(self):
        self._cancel_show()
        if self._is_hidden:
            return
        self._cancel_hide()
        self._hide_timer = GLib.timeout_add(
            self.HIDE_DELAY_MS, self._evaluate
        )

    def _do_show(self):
        self._show_timer = None
        if not self._is_hidden:
            return False
        self._is_hidden = False
        self._dock.revealer.set_reveal_child(True)
        return False

    def _do_hide(self):
        if self._is_hidden:
            return
        self._is_hidden = True
        self._dock.revealer.set_reveal_child(False)

    def _evaluate(self, clients=None):
        self._hide_timer = None

        if self._mouse_over or self._drag_active:
            if self._is_hidden:
                self._do_show()
            return False

        if self._has_overlap(clients):
            self._do_hide()
        elif self._is_hidden:
            self._do_show()
        return False

    def _has_overlap(self, clients=None):
        dock = self._dock
        dw = self._dock_width
        if not dw:
            return False

        dh = self._dock_height or 60
        dx = dock._mon_x + (dock._mon_w - dw) // 2
        dy = dock._mon_y + dock._mon_h - dh
        dx2 = dx + dw
        dy2 = dy + dh

        ws    = dock._parse("j/activeworkspace")
        ws_id = ws.get("id", 0) if ws else 0

        if clients is None:
            clients = dock._parse("j/clients")

        for w in clients:
            if w.get("hidden") or w.get("minimized"):
                continue

            w_ws = w.get("workspace", {})
            wid  = w_ws.get("id") if isinstance(w_ws, dict) else w_ws
            if wid != ws_id:
                continue
            if w.get("monitor") != dock.monitor_id:
                continue

            pos, size = w.get("at"), w.get("size")
            if not pos or not size:
                continue

            wx, wy, ww, wh = pos[0], pos[1], size[0], size[1]
            if (
                ww > 0 and wh > 0
                and wx < dx2 and wx + ww > dx
                and wy < dy2 and wy + wh > dy
            ):
                return True
        return False

    def _on_poll(self):
        if not self._mouse_over and not self._drag_active:
            self._evaluate()
        return True

    def _cancel_hide(self):
        if self._hide_timer is not None:
            GLib.source_remove(self._hide_timer)
            self._hide_timer = None

    def _cancel_show(self):
        if self._show_timer is not None:
            GLib.source_remove(self._show_timer)
            self._show_timer = None

    def _stop_poll(self):
        if self._poll_timer is not None:
            GLib.source_remove(self._poll_timer)
            self._poll_timer = None