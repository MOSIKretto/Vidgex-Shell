from fabric.core.fabricator import Fabricator
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.circularprogressbar import CircularProgressBar
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer
from fabric.widgets.scale import Scale
from fabric.utils import exec_shell_command_async

from gi.repository import GLib
import subprocess
import time
import psutil
import threading
import json

from services.network import NetworkClient
from services.upower import UPowerManager
import modules.icons as icons


class MetricsProvider:
    def __init__(self):
        self.gpu = []
        self.cpu = 0.0
        self.mem = 0.0
        self.disk = []
        self.temperature = 0.0

        self.upower = UPowerManager()
        self.display_device = self.upower.get_display_device()
        self.bat_percent = 0.0
        self.bat_charging = None
        self.bat_time = 0

        self._gpu_update_running = False
        self._gpu_update_counter = 0
        self._update_timer_id = None
        self._should_stop = False
        self._gpu_thread = None

        # Remove timer-based updates - we'll update on demand only

    def destroy(self):
        self._should_stop = True
        
        if self._update_timer_id:
            GLib.source_remove(self._update_timer_id)

            self._update_timer_id = None
        
        self._gpu_update_running = False
        if hasattr(self, '_gpu_thread') and self._gpu_thread:
            if self._gpu_thread.is_alive():
                self._gpu_thread.join(timeout=3.0)

            self._gpu_thread = None
        
        if hasattr(self, 'upower') and self.upower:
            if hasattr(self.upower, 'destroy'):
                self.upower.destroy()

            self.upower = None

    def _update(self):
        if self._should_stop:
            return False
            
        self.cpu = psutil.cpu_percent(interval=0)
        self.mem = psutil.virtual_memory().percent
        self.disk = [psutil.disk_usage(path).percent for path in ["/"]]
        
        try:
            temps = psutil.sensors_temperatures()
            if temps and 'coretemp' in temps:
                core_temps = [temp.current for temp in temps['coretemp'] if hasattr(temp, 'current')]
                if core_temps:
                    self.temperature = max(core_temps)
            elif temps:
                for sensor_name, entries in temps.items():
                    if entries and hasattr(entries[0], 'current'):
                        self.temperature = entries[0].current
                        break
        except Exception:
            self.temperature = 0.0

        self._gpu_update_counter += 1
        if self._gpu_update_counter >= 5:
            self._gpu_update_counter = 0
            if not self._gpu_update_running and not self._should_stop:
                self._update_gpu()

        battery = self.upower.get_full_device_information(self.display_device)
        if battery is None:
            self.bat_percent = 0.0
            self.bat_charging = None
            self.bat_time = 0
        else:
            self.bat_percent = battery['Percentage']
            self.bat_charging = battery['State'] == 1
            self.bat_time = battery['TimeToFull'] if self.bat_charging else battery['TimeToEmpty']

        return True

    def _update_gpu(self):
        if self._should_stop:
            return
            
        self._gpu_update_running = True
        
        def worker():
            output = None
            try:
                result = subprocess.check_output(["nvtop", "-s"], text=True, timeout=10)
                output = result
            except subprocess.TimeoutExpired:
                # Handle timeout gracefully
                pass

            if not self._should_stop:
                GLib.idle_add(self._process_gpu_output, output)
        
        self._gpu_thread = threading.Thread(target=worker, daemon=True)
        self._gpu_thread.start()

    def _process_gpu_output(self, output):
        self._gpu_update_running = False
        
        try:
            if output:
                info = json.loads(output)
                self.gpu = [
                    int(v["gpu_util"].strip("%")) if v["gpu_util"] else 0
                    for v in info
                ]
            else:
                self.gpu = []
        except Exception:
            self.gpu = []

        return False

    def get_metrics(self):
        # Update metrics only when needed instead of constantly polling
        self._update()
        return (self.cpu, self.mem, self.disk, self.gpu, self.temperature)

    def get_battery(self):
        return (self.bat_percent, self.bat_charging, self.bat_time)

    def get_gpu_info(self):
        try:
            result = subprocess.check_output(["nvtop", "-s"], text=True, timeout=5)
            return json.loads(result)
        except:
            return []


shared_provider = MetricsProvider()


