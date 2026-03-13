from fabric.widgets.box import Box
from modules.Notifications.history import NotificationHistory, NotificationContainer
from services.wayland import WaylandWindow as Window


class Notification(Window):
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
            history=self.notification_history,
            revealer_transition="slide-down",
            window=self,
        )

        self.add(Box(
            name="notification-popup-box",
            orientation="v",
            children=[self.notification_container, Box()],
        ))

    def destroy(self):
        if self.notification_container:
            self.notification_container.destroy()
            self.notification_container = None
        if self._owns_history and self.notification_history:
            self.notification_history.destroy()
        self.notification_history = None
        super().destroy()