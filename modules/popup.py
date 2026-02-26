from fabric.widgets.box import Box
from modules.Notifications.history import NotificationHistory, NotificationContainer
from services.wayland import WaylandWindow as Window


class NotificationPopup(Window):

    def __init__(self, **kwargs):
        super().__init__(
            name="notification-popup",
            anchor="right top",
            layer="top",
            keyboard_mode="none",
            exclusivity="none",
            visible=False,
            all_visible=True,
        )

        self._destroyed = False
        self._owns_history = False

        widgets = kwargs.get("widgets")

        if widgets is not None:
            if not hasattr(widgets, "notification_history"):
                raise AttributeError(
                    f"{type(widgets).__name__} has no 'notification_history' attribute"
                )
            self.notification_history = widgets.notification_history
            self._owns_history = False
        else:
            self.notification_history = NotificationHistory()
            self._owns_history = True

        self.notification_container = NotificationContainer(
            notification_history_instance=self.notification_history,
            revealer_transition_type="slide-down",
        )

        self._spacer = Box()
        self._spacer.set_size_request(1, 1)

        self._popup_box = Box(
            name="notification-popup-box",
            orientation="v",
            children=[self.notification_container, self._spacer],
        )
        self.add(self._popup_box)

    def destroy(self):
        if self._destroyed:
            return
        self._destroyed = True

        try:
            if self._owns_history and self.notification_history:
                pass

        except Exception:
            pass
        finally:
            super().destroy()

            self.notification_container = None
            self._spacer = None
            self._popup_box = None
            if self._owns_history:
                self.notification_history = None