class SingularMetric:
    def __init__(self, id, name, icon):
        self.usage = Scale(
            name=f"{id}-usage",
            value=0.25,
            orientation='v',
            inverted=True,
            v_align='fill',
            v_expand=True,
        )

        self.label = Label(
            name=f"{id}-label",
            markup=icon,
        )

        self.box = Box(
            name=f"{id}-box",
            orientation='v',
            spacing=8,
            children=[
                self.usage,
                self.label,
            ]
        )

        self.box.set_tooltip_markup(f"{icon} {name}")


class Metrics(Box):
    def __init__(self, **kwargs):
        super().__init__(
            name="metrics",
            spacing=8,
            h_align="center",
            v_align="fill",
            visible=True,
            all_visible=True,
        )

        visible = {'temp': True, 'disk': True, 'ram': True, 'cpu': True, 'gpu': True}
        
        self.temp = SingularMetric("temp", "ТЕМП", icons.temp) if visible.get('temp', True) else None
        disks = [SingularMetric("disk", "ДИСК", icons.disk) for path in ["/"]] if visible.get('disk', True) else []
        self.ram = SingularMetric("ram", "ОЗУ", icons.memory) if visible.get('ram', True) else None
        self.cpu = SingularMetric("cpu", "ЦП", icons.cpu) if visible.get('cpu', True) else None
        gpus = [SingularMetric(f"gpu", "GPU", icons.gpu) for v in shared_provider.get_gpu_info()] if visible.get('gpu', True) else []

        self.disk = disks
        self.gpu = gpus

        self.scales = []
        if self.temp: self.scales.append(self.temp.box)
        if self.disk: self.scales.extend([v.box for v in self.disk])
        if self.ram: self.scales.append(self.ram.box)
        if self.cpu: self.scales.append(self.cpu.box)
        if self.gpu: self.scales.extend([v.box for v in self.gpu])

        for metric in [self.temp, self.cpu, self.ram] + self.disk + self.gpu:
            if metric and hasattr(metric, 'usage'):
                metric.usage.set_sensitive(False)

        for x in self.scales:
            self.add(x)

        # Removed timer-based updates - update only when necessary

    def destroy(self):
        # No timer to remove since we removed it
        super().destroy()

    def update_status(self):
        cpu, mem, disks, gpus, temperature = shared_provider.get_metrics()

        if self.temp:
            self.temp.usage.value = min(temperature / 150.0, 1.0)
        for i, disk in enumerate(self.disk):
            if i < len(disks):
                disk.usage.value = disks[i] / 100.0
        if self.ram:
            self.ram.usage.value = mem / 100.0
        if self.cpu:
            self.cpu.usage.value = cpu / 100.0
        for i, gpu in enumerate(self.gpu):
            if i < len(gpus):
                gpu.usage.value = gpus[i] / 100.0
        return True


class SingularMetricSmall:
    def __init__(self, id, name, icon, is_temp=False):
        self.name_markup = name
        self.icon_markup = icon
        self.is_temp = is_temp

        self.icon = Label(name="metrics-icon", markup=icon)
        self.circle = CircularProgressBar(
            name="metrics-circle",
            value=0,
            size=28,
            line_width=2,
            start_angle=150,
            end_angle=390,
            style_classes=id,
            child=self.icon,
        )

        self.level = Label(name="metrics-level", style_classes=id, label="0°C" if is_temp else "0%")
        self.revealer = Revealer(
            name=f"metrics-{id}-revealer",
            transition_duration=250,
            transition_type="slide-left",
            child=self.level,
            child_revealed=False,
        )

        self.box = Box(
            name=f"metrics-{id}-box",
            orientation="h",
            spacing=0,
            children=[self.circle, self.revealer],
        )

    def markup(self):
        return f"{self.icon_markup} {self.name_markup}"
    

