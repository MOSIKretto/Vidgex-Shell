from fabric.widgets.box import Box

from modules.Notifications.history import NotificationContainer, get_shared_history

from services.wayland import WaylandWindow as Window


class Notifications(Window):

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

        self.notification_history = get_shared_history()

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

        if self.notification_container is not None:
            self.notification_container.destroy()
            self.notification_container = None

        self.notification_history = None
        self._spacer = None
        self._popup_box = None

        super().destroy()