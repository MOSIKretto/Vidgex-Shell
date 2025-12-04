import gi
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk

import json
import subprocess

class Signal:
    def __init__(self):
        self._callbacks = []
    
    def connect(self, callback):
        self._callbacks.append(callback)
    
    def emit(self, *args, **kwargs):
        for callback in self._callbacks:
            try:
                callback(*args, **kwargs)
            except Exception as e:
                print(f"Error in signal callback: {e}")


class MonitorManager:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        
        self._initialized = True
        self._monitors = []
        self._focused_monitor_id = 0
        self._notch_states = {}
        self._current_notch_module = {}
        self._monitor_instances = {}
        self._monitor_focus_service = None
        
        self.monitor_changed = Signal()
        self.notch_focus_changed = Signal()
        
        self.refresh_monitors()
    
    def set_monitor_focus_service(self, service):
        self._monitor_focus_service = service
        if service:
            service.monitor_focused.connect(self._on_monitor_focused)
    
    def _get_gtk_monitor_info(self):
        gtk_monitors = []
        try:
            display = Gdk.Display.get_default()
            if display and hasattr(display, 'get_n_monitors'):
                n_monitors = display.get_n_monitors()
                for i in range(n_monitors):
                    monitor = display.get_monitor(i)
                    if monitor:
                        geometry = monitor.get_geometry()
                        scale_factor = monitor.get_scale_factor()
                        model = monitor.get_model()
                        if not model:
                            model = f'monitor-{i}'
                        
                        gtk_monitors.append({
                            'id': i,
                            'name': model,
                            'width': geometry.width,
                            'height': geometry.height,
                            'x': geometry.x,
                            'y': geometry.y,
                            'scale': scale_factor
                        })
        except Exception as e:
            print(f"Error getting GTK monitor info: {e}")
        
        return gtk_monitors

    def refresh_monitors(self):
        self._monitors = []
        
        try:
            result = subprocess.run(
                ["hyprctl", "monitors", "-j"],
                capture_output=True,
                text=True,
                check=True,
                timeout=0.5  # Add timeout to prevent hanging
            )
            hypr_monitors = json.loads(result.stdout)
            
            for i, monitor in enumerate(hypr_monitors):
                monitor_name = monitor.get('name', f'monitor-{i}')
                hypr_scale = monitor.get('scale', 1.0)
                
                self._monitors.append({
                    'id': i,
                    'name': monitor_name,
                    'width': monitor.get('width', 1920),
                    'height': monitor.get('height', 1080),
                    'x': monitor.get('x', 0),
                    'y': monitor.get('y', 0),
                    'focused': monitor.get('focused', False),
                    'scale': hypr_scale
                })
                
                if i not in self._notch_states:
                    self._notch_states[i] = False
                    self._current_notch_module[i] = None
                    
        except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError, subprocess.TimeoutExpired):
            self._fallback_to_gtk()
        
        if not self._monitors:
            self._monitors = [{
                'id': 0,
                'name': 'default',
                'width': 1920,
                'height': 1080,
                'x': 0,
                'y': 0,
                'focused': True,
                'scale': 1.0
            }]
            self._notch_states[0] = False
            self._current_notch_module[0] = None
        
        for monitor in self._monitors:
            if monitor.get('focused', False):
                self._focused_monitor_id = monitor['id']
                break
        
        self.monitor_changed.emit(self._monitors)
        return self._monitors
    
    def _fallback_to_gtk(self):
        try:
            display = Gdk.Display.get_default()
            if display and hasattr(display, 'get_n_monitors'):
                n_monitors = display.get_n_monitors()
                for i in range(n_monitors):
                    monitor = display.get_monitor(i)
                    geometry = monitor.get_geometry()
                    scale_factor = monitor.get_scale_factor()
                    
                    model = monitor.get_model()
                    if not model:
                        model = f'monitor-{i}'
                    
                    self._monitors.append({
                        'id': i,
                        'name': model,
                        'width': geometry.width,
                        'height': geometry.height,
                        'x': geometry.x,
                        'y': geometry.y,
                        'focused': i == 0,
                        'scale': scale_factor
                    })
                    
                    if i not in self._notch_states:
                        self._notch_states[i] = False
                        self._current_notch_module[i] = None
        except Exception:
            pass
    
    def get_monitors(self):
        monitors_copy = []
        for monitor in self._monitors:
            monitors_copy.append(monitor.copy())
        return monitors_copy
    
    def get_monitor_by_id(self, monitor_id):
        for monitor in self._monitors:
            if monitor['id'] == monitor_id:
                return monitor.copy()
        return None
    
    def get_focused_monitor_id(self):
        return self._focused_monitor_id
    
    def get_focused_monitor(self):
        return self.get_monitor_by_id(self._focused_monitor_id)
    
    def get_workspace_range_for_monitor(self, monitor_id):
        start = (monitor_id * 10) + 1
        end = start + 9
        return (start, end)
    
    def get_monitor_for_workspace(self, workspace_id):
        if workspace_id <= 0:
            return 0
        return (workspace_id - 1) // 10
    
    def get_monitor_scale(self, monitor_id):
        monitor = self.get_monitor_by_id(monitor_id)
        if monitor:
            return monitor.get('scale', 1.0)
        return 1.0
    
    def is_notch_open(self, monitor_id):
        return self._notch_states.get(monitor_id, False)
    
    def set_notch_state(self, monitor_id, is_open, module=None):
        self._notch_states[monitor_id] = is_open
        if is_open:
            self._current_notch_module[monitor_id] = module
        else:
            self._current_notch_module[monitor_id] = None
    
    def get_current_notch_module(self, monitor_id):
        return self._current_notch_module.get(monitor_id)
    
    def close_all_notches_except(self, except_monitor_id):
        for monitor_id in self._notch_states:
            if monitor_id != except_monitor_id and self._notch_states[monitor_id]:
                self.set_notch_state(monitor_id, False)
                instances = self._monitor_instances.get(monitor_id, {})
                notch = instances.get('notch')
                if notch and hasattr(notch, 'close'):
                    notch.close()
    
    def register_monitor_instances(self, monitor_id, instances):
        self._monitor_instances[monitor_id] = instances
    
    def get_monitor_instances(self, monitor_id):
        return self._monitor_instances.get(monitor_id, {})
    
    def get_instance(self, monitor_id, component):
        instances = self._monitor_instances.get(monitor_id, {})
        return instances.get(component)
    
    def get_focused_instance(self, component):
        return self.get_instance(self._focused_monitor_id, component)
    
    def _on_monitor_focused(self, monitor_name, monitor_id, workspace_id):
        old_focused = self._focused_monitor_id
        self._focused_monitor_id = monitor_id
        
        if old_focused != monitor_id:
            self._handle_notch_focus_switch(old_focused, monitor_id)
    
    def _handle_notch_focus_switch(self, old_monitor, new_monitor):
        if self.is_notch_open(old_monitor):
            old_module = self.get_current_notch_module(old_monitor)
            self.close_all_notches_except(-1)
            
            if old_module:
                new_instances = self.get_monitor_instances(new_monitor)
                notch = new_instances.get('notch')
                if notch and hasattr(notch, 'open_module'):
                    notch.open_module(old_module)
        
        self.notch_focus_changed.emit(old_monitor, new_monitor)


_monitor_manager_instance = None

def get_monitor_manager():
    global _monitor_manager_instance
    if _monitor_manager_instance is None:
        _monitor_manager_instance = MonitorManager()
    return _monitor_manager_instance