class MetricsSmall(Button):
    def __init__(self, **kwargs):
        super().__init__(name="metrics-small", **kwargs)

        main_box = Box(
            spacing=0,
            orientation="h",
            visible=True,
            all_visible=True,
        )

        visible = {'temp': True, 'cpu': True, 'ram': True, 'disk': True, 'gpu': True}
        
        self.temp = SingularMetricSmall("temp", "ТЕМП", icons.temp, is_temp=True) if visible.get('temp', True) else None
        
        disks = [SingularMetricSmall("disk", "ДИСК", icons.disk) for path in ["/"]] if visible.get('disk', True) else []

        gpu_info = shared_provider.get_gpu_info()
        gpus = [SingularMetricSmall("gpu", "GPU", icons.gpu) for v in gpu_info] if visible.get('gpu', True) else []

        self.cpu = SingularMetricSmall("cpu", "ЦП", icons.cpu) if visible.get('cpu', True) else None
        self.ram = SingularMetricSmall("ram", "ОЗУ", icons.memory) if visible.get('ram', True) else None
        self.disk = disks
        self.gpu = gpus

        if self.temp:
            main_box.add(self.temp.box)
            main_box.add(Box(name="metrics-sep"))
        for disk in self.disk:
            main_box.add(disk.box)
            main_box.add(Box(name="metrics-sep"))
        if self.ram:
            main_box.add(self.ram.box)
            main_box.add(Box(name="metrics-sep"))
        if self.cpu:
            main_box.add(self.cpu.box)
        for gpu in self.gpu:
            main_box.add(gpu.box)
            main_box.add(Box(name="metrics-sep"))

        self.add(main_box)

        # Removed timer-based updates - update only on demand

        self.hide_timer = None
        self.hover_counter = 0
        self._enter_handler = self.connect("enter-notify-event", self.on_mouse_enter)
        self._leave_handler = self.connect("leave-notify-event", self.on_mouse_leave)
    
    def destroy(self):
        # No timer to remove since we removed it
        if self.hide_timer:
            GLib.source_remove(self.hide_timer)
            self.hide_timer = None
        if hasattr(self, '_enter_handler'):
            self.disconnect(self._enter_handler)

        if hasattr(self, '_leave_handler'):
            self.disconnect(self._leave_handler)

        super().destroy()

    def _format_percentage(self, value):
        return f"{value}%"
    
    def _format_temperature(self, value):
        rounded_value = round(value)
        return f"{rounded_value}°C"

    def on_mouse_enter(self, widget, event):
        self.hover_counter += 1
        if self.hide_timer:
            GLib.source_remove(self.hide_timer)
            self.hide_timer = None

        # Update metrics when mouse enters to ensure fresh data
        self.update_metrics_on_demand()
        
        if self.temp: self.temp.revealer.set_reveal_child(True)
        if self.cpu: self.cpu.revealer.set_reveal_child(True)
        if self.ram: self.ram.revealer.set_reveal_child(True)
        for disk in self.disk: disk.revealer.set_reveal_child(True)
        for gpu in self.gpu: gpu.revealer.set_reveal_child(True)
        return False

    def on_mouse_leave(self, widget, event):
        if self.hover_counter > 0:
            self.hover_counter -= 1
        if self.hover_counter == 0:
            if self.hide_timer:
                GLib.source_remove(self.hide_timer)
            self.hide_timer = GLib.timeout_add(500, self.hide_revealer)
        return False

    def hide_revealer(self):
        if self.temp: self.temp.revealer.set_reveal_child(False)
        if self.cpu: self.cpu.revealer.set_reveal_child(False)
        if self.ram: self.ram.revealer.set_reveal_child(False)
        for disk in self.disk: disk.revealer.set_reveal_child(False)
        for gpu in self.gpu: gpu.revealer.set_reveal_child(False)
        self.hide_timer = None
        return False

    def update_metrics_on_demand(self):
        """Update metrics only when needed"""
        cpu, mem, disks, gpus, temperature = shared_provider.get_metrics()

        if self.temp:
            self.temp.circle.set_value(min(temperature / 150.0, 1.0))
            self.temp.level.set_label(self._format_temperature(temperature))
        
        if self.cpu:
            self.cpu.circle.set_value(cpu / 100.0)
            self.cpu.level.set_label(self._format_percentage(int(cpu)))
        if self.ram:
            self.ram.circle.set_value(mem / 100.0)
            self.ram.level.set_label(self._format_percentage(int(mem)))
        for i, disk in enumerate(self.disk):
            if i < len(disks):
                disk.circle.set_value(disks[i] / 100.0)
                disk.level.set_label(self._format_percentage(int(disks[i])))
        for i, gpu in enumerate(self.gpu):
            if i < len(gpus):
                gpu.circle.set_value(gpus[i] / 100.0)
                gpu.level.set_label(self._format_percentage(int(gpus[i])))

        tooltip_metrics = []
        if self.temp: tooltip_metrics.append(self.temp)
        if self.disk: tooltip_metrics.extend(self.disk)
        if self.ram: tooltip_metrics.append(self.ram)
        if self.cpu: tooltip_metrics.append(self.cpu)
        if self.gpu: tooltip_metrics.extend(self.gpu)
        
        self.set_tooltip_markup(" - ".join([v.markup() for v in tooltip_metrics]))

    def update_metrics(self):
        """Alias for backward compatibility"""
        self.update_metrics_on_demand()
        return True  # Return True to maintain compatibility with timer interface


