from fabric.widgets.box import Box

from widgets.wayland import WaylandWindow as Window
from .history import NotificationHistory, NotificationContainer


class NotificationPopup(Window):
    def __init__(self, **kwargs):
        super().__init__(
            name="notification-popup",
            anchor="right top",
            layer="top",
            keyboard_mode="none",
            exclusivity="none",
            visible=True,
            all_visible=True,
        )

        self.widgets = kwargs.get("widgets", None)

        if self.widgets: self.notification_history = self.widgets.notification_history
        else: self.notification_history = NotificationHistory()
        
        self.notification_container = NotificationContainer(
            notification_history_instance=self.notification_history,
            revealer_transition_type="slide-down" if True else "slide-up",
        )

        self.show_box = Box()
        self.show_box.set_size_request(1, 1)

        main_box = Box(
            name="notification-popup-box",
            orientation="v",
            children=[self.notification_container, self.show_box],
        )
        
        self.add(main_box)