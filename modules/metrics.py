from fabric.core.fabricator import Fabricator
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.circularprogressbar import CircularProgressBar
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer
from fabric.widgets.scale import Scale
from fabric.utils import exec_shell_command_async

from gi.repository import GLib, Gio

from services.network import NetworkClient
from services.upower import UPowerManager
import modules.icons as icons

_STAT = '/proc/stat'
_MEM = '/proc/meminfo'
_NET = '/proc/net/dev'
_MT = b'MemTotal:'
_MA = b'MemAvailable:'
_LO = b'lo'


class MetricsProvider:
    __slots__ = ('gpu', 'cpu', 'mem', 'disk', 'temperature', 'upower',
                 'display_device', 'bat_percent', 'bat_charging', 'bat_time',
                 '_gpu_counter', '_prev_idle', '_prev_total', '_gpu_running',
                 '_gpu_type', '_gpu_count', '_temp_path', '_amd_paths', '_intel_base')

    def __init__(self):
        # Замена array('f') на list
        self.gpu = []
        self.disk = [0.0]
        self.cpu = self.mem = self.temperature = 0.0
        self._prev_idle = self._prev_total = 0
        self.upower = UPowerManager()
        self.display_device = self.upower.get_display_device()
        self.bat_percent = self.bat_time = 0.0
        self.bat_charging = None
        self._gpu_running = False
        self._gpu_counter = 0
        self._gpu_type = 0  # 0=none, 1=nvidia, 2=amd, 3=intel
        self._gpu_count = 0
        self._temp_path = None
        self._amd_paths = []
        self._intel_base = None
        self._detect_gpu()
        GLib.timeout_add_seconds(2, self._update)

    def _detect_gpu(self):
        # Температура: os.path.exists -> GLib.file_test
        for p in ('/sys/class/thermal/thermal_zone0/temp', '/sys/class/hwmon/hwmon0/temp1_input'):
            if GLib.file_test(p, GLib.FileTest.EXISTS):
                self._temp_path = p
                break

        # NVIDIA
        try:
            proc = Gio.Subprocess.new(
                ['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
            _, out, _ = proc.communicate_utf8(None)
            if out and out.strip():
                self._gpu_type = 1
                self._gpu_count = len(out.strip().split('\n'))
                self.gpu = [0.0] * self._gpu_count
                return
        except:
            pass

        # AMD - сохраняем пути к файлам
        self._amd_paths = []
        for i in range(10):
            path = f'/sys/class/drm/card{i}/device/gpu_busy_percent'
            if GLib.file_test(path, GLib.FileTest.EXISTS):
                self._amd_paths.append(path)
        if self._amd_paths:
            self._gpu_type = 2
            self._gpu_count = len(self._amd_paths)
            self.gpu = [0.0] * self._gpu_count
            return

        # Intel - сохраняем базовый путь
        for base in ('/sys/class/drm/card0/gt/gt0/rps_', '/sys/class/drm/card0/gt_'):
            if (GLib.file_test(base + 'cur_freq_mhz', GLib.FileTest.EXISTS) and 
                GLib.file_test(base + 'max_freq_mhz', GLib.FileTest.EXISTS)):
                self._intel_base = base
                self._gpu_type = 3
                self._gpu_count = 1
                self.gpu = [0.0]
                return

    def _update(self):
        # CPU
        try:
            with open(_STAT, 'rb') as f:
                p = f.readline().split()
            idle, total = int(p[4]), sum(map(int, p[1:8]))
            di, dt = idle - self._prev_idle, total - self._prev_total
            self._prev_idle, self._prev_total = idle, total
            self.cpu = (1.0 - di / dt) * 100 if dt else 0.0
        except:
            self.cpu = 0.0

        # Memory
        try:
            mt = ma = 0
            with open(_MEM, 'rb') as f:
                for ln in f:
                    if ln[:9] == _MT:
                        mt = int(ln.split()[1])
                    elif ln[:13] == _MA:
                        ma = int(ln.split()[1])
                        break
            self.mem = (1.0 - ma / mt) * 100 if mt else 0.0
        except:
            self.mem = 0.0

        # Disk - замена os.statvfs на Gio
        try:
            file = Gio.File.new_for_path("/")
            info = file.query_filesystem_info(
                f"{Gio.FILE_ATTRIBUTE_FILESYSTEM_SIZE},{Gio.FILE_ATTRIBUTE_FILESYSTEM_FREE}", 
                None
            )
            total = info.get_attribute_uint64(Gio.FILE_ATTRIBUTE_FILESYSTEM_SIZE)
            free = info.get_attribute_uint64(Gio.FILE_ATTRIBUTE_FILESYSTEM_FREE)
            self.disk[0] = (1.0 - free / total) * 100 if total else 0.0
        except:
            self.disk[0] = 0.0

        # Temperature
        if self._temp_path:
            try:
                with open(self._temp_path, 'rb') as f:
                    self.temperature = int(f.read()) * 0.001
            except:
                self.temperature = 0.0

        # GPU
        self._gpu_counter += 1
        if self._gpu_counter >= 2 and not self._gpu_running:
            self._gpu_counter = 0
            self._update_gpu()

        # Battery
        bat = self.upower.get_full_device_information(self.display_device)
        if bat:
            self.bat_percent = bat['Percentage']
            self.bat_charging = bat['State'] == 1
            self.bat_time = bat['TimeToFull'] if self.bat_charging else bat['TimeToEmpty']
        else:
            self.bat_percent = self.bat_time = 0.0
            self.bat_charging = None

        return True

    def _update_gpu(self):
        if self._gpu_type == 1:
            self._update_gpu_nvidia()
        elif self._gpu_type == 2:
            self._update_gpu_amd()
        elif self._gpu_type == 3:
            self._update_gpu_intel()

    def _update_gpu_nvidia(self):
        self._gpu_running = True
        
        # Убран weakref
        def done(proc, res):
            try:
                _, out, _ = proc.communicate_utf8_finish(res)
                if out:
                    lines = out.strip().split('\n')
                    for i, line in enumerate(lines):
                        if i < len(self.gpu):
                            v = line.strip().replace(' ', '')
                            if v:
                                try:
                                    self.gpu[i] = min(100.0, max(0.0, float(v)))
                                except:
                                    self.gpu[i] = 0.0
            except:
                pass
            self._gpu_running = False

        try:
            proc = Gio.Subprocess.new(
                ['nvidia-smi', '--query-gpu=utilization.gpu', '--format=csv,noheader,nounits'],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
            proc.communicate_utf8_async(None, None, done)
        except:
            self._gpu_running = False

    def _update_gpu_amd(self):
        for i, path in enumerate(self._amd_paths):
            try:
                with open(path, 'rb') as f:
                    self.gpu[i] = min(100.0, max(0.0, float(f.read().strip())))
            except:
                pass

    def _update_gpu_intel(self):
        if not self._intel_base:
            return
        try:
            with open(self._intel_base + 'cur_freq_mhz', 'rb') as f:
                cur = int(f.read().strip())
            with open(self._intel_base + 'max_freq_mhz', 'rb') as f:
                mx = int(f.read().strip())
            self.gpu[0] = min(100.0, (cur / mx) * 100.0) if mx else 0.0
        except:
            pass

    def get_metrics(self):
        return self.cpu, self.mem, self.disk, self.gpu, self.temperature

    def get_battery(self):
        return self.bat_percent, self.bat_charging, self.bat_time

    def get_gpu_info(self):
        return [{}] * max(1, self._gpu_count) if self._gpu_type else []


_shared_provider = None


def _get_provider():
    global _shared_provider
    if _shared_provider is None:
        _shared_provider = MetricsProvider()
    return _shared_provider


class SingularMetric:
    __slots__ = ('usage', 'label', 'box')

    def __init__(self, id, name, icon):
        self.usage = Scale(name=f"{id}-usage", value=0.25, orientation='v', inverted=True, v_align='fill', v_expand=True)
        self.label = Label(name=f"{id}-label", markup=icon)
        self.box = Box(name=f"{id}-box", orientation='v', spacing=8, children=[self.usage, self.label])
        self.box.set_tooltip_markup(f"{icon} {name}")


class SingularMetricSmall:
    __slots__ = ('name_markup', 'icon_markup', 'is_temp', 'icon', 'circle', 'level', 'revealer', 'box')

    def __init__(self, id, name, icon, is_temp=False):
        self.name_markup = name
        self.icon_markup = icon
        self.is_temp = is_temp
        self.icon = Label(name="metrics-icon", markup=icon)
        self.circle = CircularProgressBar(
            name="metrics-circle", value=0, size=28, line_width=2,
            start_angle=150, end_angle=390, style_classes=id, child=self.icon)
        self.level = Label(name="metrics-level", style_classes=id, label="0°C" if is_temp else "0%")
        self.revealer = Revealer(
            name=f"metrics-{id}-revealer", transition_duration=250,
            transition_type="slide-left", child=self.level, child_revealed=False)
        self.box = Box(name=f"metrics-{id}-box", orientation="h", spacing=0, children=[self.circle, self.revealer])

    def markup(self):
        return f"{self.icon_markup} {self.name_markup}"


class Metrics(Box):
    __slots__ = ('temp', 'disk', 'ram', 'cpu', 'gpu')

    def __init__(self, **kwargs):
        super().__init__(name="metrics", spacing=8, h_align="center", v_align="fill", visible=True, all_visible=True)
        provider = _get_provider()
        self.temp = SingularMetric("temp", "ТЕМП", icons.temp)
        self.disk = [SingularMetric("disk", "ДИСК", icons.disk)]
        self.ram = SingularMetric("ram", "ОЗУ", icons.memory)
        self.cpu = SingularMetric("cpu", "ЦП", icons.cpu)

        gpu_info = provider.get_gpu_info()
        self.gpu = [SingularMetric("gpu", "GPU", icons.gpu) for _ in gpu_info] if gpu_info else []

        for m in [self.temp] + self.disk + [self.ram, self.cpu] + self.gpu:
            m.usage.set_sensitive(False)
            self.add(m.box)

        # Убран weakref
        def update():
            if not self.get_parent():
                return False
            cpu, mem, disks, gpus, temp = provider.get_metrics()
            self.temp.usage.value = min(temp / 150.0, 1.0)
            self.ram.usage.value = mem * 0.01
            self.cpu.usage.value = cpu * 0.01
            for i, d in enumerate(self.disk):
                if i < len(disks):
                    d.usage.value = disks[i] * 0.01
            for i, g in enumerate(self.gpu):
                if i < len(gpus):
                    g.usage.value = gpus[i] * 0.01
            return True

        GLib.timeout_add_seconds(2, update)


class MetricsSmall(Button):
    __slots__ = ('temp', 'cpu', 'ram', 'disk', 'gpu', 'hide_timer', 'hover')

    def __init__(self, **kwargs):
        super().__init__(name="metrics-small", **kwargs)
        provider = _get_provider()
        box = Box(spacing=0, orientation="h", visible=True, all_visible=True)
        self.temp = SingularMetricSmall("temp", "ТЕМП", icons.temp, True)
        self.disk = [SingularMetricSmall("disk", "ДИСК", icons.disk)]
        self.cpu = SingularMetricSmall("cpu", "ЦП", icons.cpu)
        self.ram = SingularMetricSmall("ram", "ОЗУ", icons.memory)

        gpu_info = provider.get_gpu_info()
        self.gpu = [SingularMetricSmall("gpu", "GPU", icons.gpu) for _ in gpu_info] if gpu_info else []

        for w in [self.temp] + self.disk + [self.ram, self.cpu] + self.gpu:
            box.add(w.box)
            box.add(Box(name="metrics-sep"))

        self.add(box)
        self.hide_timer = None
        self.hover = 0
        self.connect("enter-notify-event", self._enter)
        self.connect("leave-notify-event", self._leave)

        # Убран weakref
        def update():
            if not self.get_parent():
                return False
            cpu, mem, disks, gpus, temp = provider.get_metrics()
            self.temp.circle.set_value(min(temp / 150.0, 1.0))
            self.temp.level.set_label(f"{round(temp)}°C")
            self.cpu.circle.set_value(cpu * 0.01)
            self.cpu.level.set_label(f"{int(cpu)}%")
            self.ram.circle.set_value(mem * 0.01)
            self.ram.level.set_label(f"{int(mem)}%")
            for i, d in enumerate(self.disk):
                if i < len(disks):
                    d.circle.set_value(disks[i] * 0.01)
                    d.level.set_label(f"{int(disks[i])}%")
            for i, g in enumerate(self.gpu):
                if i < len(gpus):
                    g.circle.set_value(gpus[i] * 0.01)
                    g.level.set_label(f"{int(gpus[i])}%")
            self.set_tooltip_markup(" - ".join(m.markup() for m in [self.temp] + self.disk + [self.ram, self.cpu] + self.gpu))
            return True

        GLib.timeout_add_seconds(2, update)

    def _all(self):
        return [self.temp] + self.disk + [self.ram, self.cpu] + self.gpu

    def _enter(self, *_):
        self.hover += 1
        if self.hide_timer:
            GLib.source_remove(self.hide_timer)
            self.hide_timer = None
        for m in self._all():
            m.revealer.set_reveal_child(True)

    def _leave(self, *_):
        self.hover = max(0, self.hover - 1)
        if self.hover:
            return
        if self.hide_timer:
            GLib.source_remove(self.hide_timer)
        
        # Убран weakref
        def hide():
            # Проверка, жив ли виджет
            try:
                if self.get_parent():
                    for m in self._all():
                        m.revealer.set_reveal_child(False)
                    self.hide_timer = None
            except Exception:
                pass
            return False

        self.hide_timer = GLib.timeout_add(500, hide)


class BatteryButton(Button):
    __slots__ = ('bat_icon', 'bat_circle', 'bat_level', 'bat_revealer', 'batt_fabricator',
                 '_on_battery_changed')  # добавляем callback

    def __init__(self, on_battery_changed=None, **kwargs):
        super().__init__(name="metrics-small", **kwargs)
        self._on_battery_changed = on_battery_changed  # callback для уведомления Battery
        
        self.bat_icon = Label(name="metrics-icon", markup=icons.battery)
        self.bat_circle = CircularProgressBar(
            name="metrics-circle", value=0, size=28, line_width=2,
            start_angle=150, end_angle=390, style_classes="bat", child=self.bat_icon)
        self.bat_level = Label(name="metrics-level", style_classes="bat", label="100%")
        self.bat_revealer = Revealer(
            name="metrics-bat-revealer", transition_duration=250,
            transition_type="slide-left", child=self.bat_level)
        self.add(Box(name="metrics-bat-box", orientation="h", spacing=0, children=[self.bat_circle, self.bat_revealer]))

        provider = _get_provider()
        self.batt_fabricator = Fabricator(
            poll_from=lambda _: provider.get_battery(),
            interval=1000, stream=False, default_value=0)
        self.batt_fabricator.connect("changed", self._update_bat)
        GLib.idle_add(self._update_bat, None, provider.get_battery())

    def _update_bat(self, _, data):
        val, charging, bat_time = data
        pct = int(val)
        low = val <= 30
        self.set_visible(val != 0)
        self.bat_circle.set_value(val * 0.01)
        self.bat_level.set_label(f"{pct}%")
        color = '#ffa500' if low else 'var(--green)'
        self.bat_circle.set_style(f"border: 3px solid {color};")
        self.bat_icon.set_style(f"color: {'#ffa500' if low else '#d3d3d3'};")
        self.bat_circle.add_style_class("battery-low" if low else "battery-normal")
        self.bat_circle.remove_style_class("battery-normal" if low else "battery-low")

        if bat_time < 60:
            t = f"{int(bat_time)}сек"
        elif bat_time < 3600:
            t = f"{int(bat_time / 60)}мин"
        else:
            t = f"{int(bat_time / 3600)}ч"

        if pct == 100:
            self.bat_icon.set_markup(icons.battery)
            tip = f"{icons.bat_full} Полностью заряжено" + ("" if charging else f" - осталось {t}")
        elif charging:
            self.bat_icon.set_markup(icons.charging)
            tip = f"{icons.bat_charging} Заряжается - осталось {t}"
        elif low:
            self.bat_icon.set_markup(icons.alert)
            tip = f"{icons.bat_low} Низкий заряд - осталось {t}"
        else:
            self.bat_icon.set_markup(icons.discharging)
            tip = f"{icons.bat_discharging} Разряжается - осталось {t}"
        self.set_tooltip_markup(tip)
        
        # Уведомляем Battery об изменении состояния батареи
        if self._on_battery_changed:
            self._on_battery_changed(val, charging)


class Battery(Box):
    __slots__ = ('battery_button', 'power_modes_box', 'power_modes_revealer',
                 'bat_save', 'bat_balanced', 'bat_perf', 'current_mode', 'hide_timer',
                 'auto_mode_enabled', 'manual_override', 'last_charging_state')

    # Пороговые значения для автопереключения
    LOW_BATTERY_THRESHOLD = 30
    
    def __init__(self, **kwargs):
        super().__init__(orientation="h", spacing=0, **kwargs)
        self.set_name("battery-container")
        
        # Флаги для автоматического управления
        self.auto_mode_enabled = True  # включено ли автопереключение
        self.manual_override = False   # было ли ручное переключение (временно отключает авто)
        self.last_charging_state = None  # для отслеживания изменения состояния зарядки
        
        # Создаём кнопку батареи с callback'ом
        self.battery_button = BatteryButton(on_battery_changed=self._on_battery_state_changed)
        
        self.power_modes_box = Box(name="power-mode-switcher", orientation="h", spacing=2)
        self.power_modes_revealer = Revealer(
            name="metrics-power-modes-revealer", transition_duration=250,
            transition_type="slide-left", child=self.power_modes_box, child_revealed=False)
        self.add(self.battery_button)
        self.add(self.power_modes_revealer)
        self.bat_save = self.bat_balanced = self.bat_perf = None
        self.current_mode = "balanced"
        self.hide_timer = None
        self._init_power_modes()

        # Убран weakref
        def get_mode():
            if not self.get_parent():
                return False

            def done(proc, res):
                # Проверка self не через weakref
                try:
                    # Если объект удален, доступ к атрибутам вызовет ошибку
                    # или метод get_parent вернет None
                    if not self.get_parent():
                        return
                    
                    _, out, _ = proc.communicate_utf8_finish(res)
                    if out and (m := out.strip()) in ("power-saver", "balanced", "performance"):
                        self.current_mode = m
                        self._update_styles()
                except:
                    pass

            try:
                proc = Gio.Subprocess.new(
                    ['powerprofilesctl', 'get'],
                    Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
                proc.communicate_utf8_async(None, None, done)
            except:
                pass
            return True

        GLib.timeout_add(2000, get_mode)

        for w in [self, self.battery_button, self.power_modes_box]:
            w.connect("enter-notify-event", self._enter)
            w.connect("leave-notify-event", self._leave)

    def _on_battery_state_changed(self, battery_level, is_charging):
        """
        Обработчик изменения состояния батареи.
        Логика:
        - На зарядке → производительный режим
        - Не на зарядке и заряд > 30% → сбалансированный
        - Не на зарядке и заряд <= 30% → энергосбережение
        """
        if not self.auto_mode_enabled:
            return
            
        # Определяем изменилось ли состояние зарядки
        charging_state_changed = (self.last_charging_state is not None and 
                                self.last_charging_state != is_charging)
        self.last_charging_state = is_charging
        
        # Если состояние зарядки изменилось - сбрасываем ручное переопределение
        if charging_state_changed:
            self.manual_override = False
        
        # Если было ручное переключение - не меняем автоматически
        if self.manual_override:
            return
            
        # Определяем целевой режим
        if is_charging:
            # На зарядке → производительный режим
            target_mode = "performance" if self.bat_perf else None
        elif battery_level <= self.LOW_BATTERY_THRESHOLD:
            # Не на зарядке, заряд <= 30% → энергосбережение
            target_mode = "power-saver" if self.bat_save else None
        else:
            # Не на зарядке, заряд > 30% → сбалансированный
            target_mode = "balanced" if self.bat_balanced else None
        
        # Применяем если режим отличается от текущего
        if target_mode and target_mode != self.current_mode:
            self._set_mode_auto(target_mode)

    def _init_power_modes(self):
        profiles = ""
        try:
            proc = Gio.Subprocess.new(
                ['powerprofilesctl', 'list'],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
            _, profiles, _ = proc.communicate_utf8(None)
        except:
            pass

        if "power-saver" in profiles:
            self.bat_save = Button(
                name="battery-save",
                child=Label(name="battery-save-label", markup=icons.power_saving),
                on_clicked=lambda *_: self._set_mode("power-saver"),
                tooltip_text="Энергосбережение")
            self.power_modes_box.add(self.bat_save)
            self.bat_save.connect("enter-notify-event", self._enter)
            self.bat_save.connect("leave-notify-event", self._leave)

        if "balanced" in profiles:
            self.bat_balanced = Button(
                name="battery-balanced",
                child=Label(name="battery-balanced-label", markup=icons.power_balanced),
                on_clicked=lambda *_: self._set_mode("balanced"),
                tooltip_text="Сбалансированный")
            self.power_modes_box.add(self.bat_balanced)
            self.bat_balanced.connect("enter-notify-event", self._enter)
            self.bat_balanced.connect("leave-notify-event", self._leave)

        if "performance" in profiles:
            self.bat_perf = Button(
                name="battery-performance",
                child=Label(name="battery-performance-label", markup=icons.power_performance),
                on_clicked=lambda *_: self._set_mode("performance"),
                tooltip_text="Производительный")
            self.power_modes_box.add(self.bat_perf)
            self.bat_perf.connect("enter-notify-event", self._enter)
            self.bat_perf.connect("leave-notify-event", self._leave)

        self._update_styles()

    def _enter(self, *_):
        if self.hide_timer:
            GLib.source_remove(self.hide_timer)
            self.hide_timer = None
        self.battery_button.bat_revealer.set_reveal_child(True)
        self.power_modes_revealer.set_reveal_child(True)
        return False

    def _leave(self, *_):
        if self.hide_timer:
            GLib.source_remove(self.hide_timer)
        
        # Убран weakref
        def hide():
            try:
                if self.get_parent():
                    self.battery_button.bat_revealer.set_reveal_child(False)
                    self.power_modes_revealer.set_reveal_child(False)
                    self.hide_timer = None
            except:
                pass
            return False

        self.hide_timer = GLib.timeout_add(300, hide)
        return False

    def _set_mode(self, mode):
        """Ручное переключение режима пользователем"""
        # Помечаем что было ручное переключение
        self.manual_override = True
        self._apply_mode(mode)
        
    def _set_mode_auto(self, mode):
        """Автоматическое переключение режима"""
        self._apply_mode(mode, is_auto=True)
        
    def _apply_mode(self, mode, is_auto=False):
        """Применяет режим питания"""
        exec_shell_command_async(f"powerprofilesctl set {mode}")
        self.current_mode = mode
        self._update_styles()

    def _update_styles(self):
        """Обновляет стили кнопок режимов"""
        for btn in (self.bat_save, self.bat_balanced, self.bat_perf):
            if btn:
                btn.remove_style_class("active")
        btn = {
            "power-saver": self.bat_save, 
            "balanced": self.bat_balanced, 
            "performance": self.bat_perf
        }.get(self.current_mode)
        if btn:
            btn.add_style_class("active")
            
    def set_auto_mode(self, enabled):
        """Включает/выключает автоматическое управление режимами"""
        self.auto_mode_enabled = enabled
        if enabled:
            self.manual_override = False
            # Принудительно проверяем текущее состояние
            battery_data = _get_provider().get_battery()
            self._on_battery_state_changed(battery_data[0], battery_data[1])
            
    def reset_manual_override(self):
        """Сбрасывает флаг ручного переопределения"""
        self.manual_override = False


class NetworkApplet(Button):
    __slots__ = ('network_client', 'hover', 'dl_label', 'ul_label', 'wifi_label',
                 'dl_rev', 'ul_rev', 'last_recv', 'last_sent', 'last_time')

    def __init__(self, **kwargs):
        super().__init__(name="button-bar", **kwargs)
        self.network_client = NetworkClient()
        self.hover = False
        self.dl_label = Label(name="download-label", markup="0 B/s")
        self.ul_label = Label(name="upload-label", markup="0 B/s")
        self.wifi_label = Label(name="network-icon-label", markup=icons.world_off)
        self.dl_rev = Revealer(
            child=Box(children=[Label(name="download-icon-label", markup=icons.download), self.dl_label]),
            transition_type="slide-right", child_revealed=False)
        self.ul_rev = Revealer(
            child=Box(children=[self.ul_label, Label(name="upload-icon-label", markup=icons.upload)]),
            transition_type="slide-left", child_revealed=False)
        self.add(Box(orientation="h", children=[self.ul_rev, self.wifi_label, self.dl_rev]))
        self.last_recv = self.last_sent = 0
        self.last_time = GLib.get_monotonic_time()
        self._read_init()
        self.connect("enter-notify-event", self._enter)
        self.connect("leave-notify-event", self._leave)

        # Убран weakref
        def update():
            if not self.get_parent():
                return False
            now = GLib.get_monotonic_time()
            dt = (now - self.last_time) * 1e-6
            if dt <= 0:
                return True
            recv = sent = 0
            try:
                with open(_NET, 'rb') as f:
                    for ln in f.readlines()[2:]:
                        p = ln.split()
                        if not p[0].rstrip(b':').endswith(_LO):
                            recv += int(p[1])
                            sent += int(p[9])
            except:
                pass
            self.dl_label.set_markup(self._fmt((recv - self.last_recv) / dt))
            self.ul_label.set_markup(self._fmt((sent - self.last_sent) / dt))
            self.last_recv, self.last_sent, self.last_time = recv, sent, now
            self._update_wifi()
            return True

        GLib.timeout_add(1000, update)

    def _read_init(self):
        try:
            with open(_NET, 'rb') as f:
                for ln in f.readlines()[2:]:
                    p = ln.split()
                    if not p[0].rstrip(b':').endswith(_LO):
                        self.last_recv += int(p[1])
                        self.last_sent += int(p[9])
        except:
            pass

    def _enter(self, *_):
        self.hover = True
        self.dl_rev.set_reveal_child(True)
        self.ul_rev.set_reveal_child(True)

    def _leave(self, *_):
        self.hover = False
        self.dl_rev.set_reveal_child(False)
        self.ul_rev.set_reveal_child(False)

    def _update_wifi(self):
        nc = self.network_client
        if nc.ethernet_device and nc.ethernet_device.internet in ("activated", "activating"):
            self.wifi_label.set_markup(icons.world)
            self.set_tooltip_text("Ethernet")
            return
        wd = nc.wifi_device
        if not (wd and getattr(wd, "enabled", False)):
            self.wifi_label.set_markup(icons.world_off)
            self.set_tooltip_text("Disconnected")
            return
        ssid = getattr(wd, "ssid", None)
        if not ssid or ssid in ("Disconnected", "Выключено", "Не подключено"):
            self.wifi_label.set_markup(icons.world_off)
            self.set_tooltip_text("Disconnected")
            return
        s = wd.strength
        self.wifi_label.set_markup(
            icons.wifi_3 if s >= 75 else icons.wifi_2 if s >= 50 else icons.wifi_1 if s >= 25 else icons.wifi_0)
        self.set_tooltip_text(ssid)

    @staticmethod
    def _fmt(sp):
        if sp < 1024:
            return f"{sp:.0f} B/s"
        elif sp < 1048576:
            return f"{sp * 0.0009765625:.1f} KB/s"
        else:
            return f"{sp * 9.5367431640625e-07:.1f} MB/s"