class BatteryButton(Button):
    def __init__(self, **kwargs):
        super().__init__(name="metrics-small", **kwargs)

        self.bat_icon = Label(name="metrics-icon", markup=icons.battery)
        self.bat_circle = CircularProgressBar(
            name="metrics-circle",
            value=0,
            size=28,
            line_width=2,
            start_angle=150,
            end_angle=390,
            style_classes="bat",
            child=self.bat_icon,
        )
        self.bat_level = Label(name="metrics-level", style_classes="bat", label="100%")

        self.bat_revealer = Revealer(
            name="metrics-bat-revealer",
            transition_duration=250,
            transition_type="slide-left",
            child=self.bat_level,
        )

        self.bat_box = Box(
            name="metrics-bat-box",
            orientation="h",
            spacing=0,
            children=[self.bat_circle, self.bat_revealer],
        )

        self.add(self.bat_box)

        # Use a more efficient approach - update only when needed
        self.update_battery(None, shared_provider.get_battery())

    def destroy(self):
        super().destroy()

    def _format_percentage(self, value):
        return f"{value}%"

    def update_battery(self, sender, battery_data):
        if battery_data:
            value, charging, time = battery_data
        else:
            value, charging, time = shared_provider.get_battery()
            
        if value == 0:
            self.set_visible(False)
        else:
            self.set_visible(True)
            self.bat_circle.set_value(value / 100)
            
        percentage = int(value)
        self.bat_level.set_label(self._format_percentage(percentage))
        
        if percentage <= 30:
            self.bat_circle.set_style("border: 3px solid #ffa500;")
            self.bat_icon.set_style("color: #ffa500;")
            self.bat_circle.add_style_class("battery-low")
            self.bat_circle.remove_style_class("battery-normal")
        else:
            self.bat_circle.set_style("border: 3px solid var(--green);")
            self.bat_icon.set_style("color: #d3d3d3;")
            self.bat_circle.add_style_class("battery-normal")
            self.bat_circle.remove_style_class("battery-low")

        if time < 60:time_status = f"{int(time)}сек"
        elif time < 60 * 60:time_status = f"{int(time / 60)}мин"
        else: time_status = f"{int(time / 60 / 60)}ч"

        if percentage == 100 and not charging:
            self.bat_icon.set_markup(icons.battery)
            charging_status = f"{icons.bat_full} Полностью заряжено - осталось {time_status}"
        elif percentage == 100 and charging:
            self.bat_icon.set_markup(icons.battery)
            charging_status = f"{icons.bat_full} Полностью заряжено"
        elif charging:
            self.bat_icon.set_markup(icons.charging)
            charging_status = f"{icons.bat_charging} Заряжается - осталось {time_status}"
        elif percentage <= 30 and not charging:
            self.bat_icon.set_markup(icons.alert)
            charging_status = f"{icons.bat_low} Низкий заряд - осталось {time_status}"
        elif not charging:
            self.bat_icon.set_markup(icons.discharging)
            charging_status = f"{icons.bat_discharging} Разряжается - осталось {time_status}"
        else:
            self.bat_icon.set_markup(icons.battery)
            charging_status = "Батарея"

        self.set_tooltip_markup(charging_status)


