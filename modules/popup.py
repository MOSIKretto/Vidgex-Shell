from fabric.widgets.box import Box
from modules.Notifications.history import NotificationHistory, NotificationContainer
from services.wayland import WaylandWindow as Window


class NotificationPopup(Window):
    __slots__ = ('notification_history', 'notification_container', '_spacer')

    def __init__(self, **kwargs):
        super().__init__(
            name="notification-popup", anchor="right top", layer="top",
            keyboard_mode="none", exclusivity="none", visible=True, all_visible=True,
        )

        widgets = kwargs.get("widgets")
        self.notification_history = widgets.notification_history if widgets else NotificationHistory()
        self.notification_container = NotificationContainer(
            notification_history_instance=self.notification_history,
            revealer_transition_type="slide-down",
        )

        self._spacer = Box()
        self._spacer.set_size_request(1, 1)

        self.add(Box(name="notification-popup-box", orientation="v", children=[self.notification_container, self._spacer]))

    def destroy(self):
        if getattr(self, 'notification_container', None):
            self.notification_container.destroy()
            self.notification_container = None
        if getattr(self, '_spacer', None):
            self._spacer.destroy()
            self._spacer = None
        self.notification_history = None
        super().destroy()