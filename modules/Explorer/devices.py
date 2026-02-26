import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Gio

from pathlib import Path


class DevicesMixin:
    def _setup_volume_monitor(self):
        try:
            self._volume_monitor = Gio.VolumeMonitor.get()
            self._volume_monitor.connect("mount-added", self._on_mount_changed)
            self._volume_monitor.connect("mount-removed", self._on_mount_changed)
            self._volume_monitor.connect("volume-added", self._on_volume_changed)
            self._volume_monitor.connect("volume-removed", self._on_volume_changed)
            self._volume_monitor.connect("drive-connected", self._on_drive_changed)
            self._volume_monitor.connect("drive-disconnected", self._on_drive_changed)
            self._refresh_devices()
        except Exception as e:
            print(f"Error setting up volume monitor: {e}")

    def _on_mount_changed(self, monitor, mount):
        GLib.idle_add(self._refresh_devices)
        GLib.idle_add(self._update_eject_button)

    def _on_volume_changed(self, monitor, volume):
        GLib.idle_add(self._refresh_devices)

    def _on_drive_changed(self, monitor, drive):
        GLib.idle_add(self._refresh_devices)

    def _refresh_devices(self) -> bool:
        if not self._devices_container:
            return False
        for child in self._devices_container.get_children():
            child.destroy()
        self._populate_devices(self._devices_container)
        self._devices_container.show_all()
        return False

    def _populate_devices(self, container):
        if not self._volume_monitor:
            return
        added_identifiers = set()
        add = container.add
        try:
            mounts = self._volume_monitor.get_mounts()
            for mount in mounts:
                try:
                    root = mount.get_root()
                    if not root:
                        continue
                    path_str = root.get_path()
                    if not path_str:
                        continue
                    
                    if path_str == "/" or path_str.startswith("/boot") or path_str.startswith("/snap") or path_str.startswith("/var/snap"):
                        continue
                        
                    identifier = f"mount:{path_str}"
                    if identifier in added_identifiers:
                        continue
                    added_identifiers.add(identifier)
                    
                    volume = mount.get_volume()
                    if volume:
                        vol_id = volume.get_identifier("uuid") or volume.get_name()
                        if vol_id:
                            added_identifiers.add(f"volume:{vol_id}")
                            
                    path = Path(path_str)
                    name = mount.get_name() or path.name or "Unknown"
                    icon_name = self._get_mount_icon(mount)
                    row = self._create_device_row(icon_name, name, path, mount)
                    add(row)
                except:
                    continue
                    
            volumes = self._volume_monitor.get_volumes()
            for volume in volumes:
                try:
                    mount = volume.get_mount()
                    if mount:
                        continue
                    vol_id = volume.get_identifier("uuid") or volume.get_name() or str(id(volume))
                    identifier = f"volume:{vol_id}"
                    if identifier in added_identifiers:
                        continue
                    added_identifiers.add(identifier)
                    if not volume.can_mount():
                        continue
                    name = volume.get_name() or "Unknown Volume"
                    icon_name = self._get_volume_icon(volume)
                    row = self._create_unmounted_volume_row(icon_name, name, volume)
                    add(row)
                except:
                    continue
        except:
            pass

    def _get_mount_icon(self, mount) -> str:
        try:
            icon = mount.get_icon()
            if icon and isinstance(icon, Gio.ThemedIcon):
                names = icon.get_names()
                if names:
                    for name in names:
                        if "symbolic" in name:
                            return name
                    return names[0]
        except:
            pass
        return "drive-removable-media-symbolic"

    def _get_volume_icon(self, volume) -> str:
        try:
            icon = volume.get_icon()
            if icon and isinstance(icon, Gio.ThemedIcon):
                names = icon.get_names()
                if names:
                    for name in names:
                        if "symbolic" in name:
                            return name
                    return names[0]
        except:
            pass
        return "drive-removable-media-symbolic"

    def _create_device_row(self, icon_name: str, label_text: str, path: Path, mount) -> Gtk.Button:
        icon = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.LARGE_TOOLBAR)
        icon.set_pixel_size(24)
        icon.set_name("explorer-bookmark-icon")
        name_label = Gtk.Label(label=label_text)
        name_label.set_name("explorer-file-name")
        name_label.set_halign(Gtk.Align.START)
        name_label.set_hexpand(True)
        name_label.set_ellipsize(3)
        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        content.pack_start(icon, False, False, 0)
        content.pack_start(name_label, True, True, 0)
        btn = Gtk.Button()
        btn.set_name("explorer-file-row")
        btn.add(content)
        btn._path = path
        btn._mount = mount
        btn._device_name = label_text
        btn.connect("clicked", self._on_device_nav_clicked)
        btn.get_style_context().add_class("directory")
        btn.get_style_context().add_class("device")
        btn.show_all()
        self._setup_drop_target(btn, target_path=path)
        return btn

    def _create_unmounted_volume_row(self, icon_name: str, label_text: str, volume) -> Gtk.Button:
        icon = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.LARGE_TOOLBAR)
        icon.set_pixel_size(24)
        icon.set_name("explorer-bookmark-icon")
        name_label = Gtk.Label(label=label_text)
        name_label.set_name("explorer-file-name")
        name_label.set_halign(Gtk.Align.START)
        name_label.set_hexpand(True)
        name_label.set_ellipsize(3)
        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        content.pack_start(icon, False, False, 0)
        content.pack_start(name_label, True, True, 0)
        btn = Gtk.Button()
        btn.set_name("explorer-file-row")
        btn.add(content)
        btn._volume = volume
        btn._device_name = label_text
        btn.set_tooltip_text(f"Click to mount {label_text}")
        btn.connect("clicked", self._on_mount_volume_clicked)
        btn.get_style_context().add_class("directory")
        btn.get_style_context().add_class("unmounted")
        btn.show_all()
        return btn

    def _on_device_nav_clicked(self, btn):
        self._set_navigation_lock()
        if self._pending_drop_source:
            return
        if hasattr(btn, '_path') and btn._path:
            self._navigate_to(btn._path)

    def _on_mount_volume_clicked(self, button):
        self._set_navigation_lock()
        volume = getattr(button, '_volume', None)
        name = getattr(button, '_device_name', 'volume')
        if not volume:
            self.status_label.set_label("No volume to mount")
            return
        button.set_sensitive(False)
        self.status_label.set_label(f"Mounting {name}...")
        try:
            volume.mount(Gio.MountMountFlags.NONE, None, None, self._on_mount_finished, (name, button, volume))
        except Exception as e:
            self.status_label.set_label(f"Mount error: {e}")
            button.set_sensitive(True)

    def _on_mount_finished(self, volume, result, user_data):
        name, button, vol = user_data
        try:
            vol.mount_finish(result)
            GLib.idle_add(lambda: self.status_label.set_label(f"Mounted: {name}"))

            def navigate_after_mount():
                mount = vol.get_mount()
                if mount:
                    root = mount.get_root()
                    if root:
                        path_str = root.get_path()
                        if path_str:
                            self._navigate_to(Path(path_str))
                return False

            GLib.timeout_add(100, navigate_after_mount)
        except Exception as e:
            GLib.idle_add(lambda: self.status_label.set_label(f"Mount failed: {str(e)}"))
            GLib.idle_add(lambda: button.set_sensitive(True) if button else None)

    def _find_mount_for_path(self, path: Path):
        if not self._volume_monitor:
            return None, None, None
        try:
            path_resolved_str = str(path.resolve())
            mounts = self._volume_monitor.get_mounts()
            best_match, best_path, best_name, best_len = None, None, None, 0
            for mount in mounts:
                try:
                    root = mount.get_root()
                    if not root:
                        continue
                    mount_path_str = root.get_path()
                    if not mount_path_str:
                        continue
                    mount_path = Path(mount_path_str).resolve()
                    if mount_path == Path("/"):
                        continue
                    mount_str = str(mount_path)
                    
                    if path_resolved_str == mount_str or path_resolved_str.startswith(mount_str + "/"):
                        length = len(mount_str)
                        if length > best_len:
                            best_match = mount
                            best_path = mount_path
                            best_name = mount.get_name() or mount_path.name
                            best_len = length
                except:
                    continue
            return best_match, best_path, best_name
        except:
            return None, None, None

    def _update_eject_button(self):
        mount, mount_path, mount_name = self._find_mount_for_path(self._current_path)
        if mount and (mount.can_eject() or mount.can_unmount()):
            self._current_mount = mount
            self._current_mount_path = mount_path
            self._current_mount_name = mount_name
            self.btn_eject.set_tooltip_text(f"Eject {mount_name}")
            self.btn_eject.show()
        else:
            self._current_mount = None
            self._current_mount_path = None
            self._current_mount_name = None
            self.btn_eject.hide()

    def _on_header_eject_clicked(self, btn):
        self._set_navigation_lock()
        if not self._current_mount:
            self.status_label.set_label("No device to eject")
            return
        mount = self._current_mount
        name = self._current_mount_name or "device"
        self._navigate_to(Path.home())
        btn.set_sensitive(False)
        self.status_label.set_label(f"Ejecting {name}...")
        try:
            if mount.can_eject():
                mount.eject_with_operation(Gio.MountUnmountFlags.NONE, None, None, self._on_header_eject_finished, (name, btn))
            elif mount.can_unmount():
                mount.unmount_with_operation(Gio.MountUnmountFlags.NONE, None, None, self._on_header_unmount_finished, (name, btn))
            else:
                self.status_label.set_label(f"Cannot eject {name}")
                btn.set_sensitive(True)
        except Exception as e:
            self.status_label.set_label(f"Error: {e}")
            btn.set_sensitive(True)

    def _on_header_eject_finished(self, mount, result, user_data):
        name, button = user_data
        try:
            mount.eject_with_operation_finish(result)
            GLib.idle_add(lambda: self.status_label.set_label(f"Ejected: {name}"))
        except Exception as e:
            GLib.idle_add(lambda: self.status_label.set_label(f"Eject failed: {str(e)}"))
        GLib.idle_add(lambda: button.set_sensitive(True) if button else None)

    def _on_header_unmount_finished(self, mount, result, user_data):
        name, button = user_data
        try:
            mount.unmount_with_operation_finish(result)
            GLib.idle_add(lambda: self.status_label.set_label(f"Unmounted: {name}"))
        except Exception as e:
            GLib.idle_add(lambda: self.status_label.set_label(f"Unmount failed: {str(e)}"))
        GLib.idle_add(lambda: button.set_sensitive(True) if button else None)