class Battery(Box):
    def __init__(self, **kwargs):
        super().__init__(
            orientation="h",
            spacing=0,
            **kwargs,
        )

        self.set_name("battery-container")
        
        self.battery_button = BatteryButton()
        
        self.power_modes_box = Box(
            name="power-mode-switcher",
            orientation="h",
            spacing=2,
        )
        
        self.power_modes_revealer = Revealer(
            name="metrics-power-modes-revealer",
            transition_duration=250,
            transition_type="slide-left",
            child=self.power_modes_box,
            child_revealed=False,
        )

        self.add(self.battery_button)
        self.add(self.power_modes_revealer)

        self.bat_save = None
        self.bat_balanced = None
        self.bat_perf = None
        self.current_mode = "balanced"

        self._init_power_modes()

        # Removed timer-based updates

        self._event_handlers = []
        self._event_handlers.append(self.connect("enter-notify-event", self.on_container_enter))
        self._event_handlers.append(self.connect("leave-notify-event", self.on_container_leave))
        
        self._event_handlers.append(self.battery_button.connect("enter-notify-event", self.on_container_enter))
        self._event_handlers.append(self.battery_button.connect("leave-notify-event", self.on_container_leave))
        
        self._event_handlers.append(self.power_modes_box.connect("enter-notify-event", self.on_container_enter))
        self._event_handlers.append(self.power_modes_box.connect("leave-notify-event", self.on_container_leave))

        for button in [self.bat_save, self.bat_balanced, self.bat_perf]:
            if button:
                self._event_handlers.append(button.connect("enter-notify-event", self.on_container_enter))
                self._event_handlers.append(button.connect("leave-notify-event", self.on_container_leave))

        self.hide_timer = None
        self.is_mouse_inside = False
    
    def destroy(self):
        # No timer to remove since we removed it
        if self.hide_timer:
            GLib.source_remove(self.hide_timer)
            self.hide_timer = None
            
        for handler_id in self._event_handlers:
            self.disconnect(handler_id)

        self._event_handlers.clear()
        
        if hasattr(self, 'battery_button'):
            self.battery_button.destroy()
            
        super().destroy()

    def _init_power_modes(self):
        try:
            result = subprocess.run(
                ["powerprofilesctl", "list"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
                timeout=1  # Add timeout to prevent hanging
            )
            available_profiles = result.stdout
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            available_profiles = ""

        children = []
        
        if "power-saver" in available_profiles:
            self.bat_save = Button(
                name="battery-save",
                child=Label(name="battery-save-label", markup=icons.power_saving),
                on_clicked=lambda *_: self.set_power_mode("power-saver"),
                tooltip_text="Режим энергосбережения",
            )
            children.append(self.bat_save)

        if "balanced" in available_profiles:
            self.bat_balanced = Button(
                name="battery-balanced",
                child=Label(name="battery-balanced-label", markup=icons.power_balanced),
                on_clicked=lambda *_: self.set_power_mode("balanced"),
                tooltip_text="Сбалансированный режим",
            )
            children.append(self.bat_balanced)

        if "performance" in available_profiles:
            self.bat_perf = Button(
                name="battery-performance",
                child=Label(name="battery-performance-label", markup=icons.power_performance),
                on_clicked=lambda *_: self.set_power_mode("performance"),
                tooltip_text="Производительный режим",
            )
            children.append(self.bat_perf)
            
        for child in children:
            self.power_modes_box.add(child)
            
        self.update_button_styles()

    def on_container_enter(self, widget, event):
        if self.hide_timer:
            GLib.source_remove(self.hide_timer)
            self.hide_timer = None
        
        self.battery_button.bat_revealer.set_reveal_child(True)
        self.power_modes_revealer.set_reveal_child(True)
        
        self.is_mouse_inside = True

    def on_container_leave(self, widget, event):
        if self.hide_timer:
            GLib.source_remove(self.hide_timer)
        
        self.hide_timer = GLib.timeout_add(300, self.hide_revealers)

    def hide_revealers(self):
        self.battery_button.bat_revealer.set_reveal_child(False)
        self.power_modes_revealer.set_reveal_child(False)
        self.hide_timer = None
        self.is_mouse_inside = False
        return False

    def get_current_power_mode(self):
        """Get current power mode on demand"""
        try:
            result = subprocess.run(
                ["powerprofilesctl", "get"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
                timeout=1  # Add timeout to prevent hanging
            )

            output = result.stdout.strip()
            if output in ["power-saver", "balanced", "performance"]: self.current_mode = output
            else: self.current_mode = "balanced"

            self.update_button_styles()

        except Exception:
            self.current_mode = "balanced"
        
        return True

    def set_power_mode(self, mode):
        commands = {
            "power-saver": "powerprofilesctl set power-saver",
            "balanced": "powerprofilesctl set balanced", 
            "performance": "powerprofilesctl set performance",
        }
        if mode in commands:
            exec_shell_command_async(commands[mode])
            self.current_mode = mode
            self.update_button_styles()
            
            mode_text = {
                "power-saver": "Энергосбережение",
                "balanced": "Сбалансированный", 
                "performance": "Производительный"
            }.get(self.current_mode, "Неизвестно")
            
            battery_data = shared_provider.get_battery()
            value, charging, time = battery_data
            percentage = int(value)
            
            full_tooltip = f"Режим питания: {mode_text}\nЗаряд: {percentage}%"
            self.battery_button.set_tooltip_markup(full_tooltip)


    def update_button_styles(self):
        if self.bat_save: self.bat_save.remove_style_class("active")
        if self.bat_balanced: self.bat_balanced.remove_style_class("active")
        if self.bat_perf: self.bat_perf.remove_style_class("active")

        if self.current_mode == "power-saver" and self.bat_save: self.bat_save.add_style_class("active")
        elif self.current_mode == "balanced" and self.bat_balanced: self.bat_balanced.add_style_class("active")
        elif self.current_mode == "performance" and self.bat_perf: self.bat_perf.add_style_class("active")


class NetworkApplet(Button):
    def __init__(self, **kwargs):
        super().__init__(name="button-bar", **kwargs)

        self.network_client = NetworkClient()
        self.is_mouse_over = False
        self._revealed = False

        self.download_label = Label(name="download-label", markup="0 B/s")
        self.upload_label = Label(name="upload-label", markup="0 B/s")
        self.wifi_label = Label(name="network-icon-label", markup=icons.world_off)

        self.download_revealer = Revealer(
            child=Box(children=[
                Label(name="download-icon-label", markup=icons.download),
                self.download_label,
            ]),
            transition_type="slide-right",
            child_revealed=False,
        )

        self.upload_revealer = Revealer(
            child=Box(children=[
                self.upload_label,
                Label(name="upload-icon-label", markup=icons.upload),
            ]),
            transition_type="slide-left",
            child_revealed=False,
        )

        self.add(Box(
            orientation="h",
            children=[self.upload_revealer, self.wifi_label, self.download_revealer],
        ))

        self.last_counters = psutil.net_io_counters()
        self.last_time = time.time()

        # Removed timer-based updates - we'll update on demand
        self._enter_handler = self.connect("enter-notify-event", self._on_enter)
        self._leave_handler = self.connect("leave-notify-event", self._on_leave)

    def destroy(self):
        # No timer to remove since we removed it

        for h in (self._enter_handler, self._leave_handler):
            if h:
                self.disconnect(h)

        if self.network_client:
            for m in ("destroy", "close"):
                if hasattr(self.network_client, m):
                    getattr(self.network_client, m)()
                    break

        super().destroy()

    def update_network(self):
        """Update network stats on demand"""
        now = time.time()
        elapsed = now - self.last_time
        if elapsed <= 0:
            return True

        counters = psutil.net_io_counters()
        recv = (counters.bytes_recv - self.last_counters.bytes_recv) / elapsed
        sent = (counters.bytes_sent - self.last_counters.bytes_sent) / elapsed

        self.download_label.set_markup(self.format_speed(recv))
        self.upload_label.set_markup(self.format_speed(sent))

        self._update_reveal()
        self._update_wifi_icon()

        self.last_counters = counters
        self.last_time = now
        return True

    def _update_reveal(self):
        if self._revealed == self.is_mouse_over:
            return
        self._revealed = self.is_mouse_over
        self.download_revealer.set_reveal_child(self._revealed)
        self.upload_revealer.set_reveal_child(self._revealed)

    def _update_wifi_icon(self):
        nc = self.network_client

        if nc.ethernet_device and nc.ethernet_device.internet in ("activated", "activating"):
            self.wifi_label.set_markup(icons.world)
            self.set_tooltip_text("Ethernet Connection")
            return

        wd = nc.wifi_device
        if not (wd and getattr(wd, "enabled", False)):
            self._set_disconnected()
            return

        ssid = getattr(wd, "ssid", None)
        if not ssid or ssid in ("Disconnected", "Выключено", "Не подключено"):
            self._set_disconnected()
            return

        s = wd.strength
        self.wifi_label.set_markup(
            icons.wifi_3 if s >= 75 else
            icons.wifi_2 if s >= 50 else
            icons.wifi_1 if s >= 25 else
            icons.wifi_0
        )
        self.set_tooltip_text(ssid)

    def _set_disconnected(self):
        self.wifi_label.set_markup(icons.world_off)
        self.set_tooltip_text("Disconnected")

    def format_speed(self, speed):
        if speed < 1024:
            return f"{speed:.0f} B/s"
        if speed < 1024 * 1024:
            return f"{speed / 1024:.1f} KB/s"
        return f"{speed / (1024 * 1024):.1f} MB/s"

    def _on_enter(self, *_):
        self.is_mouse_over = True
        # Update network stats when mouse enters to ensure fresh data
        self.update_network()
        self._update_reveal()

    def _on_leave(self, *_):
        self.is_mouse_over = False
        self._update_reveal()