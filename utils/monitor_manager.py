"""Менеджер управления несколькими мониторами."""
import json
import subprocess
from typing import Dict, List, Optional
from gi.repository import GLib

class MonitorManager:
    """Управление мониторами и распределение компонентов."""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance
    
    def _init(self):
        """Инициализация менеджера."""
        self.monitors = []
        self.monitor_instances = {}
        self._focused_monitor_id = 0
        self._notch_states = {}
        self._update_timer_id = None
        
        # Загрузка информации о мониторах
        self.refresh_monitors()
        
        # Периодическое обновление
        self._update_timer_id = GLib.timeout_add_seconds(5, self._periodic_update)
    
    def get_monitors(self) -> List[Dict]:
        """Получение списка мониторов."""
        if not self.monitors:
            self.refresh_monitors()
        return self.monitors
    
    def refresh_monitors(self):
        """Обновление информации о мониторах."""
        try:
            result = subprocess.run(
                ["hyprctl", "monitors", "-j"],
                capture_output=True,
                text=True,
                timeout=1
            )
            if result.returncode == 0:
                self.monitors = json.loads(result.stdout)
                
                # Определение основного монитора
                for i, monitor in enumerate(self.monitors):
                    if monitor.get('focused', False):
                        self._focused_monitor_id = i
                        break
        except (subprocess.TimeoutExpired, json.JSONDecodeError):
            # Fallback для случая без Hyprland
            self.monitors = [{'id': 0, 'name': 'default', 'width': 1920, 'height': 1080}]
    
    def get_focused_monitor_id(self) -> int:
        """Получение ID активного монитора."""
        return self._focused_monitor_id
    
    def set_focused_monitor(self, monitor_id: int):
        """Установка активного монитора."""
        if 0 <= monitor_id < len(self.monitors):
            self._focused_monitor_id = monitor_id
    
    def register_monitor_instances(self, monitor_id: int, instances: Dict):
        """Регистрация компонентов для монитора."""
        self.monitor_instances[monitor_id] = instances
    
    def get_instance(self, monitor_id: int, instance_type: str):
        """Получение компонента монитора по типу."""
        instances = self.monitor_instances.get(monitor_id, {})
        return instances.get(instance_type)
    
    def set_notch_state(self, monitor_id: int, is_open: bool, widget_name: str = None):
        """Установка состояния выреза."""
        self._notch_states[monitor_id] = {
            'open': is_open,
            'widget': widget_name
        }
    
    def get_notch_state(self, monitor_id: int) -> Optional[Dict]:
        """Получение состояния выреза."""
        return self._notch_states.get(monitor_id)
    
    def close_all_notches_except(self, except_monitor_id: int):
        """Закрытие всех вырезов кроме указанного."""
        for monitor_id in list(self._notch_states.keys()):
            if monitor_id != except_monitor_id:
                self._notch_states[monitor_id]['open'] = False
                
                # Закрытие выреза через его экземпляр
                notch_instance = self.get_instance(monitor_id, 'notch')
                if notch_instance and hasattr(notch_instance, 'close_notch'):
                    notch_instance.close_notch()
    
    def get_workspace_range_for_monitor(self, monitor_id: int) -> tuple:
        """Получение диапазона рабочих столов для монитора."""
        # Простая реализация - по 9 рабочих столов на монитор
        start = monitor_id * 9 + 1
        end = start + 8
        return (start, end)
    
    def _periodic_update(self):
        """Периодическое обновление состояния."""
        self.refresh_monitors()
        return True  # Продолжить таймер
    
    def cleanup(self):
        """Очистка ресурсов."""
        if hasattr(self, '_update_timer_id') and self._update_timer_id:
            GLib.source_remove(self._update_timer_id)
            self._update_timer_id = None
        
        if hasattr(self, 'monitors'):
            self.monitors.clear()
        if hasattr(self, 'monitor_instances'):
            self.monitor_instances.clear()
        if hasattr(self, '_notch_states'):
            self._notch_states.clear()

def get_monitor_manager():
    """Фабрика для получения менеджера мониторов."""
    return MonitorManager()