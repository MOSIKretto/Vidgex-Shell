import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

import json
import os
import re
from collections import OrderedDict
from loguru import logger

# Performance optimization constants
MAX_ICON_RESOLVER_CACHE = 100  # Limit icon resolver cache

ICON_CACHE_FILE = str(GLib.get_user_cache_dir()) + "/ax-shell/icons.json"

class IconResolver:
    def __init__(self, default_applicaiton_icon="application-x-executable-symbolic"):
        # Use OrderedDict for LRU cache
        self._icon_dict = OrderedDict()
        if os.path.exists(ICON_CACHE_FILE):
            try:
                with open(ICON_CACHE_FILE) as f:
                    cache_data = json.load(f)
                    # Convert to OrderedDict
                    self._icon_dict = OrderedDict(cache_data)
            except json.JSONDecodeError:
                logger.info("[ICONS] Cache file does not exist or is corrupted")

        self.default_applicaiton_icon = default_applicaiton_icon

    def get_icon_name(self, app_id):
        if app_id in self._icon_dict:
            # Move to end (most recently used)
            self._icon_dict.move_to_end(app_id)
            return self._icon_dict[app_id]
        
        new_icon = self._compositor_find_icon(app_id)
        logger.info(f"[ICONS] found new icon: '{new_icon}' for app id: '{app_id}', storing...")
        self._store_new_icon(app_id, new_icon)
        return new_icon

    def get_icon_pixbuf(self, app_id, size=16):
        icon_theme = Gtk.IconTheme.get_default()
        icon_name = self.get_icon_name(app_id)
        
        # Try to load the resolved icon
        try:
            return icon_theme.load_icon(icon_name, size, Gtk.IconLookupFlags.FORCE_SIZE)
        except GLib.Error as primary_error:
            logger.warning(f"Warning: Icon '{icon_name}' not found in theme. Error: {primary_error}")
        
        # Fallback to the default application icon
        try:
            return icon_theme.load_icon(self.default_applicaiton_icon, size, Gtk.IconLookupFlags.FORCE_SIZE)
        except GLib.Error as fallback_error:
            logger.error(f"Error: Fallback icon '{self.default_applicaiton_icon}' also not found. Error: {fallback_error}")
            return None

    def _store_new_icon(self, app_id, icon):
        # Add to cache with LRU eviction
        self._icon_dict[app_id] = icon
        # Limit cache size
        if len(self._icon_dict) > MAX_ICON_RESOLVER_CACHE:
            # Remove oldest item
            self._icon_dict.popitem(last=False)
        with open(ICON_CACHE_FILE, "w") as f:
            json.dump(self._icon_dict, f)

    def _get_icon_from_desktop_file(self, desktop_file_path):
        icon_name = self.default_applicaiton_icon
        try:
            with open(desktop_file_path) as f:
                for line in f:
                    if line.startswith("Icon="):
                        icon_name = line[5:].strip()
                        break
        except:
            pass
        return icon_name

    def _get_desktop_file(self, app_id):
        data_dirs = GLib.get_system_data_dirs()
        app_id_clean = "".join(app_id.lower().split())
        
        for data_dir in data_dirs:
            applications_dir = os.path.join(data_dir, "applications")
            if not os.path.exists(applications_dir):
                continue
                
            files = os.listdir(applications_dir)
            
            # First try: exact match
            for filename in files:
                if app_id_clean in filename.lower():
                    return os.path.join(applications_dir, filename)
            
            # Second try: word match
            words = re.split(r"-|\.|_|\s", app_id)
            for word in words:
                if not word:
                    continue
                for filename in files:
                    if word.lower() in filename.lower():
                        return os.path.join(applications_dir, filename)
        
        return None

    def _compositor_find_icon(self, app_id):
        icon_theme = Gtk.IconTheme.get_default()
        
        if icon_theme.has_icon(app_id):
            return app_id
        if icon_theme.has_icon(app_id + "-desktop"):
            return app_id + "-desktop"
            
        desktop_file = self._get_desktop_file(app_id)
        if desktop_file:
            return self._get_icon_from_desktop_file(desktop_file)
        else:
            return self.default_applicaiton_icon