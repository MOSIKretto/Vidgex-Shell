import weakref

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer

import services.icons as icons

NOTIFICATION_WIDTH = 320
GROUP_ANIMATION_DURATION = 200


class NotificationGroup(Box):
    def __init__(self, app_name: str, history, is_expanded: bool = False):
        super().__init__(name="notification-group", orientation="v", h_align="fill", h_expand=True)
        self.set_size_request(NOTIFICATION_WIDTH, -1)
        self.app_name = app_name[:30]
        self._history_ref = weakref.ref(history)
        self.notification_ids: list = []
        self.is_expanded = is_expanded
        self.latest_arrival_time = None
        self._is_destroyed = False
        self._build_ui()
        self.connect("destroy", self._on_destroy)

    def _build_ui(self) -> None:
        self.expand_icon = Label(
            name="group-expand-icon",
            markup=icons.chevron_up if self.is_expanded else icons.chevron_down,
        )
        self.expand_icon.set_no_show_all(True)

        self.count_label = Label(name="group-count-label", label="", h_align="end")
        self.count_label.set_no_show_all(True)

        self.header = Button(
            name="group-expand-button",
            h_expand=True,
            child=Box(
                name="group-header-content",
                spacing=8,
                h_expand=True,
                children=[
                    self.expand_icon,
                    Label(name="group-app-name", label=self.app_name, h_align="start",
                          h_expand=True, ellipsization="end", max_chars_width=20),
                    self.count_label,
                ],
            ),
            on_clicked=self._toggle_expand,
        )
        self.clear_btn = Button(
            name="notif-close-button",
            child=Label(name="notif-close-label", markup=icons.cancel),
            on_clicked=self._on_clear_group,
        )
        self.header_row = Box(
            name="notification-group-header",
            orientation="h",
            spacing=4,
            h_expand=True,
            children=[self.header, self.clear_btn],
        )
        self.header_row.set_visible(False)

        self.first_container_box = Box(name="group-first-notification", orientation="v", h_expand=True)

        self.stack_indicator_1 = Box(name="stack-indicator")
        self.stack_indicator_1.add_style_class("first")
        self.stack_indicator_1.set_no_show_all(True)

        self.stack_indicator_2 = Box(name="stack-indicator")
        self.stack_indicator_2.add_style_class("second")
        self.stack_indicator_2.set_no_show_all(True)

        self.stack_indicators_revealer = Revealer(
            name="stack-indicators-revealer",
            transition_type="slide-down",
            transition_duration=GROUP_ANIMATION_DURATION,
            child=Box(name="stack-indicators", orientation="v",
                      children=[self.stack_indicator_1, self.stack_indicator_2]),
            reveal_child=False,
        )
        self.stacked_container = Box(name="group-stacked-container", orientation="v", spacing=4, h_expand=True)
        self.stacked_revealer = Revealer(
            name="group-stacked-revealer",
            transition_type="slide-down",
            transition_duration=GROUP_ANIMATION_DURATION,
            child=self.stacked_container,
            reveal_child=self.is_expanded,
        )
        for w in (self.header_row, self.first_container_box, self.stack_indicators_revealer, self.stacked_revealer):
            self.add(w)

        if self.is_expanded:
            self._apply_expanded_state()

    def _apply_expanded_state(self) -> None:
        self.expand_icon.set_markup(icons.chevron_up)
        self.header_row.add_style_class("expanded")
        self.add_style_class("expanded")

    def _apply_collapsed_state(self) -> None:
        self.expand_icon.set_markup(icons.chevron_down)
        self.header_row.remove_style_class("expanded")
        self.remove_style_class("expanded")

    def _toggle_expand(self, *_) -> None:
        if self._is_destroyed or len(self.notification_ids) <= 1:
            return
        self.is_expanded = not self.is_expanded
        self.stack_indicators_revealer.set_reveal_child(not self.is_expanded)
        self.stacked_revealer.set_reveal_child(self.is_expanded)
        if self.is_expanded:
            self._apply_expanded_state()
        else:
            self._apply_collapsed_state()

    def _on_clear_group(self, *_) -> None:
        if self._is_destroyed:
            return
        self.clear_btn.set_sensitive(False)
        history = self._history_ref()
        if history is not None and not history._is_destroyed:
            history.clear_history_for_app(self.app_name)

    def update_display(self, containers_by_id: dict) -> None:
        if self._is_destroyed or not self.notification_ids:
            return

        valid = sorted(
            [containers_by_id[nid] for nid in self.notification_ids if nid in containers_by_id],
            key=lambda c: c.arrival_time,
            reverse=True,
        )
        if not valid:
            return

        valid_set = set(valid)
        for box in (self.first_container_box, self.stacked_container):
            for child in box.get_children():
                if child not in valid_set:
                    box.remove(child)

        for i, container in enumerate(valid):
            target = self.first_container_box if i == 0 else self.stacked_container
            parent = container.get_parent()
            if parent is not target:
                if parent is not None:
                    parent.remove(container)
                target.pack_start(container, False, False, 0)
            target.reorder_child(container, 0 if i == 0 else i - 1)

        count = len(valid)
        is_multi = count > 1
        self.header_row.set_visible(True)
        self.count_label.set_label(f"+{count - 1}" if is_multi else "")
        self.count_label.set_visible(is_multi)
        self.expand_icon.set_visible(is_multi)
        self.header.set_can_focus(is_multi)

        if not is_multi:
            self.is_expanded = False
            self._apply_collapsed_state()

        self.stacked_revealer.set_reveal_child(self.is_expanded and is_multi)
        self.stack_indicator_1.set_visible(count > 1)
        self.stack_indicator_2.set_visible(count > 2)
        self.stack_indicators_revealer.set_reveal_child(count > 1 and not self.is_expanded)

        self.latest_arrival_time = valid[0].arrival_time
        self.first_container_box.show_all()
        self.stacked_container.show_all()

    def add_notification_id(self, nid, arrival_time) -> None:
        if nid not in self.notification_ids:
            self.notification_ids.insert(0, nid)
        if self.latest_arrival_time is None or arrival_time > self.latest_arrival_time:
            self.latest_arrival_time = arrival_time

    def remove_notification_id(self, nid) -> bool:
        if nid in self.notification_ids:
            self.notification_ids.remove(nid)
        return len(self.notification_ids) == 0

    def get_notification_count(self) -> int:
        return len(self.notification_ids)

    def clear_containers(self) -> None:
        for box in (self.first_container_box, self.stacked_container):
            for child in list(box.get_children()):
                box.remove(child)
        self.notification_ids.clear()
        self.latest_arrival_time = None

    def _on_destroy(self, _widget) -> None:
        if self._is_destroyed:
            return
        self._is_destroyed = True
        self.clear_containers()
        self._history_ref = None