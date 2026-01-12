import json, subprocess, threading, time, weakref
from typing import Dict, List, Tuple
from gi.repository import GLib, GObject


class MonitorManager(GObject.GObject):
    __gsignals__ = {
        'monitors-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'focused-monitor-changed': (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if not cls._instance:
                cls._instance = super().__new__(cls)
            return cls._instance

    def __init__(self):
        if hasattr(self, '_init'):
            return
        super().__init__()
        self._init = True
        self._monitors: List[Dict] = []
        self._focused_id = 0
        self._cache_ts = 0.0
        self._instances = {}
        self._notch_states = {}
        self._lock = threading.Lock()
        self._updating = False
        
        self.refresh(async_mode=False)
        GLib.timeout_add_seconds(30, self._timer_callback)

    def _timer_callback(self):
        self.refresh(async_mode=True)
        return True

    def _fetch(self) -> List[Dict]:
        try:
            res = subprocess.run(['hyprctl', 'monitors', '-j'], capture_output=True, text=True, timeout=2)
            if res.returncode == 0:
                return json.loads(res.stdout)
        except Exception:
            pass
        return []

    def refresh(self, async_mode=True):
        if self._updating:
            return
        if not async_mode:
            self._apply_update(self._fetch())
            return

        def _bg():
            data = self._fetch()
            GLib.idle_add(self._apply_update, data)
        
        self._updating = True
        threading.Thread(target=_bg, daemon=True).start()

    def _apply_update(self, data: List[Dict]):
        with self._lock:
            self._updating = False
            if not data:
                return
            
            old_count = len(self._monitors)
            old_focus = self._focused_id
            self._monitors = data
            self._cache_ts = time.monotonic()
            
            # Find focus
            self._focused_id = next((i for i, m in enumerate(data) if m.get('focused')), 0)
            
            if old_count != len(data):
                self.emit('monitors-changed')
            if old_focus != self._focused_id:
                self.emit('focused-monitor-changed', self._focused_id)

    def get_monitors(self) -> List[Dict]:
        if time.monotonic() - self._cache_ts > 30:
            self.refresh(async_mode=True)
        return self._monitors or [{
            'id': 0, 'name': 'def', 'width': 1920, 'height': 1080, 'focused': True, 'activeWorkspace': {'id': 1}
        }]

    def get_focused_monitor_id(self) -> int:
        return self._focused_id

    def register_monitor_instances(self, m_id: int, instances: Dict):
        with self._lock:
            self._instances[m_id] = {k: weakref.ref(v) if v else None for k, v in instances.items()}

    def get_instance(self, m_id: int, i_type: str):
        with self._lock:
            ref_dict = self._instances.get(m_id, {})
            ref = ref_dict.get(i_type)
            obj = ref() if ref else None
            if ref and not obj:
                del ref_dict[i_type]
            return obj

    def set_notch_state(self, m_id: int, is_open: bool, name: str = None):
        self._notch_states[m_id] = {'open': is_open, 'widget': name}

    def close_all_notches_except(self, exc_id: int):
        for m_id, state in self._notch_states.items():
            if m_id != exc_id and state.get('open'):
                inst = self.get_instance(m_id, 'notch')
                if inst and hasattr(inst, 'close_notch'):
                    inst.close_notch()
                state['open'] = False

    def get_workspace_range_for_monitor(self, m_id: int) -> Tuple[int, int]:
        return (m_id * 10 + 1, m_id * 10 + 10)

    def cleanup(self):
        self._monitors.clear()
        self._instances.clear()

def get_monitor_manager() -> MonitorManager:
    return MonitorManager()