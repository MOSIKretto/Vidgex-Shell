from fabric.widgets.box import Box
from fabric.widgets.label import Label
from gi.repository import GdkPixbuf, GLib

from .historyStorage import get_safe_image_path, is_safe_image_file, submit_io_task


class HistoricalNotification:
    __slots__ = ("id", "summary", "body", "app_name", "timestamp")

    def __init__(self, *, id, summary, body, app_name, timestamp):
        self.id = id
        self.summary = summary
        self.body = body
        self.app_name = app_name
        self.timestamp = timestamp


class HistoryEntry(Box):
    def __init__(self, notification: HistoricalNotification, server,
                 pixbuf: GdkPixbuf.Pixbuf | None = None):
        super().__init__(name="notification-box", orientation="v", h_align="fill", h_expand=True)
        self.notification = notification
        self.uuid = notification.id
        self._server = server
        self._destroyed = False
        self._thumb_path = get_safe_image_path(self.uuid)

        self.image_box = Box(name="notification-image", orientation="v")
        self._load_image(pixbuf)
        self.add(self._create_content())
        self.connect("destroy", self._on_destroy)

    def _load_image(self, pixbuf: GdkPixbuf.Pixbuf | None) -> None:
        if pixbuf is not None:
            self._apply_pixbuf(pixbuf)
            path = self._thumb_path
            submit_io_task(lambda: self._save_pixbuf_sync(pixbuf, path))
            return
        submit_io_task(self._read_thumbnail)

    def _read_thumbnail(self) -> None:
        path = self._thumb_path
        if not is_safe_image_file(path):
            return
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file(path)
        except GLib.Error:
            return
        GLib.idle_add(self._apply_pixbuf, pb)

    @staticmethod
    def _save_pixbuf_sync(pb: GdkPixbuf.Pixbuf, path: str) -> None:
        try:
            pb.savev(path, "png", [], [])
        except GLib.Error:
            pass

    def _apply_pixbuf(self, pb: GdkPixbuf.Pixbuf) -> bool:
        if self._destroyed:
            return False
        img = self._server.make_image(pb)
        for child in self.image_box.get_children():
            child.destroy()
        self.image_box.add(img)
        self.image_box.show_all()
        return False

    def _create_content(self) -> Box:
        n = self.notification
        summary = Label(
            name="notification-summary",
            label=str(n.summary),
            h_align="start",
            max_chars_width=20,
            ellipsization="end",
        )
        app_name_label = Label(
            name="notification-app-name",
            label=(n.app_name or "Unknown")[:20],
            h_align="start",
            max_chars_width=12,
            ellipsization="end",
        )
        text_children = [
            Box(
                name="notification-summary-box",
                orientation="h",
                children=[summary, Box(name="notif-sep"), app_name_label],
            )
        ]
        if n.body:
            text_children.append(
                Label(
                    name="notification-body",
                    markup=self._server.safe_markup(str(n.body)),
                    h_align="start",
                    max_chars_width=40,
                    ellipsization="end",
                )
            )
        return Box(
            name="notification-content",
            spacing=8,
            h_expand=True,
            children=[
                self.image_box,
                Box(name="notification-text", orientation="v", v_align="center",
                    h_expand=True, children=text_children),
            ],
        )

    def _on_destroy(self, _widget) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        self.notification = None
        self._server = None