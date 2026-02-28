import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Gio

from pathlib import Path
from typing import Optional, Tuple, Set


class DevicesMixin:

    @staticmethod
    def _algo_resolve_icon(gio_icon: Gio.Icon, fallback_drive: Gio.Drive = None) -> str:
        if gio_icon and isinstance(gio_icon, Gio.ThemedIcon):
            theme = Gtk.IconTheme.get_default()
            names = gio_icon.get_names()
            if names:
                for name in names:
                    if "symbolic" in name and theme.has_icon(name):
                        return name
                for name in names:
                    if theme.has_icon(name):
                        return name

        if fallback_drive:
            if fallback_drive.can_eject():
                return "media-removable-symbolic"
            if fallback_drive.has_media():
                return "drive-optical-symbolic"
                
        return "drive-harddisk-symbolic"

    @staticmethod
    def _algo_is_system_volume(volume: Gio.Volume) -> bool:
        try:
            return hasattr(volume, 'is_system_internal') and volume.is_system_internal()
        except Exception:
            return False

    @staticmethod
    def _algo_best_mount_for_path(mounts: list, path_resolved_str: str) -> Tuple[Optional[Gio.Mount], Optional[Path], Optional[str]]:
        best_mount = None
        best_path = None
        best_name = None
        best_len = 0

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
                    
                ms = str(mount_path)
                if path_resolved_str == ms or path_resolved_str.startswith(ms + "/"):
                    if len(ms) > best_len:
                        best_mount = mount
                        best_path = mount_path
                        best_name = mount.get_name() or mount_path.name
                        best_len = len(ms)
            except Exception:
                continue

        return best_mount, best_path, best_name

    def _setup_volume_monitor(self):
        try:
            vm = Gio.VolumeMonitor.get()
            vm.connect("mount-added", self._h_mount_changed)
            vm.connect("mount-removed", self._h_mount_changed)
            vm.connect("volume-added", self._h_volume_changed)
            vm.connect("volume-removed", self._h_volume_changed)
            vm.connect("drive-connected", self._h_drive_changed)
            vm.connect("drive-disconnected", self._h_drive_changed)
            
            self._volume_monitor = vm
            self._refresh_devices()
        except Exception as e:
            print(f"Error setting up volume monitor: {e}")

    def _h_mount_changed(self, monitor, mount):
        GLib.idle_add(self._refresh_devices)
        GLib.idle_add(self._update_eject_button)

    def _h_volume_changed(self, monitor, volume):
        GLib.idle_add(self._refresh_devices)

    def _h_drive_changed(self, monitor, drive):
        GLib.idle_add(self._refresh_devices)

    def _refresh_devices(self) -> bool:
        if not self._devices_container:
            return False
            
        for child in self._devices_container.get_children():
            child.destroy()
            
        self._populate_devices()
        self._devices_container.show_all()
        return False

    def _populate_devices(self):
        if not self._volume_monitor:
            return

        added = set()

        try:
            for mount in self._volume_monitor.get_mounts():
                self._add_mount_row(mount, self._devices_container, added)
        except Exception:
            pass

        try:
            for volume in self._volume_monitor.get_volumes():
                self._add_unmounted_row(volume, self._devices_container, added)
        except Exception:
            pass

    def _add_mount_row(self, mount: Gio.Mount, container: Gtk.Box, added: Set[str]):
        try:
            volume = mount.get_volume()
            if volume and self._algo_is_system_volume(volume):
                return

            root = mount.get_root()
            if not root:
                return
                
            path_str = root.get_path()
            if not path_str or path_str == "/":
                return

            ident = f"mount:{path_str}"
            if ident in added:
                return
            added.add(ident)

            if volume:
                vol_id = volume.get_identifier("uuid") or volume.get_name()
                if vol_id:
                    added.add(f"volume:{vol_id}")

            path = Path(path_str)
            name = mount.get_name() or path.name or "Unknown"
            icon_name = self._algo_resolve_icon(mount.get_icon(), mount.get_drive())

            row = self._create_device_row(icon_name, name, path, mount)
            container.add(row)
        except Exception:
            pass

    def _add_unmounted_row(self, volume: Gio.Volume, container: Gtk.Box, added: Set[str]):
        try:
            if volume.get_mount() or self._algo_is_system_volume(volume) or not volume.can_mount():
                return

            vol_id = volume.get_identifier("uuid") or volume.get_name() or str(id(volume))
            ident = f"volume:{vol_id}"
            
            if ident in added:
                return
            added.add(ident)

            name = volume.get_name() or "Unknown Volume"
            icon_name = self._algo_resolve_icon(volume.get_icon(), volume.get_drive())

            row = self._create_unmounted_volume_row(icon_name, name, volume)
            container.add(row)
        except Exception:
            pass

    def _create_device_row_content(self, icon_name: str, label_text: str) -> Gtk.Box:
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
        return content

    def _create_device_row(self, icon_name: str, label_text: str, path: Path, mount: Gio.Mount) -> Gtk.Button:
        btn = Gtk.Button()
        btn.set_name("explorer-file-row")
        btn.add(self._create_device_row_content(icon_name, label_text))
        
        btn._path = path
        btn._mount = mount
        btn._device_name = label_text
        btn.connect("clicked", self._on_device_nav_clicked)
        
        ctx = btn.get_style_context()
        ctx.add_class("directory")
        ctx.add_class("device")
        
        btn.show_all()
        self._setup_drop_target(btn, target_path=path)
        return btn

    def _create_unmounted_volume_row(self, icon_name: str, label_text: str, volume: Gio.Volume) -> Gtk.Button:
        btn = Gtk.Button()
        btn.set_name("explorer-file-row")
        btn.add(self._create_device_row_content(icon_name, label_text))
        
        btn._volume = volume
        btn._device_name = label_text
        btn.set_tooltip_text(f"Click to mount {label_text}")
        btn.connect("clicked", self._on_mount_volume_clicked)
        
        ctx = btn.get_style_context()
        ctx.add_class("directory")
        ctx.add_class("unmounted")
        
        btn.show_all()
        return btn

    def _find_mount_for_path(self, path: Path) -> Tuple[Optional[Gio.Mount], Optional[Path], Optional[str]]:
        if not self._volume_monitor:
            return None, None, None
        try:
            return self._algo_best_mount_for_path(self._volume_monitor.get_mounts(), str(path.resolve()))
        except Exception:
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

    def _on_device_nav_clicked(self, btn: Gtk.Button):
        self._lock_set()
        if self._pending_drop_source:
            return
            
        path = getattr(btn, '_path', None)
        if path:
            self._navigate_to(path)

    def _on_mount_volume_clicked(self, button: Gtk.Button):
        self._lock_set()
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

    def _navigate_to_mount(self, vol: Gio.Volume):
        try:
            mount = vol.get_mount()
            if mount:
                root = mount.get_root()
                if root and root.get_path():
                    self._navigate_to(Path(root.get_path()))
        except Exception:
            pass
        return False

    def _on_mount_finished(self, volume: Gio.Volume, result: Gio.AsyncResult, user_data: tuple):
        name, button, vol = user_data
        try:
            vol.mount_finish(result)
            GLib.idle_add(self.status_label.set_label, f"Mounted: {name}")
            GLib.idle_add(self._navigate_to_mount, vol)
        except Exception as e:
            GLib.idle_add(self.status_label.set_label, f"Mount failed: {e}")
            if button:
                GLib.idle_add(button.set_sensitive, True)

    def _on_header_eject_clicked(self, btn: Gtk.Button):
        self._lock_set()
        mount = self._current_mount
        if not mount:
            self.status_label.set_label("No device to eject")
            return

        name = self._current_mount_name or "device"
        self._navigate_to(Path.home())
        btn.set_sensitive(False)
        self.status_label.set_label(f"Ejecting {name}...")

        try:
            if mount.can_eject():
                mount.eject_with_operation(Gio.MountUnmountFlags.NONE, None, None, self._on_eject_finished, (name, btn))
            elif mount.can_unmount():
                mount.unmount_with_operation(Gio.MountUnmountFlags.NONE, None, None, self._on_unmount_finished, (name, btn))
            else:
                self.status_label.set_label(f"Cannot eject {name}")
                btn.set_sensitive(True)
        except Exception as e:
            self.status_label.set_label(f"Error: {e}")
            btn.set_sensitive(True)

    def _on_eject_finished(self, mount: Gio.Mount, result: Gio.AsyncResult, user_data: tuple):
        name, button = user_data
        try:
            mount.eject_with_operation_finish(result)
            GLib.idle_add(self.status_label.set_label, f"Ejected: {name}")
        except Exception as e:
            GLib.idle_add(self.status_label.set_label, f"Eject failed: {e}")
            
        if button:
            GLib.idle_add(button.set_sensitive, True)

    def _on_unmount_finished(self, mount: Gio.Mount, result: Gio.AsyncResult, user_data: tuple):
        name, button = user_data
        try:
            mount.unmount_with_operation_finish(result)
            GLib.idle_add(self.status_label.set_label, f"Unmounted: {name}")
        except Exception as e:
            GLib.idle_add(self.status_label.set_label, f"Unmount failed: {e}")
            
        if button:
            GLib.idle_add(button.set_sensitive, True)