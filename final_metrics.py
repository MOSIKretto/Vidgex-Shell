"""Final refactored metrics module with proper cleanup and DRY principles."""

from fabric.core.fabricator import Fabricator
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label
from fabric.widgets.scale import Scale
from fabric.utils import exec_shell_command_async

from gi.repository import GLib
import subprocess
import psutil
import threading
import json
from typing import Dict, List, Tuple, Optional

from base_component import BaseComponent, ReusableUIBuilder, debounce
from services.network import NetworkClient
from services.upower import UPowerManager
import modules.icons as icons


class MetricsProvider(BaseComponent):
    """Singleton metrics provider with proper resource management."""
    
    _instance = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MetricsProvider, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        super().__init__()  # Call parent init to set up base properties
        self._initialized = True
        
        # Initialize metrics
        self.cpu = 0.0
        self.mem = 0.0
        self.disk = []
        self.gpu = []
        self.temperature = 0.0
        
        # Battery information
        self.bat_percent = 0.0
        self.bat_charging = None
        self.bat_time = 0
        
        # Initialize services
        self.upower = UPowerManager()
        self.display_device = self.upower.get_display_device()
        
        # Threading and timing
        self._lock = threading.Lock()
        self._gpu_update_running = False
        self._gpu_update_counter = 0
        self._should_stop = False
        self._gpu_thread = None
        
        # Start update loop
        timer_id = GLib.timeout_add_seconds(2, self._update)
        self.add_timer(timer_id)

    def destroy(self):
        """Properly clean up resources."""
        self._should_stop = True
        
        # Stop GPU thread
        if self._gpu_thread and self._gpu_thread.is_alive():
            self._gpu_thread.join(timeout=3.0)
        
        # Clean up upower manager
        if self.upower:
            self.upower.destroy()
        
        # Call parent destroy
        super().destroy()

    def _update(self):
        """Update metrics periodically."""
        if self._should_stop:
            return False
            
        # Update CPU, memory, disk
        self.cpu = psutil.cpu_percent(interval=0)
        self.mem = psutil.virtual_memory().percent
        self.disk = [psutil.disk_usage(path).percent for path in ["/"]]
        
        # Update temperature
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

        # Update GPU every 5 cycles
        self._gpu_update_counter += 1
        if self._gpu_update_counter >= 5:
            self._gpu_update_counter = 0
            if not self._gpu_update_running and not self._should_stop:
                self._update_gpu()

        # Update battery
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
        """Update GPU metrics in a separate thread."""
        if self._should_stop:
            return
            
        with self._lock:
            if self._gpu_update_running or self._should_stop:
                return
            self._gpu_update_running = True
        
        def worker():
            output = None
            try:
                result = subprocess.check_output(["nvtop", "-s"], text=True, timeout=10)
                output = result
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError, Exception):
                print("Failed to collect GPU info")
                output = None

            if not self._should_stop:
                GLib.idle_add(self._process_gpu_output, output)
        
        self._gpu_thread = threading.Thread(target=worker, daemon=True)
        self._gpu_thread.start()

    def _process_gpu_output(self, output):
        """Process GPU output from thread."""
        with self._lock:
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

    def get_metrics(self) -> Tuple[float, float, List[float], List[int], float]:
        """Get all metrics."""
        return (self.cpu, self.mem, self.disk, self.gpu, self.temperature)

    def get_battery(self) -> Tuple[float, bool, int]:
        """Get battery information."""
        return (self.bat_percent, self.bat_charging, self.bat_time)


def cleanup_metrics_provider():
    """Cleanup function for metrics provider."""
    provider = MetricsProvider()
    provider.destroy()


class MetricsSmall(BaseComponent):
    """Compact metrics display component."""
    
    def __init__(self, **kwargs):
        super().__init__()
        
        # Create main button container
        self.button = Button(name="metrics-small", **kwargs)
        
        # Create main container
        main_box = Box(
            spacing=0,
            orientation="h",
            visible=True,
            all_visible=True,
        )
        
        # Get metrics provider
        self.provider = MetricsProvider()
        
        # Create metric widgets using the reusable builder
        self.temp_widget = ReusableUIBuilder.create_metric_widget(icons.temp, "temp", is_temp=True)
        self.disk_widgets = [ReusableUIBuilder.create_metric_widget(icons.disk, "disk") for _ in ["/"]]
        self.cpu_widget = ReusableUIBuilder.create_metric_widget(icons.cpu, "cpu")
        self.ram_widget = ReusableUIBuilder.create_metric_widget(icons.memory, "ram")
        
        try:
            gpu_info = self.provider.get_gpu_info()
            self.gpu_widgets = [ReusableUIBuilder.create_metric_widget(icons.gpu, "gpu") for _ in gpu_info]
        except:
            self.gpu_widgets = []

        # Add widgets to main box
        if self.temp_widget:
            main_box.add(self.temp_widget['container'])
            main_box.add(Box(name="metrics-sep"))
        for disk in self.disk_widgets:
            main_box.add(disk['container'])
            main_box.add(Box(name="metrics-sep"))
        if self.ram_widget:
            main_box.add(self.ram_widget['container'])
            main_box.add(Box(name="metrics-sep"))
        if self.cpu_widget:
            main_box.add(self.cpu_widget['container'])
        for gpu in self.gpu_widgets:
            main_box.add(gpu['container'])
            main_box.add(Box(name="metrics-sep"))

        self.button.add(main_box)

        # Timer for updates
        timer_id = GLib.timeout_add_seconds(2, self.update_metrics)
        self.add_timer(timer_id)

        # Mouse enter/leave handlers
        self.hide_timer = None
        self.hover_counter = 0
        self._enter_handler = self.button.connect("enter-notify-event", self.on_mouse_enter)
        self._leave_handler = self.button.connect("leave-notify-event", self.on_mouse_leave)
        
        # Track signal handlers
        self.add_signal_handler(self.button, self._enter_handler)
        self.add_signal_handler(self.button, self._leave_handler)

    def _format_percentage(self, value: float) -> str:
        """Format percentage value."""
        return f"{int(value)}%"

    def _format_temperature(self, value: float) -> str:
        """Format temperature value."""
        rounded_value = round(value)
        return f"{rounded_value}°C"

    def on_mouse_enter(self, widget, event):
        """Handle mouse enter event."""
        self.hover_counter += 1
        if self.hide_timer:
            GLib.source_remove(self.hide_timer)
            self.hide_timer = None

        if self.temp_widget: 
            self.temp_widget['revealer'].set_reveal_child(True)
        if self.cpu_widget: 
            self.cpu_widget['revealer'].set_reveal_child(True)
        if self.ram_widget: 
            self.ram_widget['revealer'].set_reveal_child(True)
        for disk in self.disk_widgets: 
            disk['revealer'].set_reveal_child(True)
        for gpu in self.gpu_widgets: 
            gpu['revealer'].set_reveal_child(True)
        return False

    def on_mouse_leave(self, widget, event):
        """Handle mouse leave event."""
        if self.hover_counter > 0:
            self.hover_counter -= 1
        if self.hover_counter == 0:
            if self.hide_timer:
                GLib.source_remove(self.hide_timer)
            self.hide_timer = GLib.timeout_add(500, self.hide_revealer)
        return False

    def hide_revealer(self):
        """Hide all revealers."""
        if self.temp_widget: 
            self.temp_widget['revealer'].set_reveal_child(False)
        if self.cpu_widget: 
            self.cpu_widget['revealer'].set_reveal_child(False)
        if self.ram_widget: 
            self.ram_widget['revealer'].set_reveal_child(False)
        for disk in self.disk_widgets: 
            disk['revealer'].set_reveal_child(False)
        for gpu in self.gpu_widgets: 
            gpu['revealer'].set_reveal_child(False)
        self.hide_timer = None
        return False

    def update_metrics(self):
        """Update metrics display."""
        cpu, mem, disks, gpus, temperature = self.provider.get_metrics()

        if self.temp_widget:
            self.temp_widget['circle'].set_value(min(temperature / 150.0, 1.0))
            self.temp_widget['level'].set_label(self._format_temperature(temperature))
        
        if self.cpu_widget:
            self.cpu_widget['circle'].set_value(cpu / 100.0)
            self.cpu_widget['level'].set_label(self._format_percentage(cpu))
        if self.ram_widget:
            self.ram_widget['circle'].set_value(mem / 100.0)
            self.ram_widget['level'].set_label(self._format_percentage(mem))
        for i, disk in enumerate(self.disk_widgets):
            if i < len(disks):
                disk['circle'].set_value(disks[i] / 100.0)
                disk['level'].set_label(self._format_percentage(disks[i]))
        for i, gpu in enumerate(self.gpu_widgets):
            if i < len(gpus):
                gpu['circle'].set_value(gpus[i] / 100.0)
                gpu['level'].set_label(self._format_percentage(gpus[i]))

        # Update tooltip
        tooltip_parts = []
        if self.temp_widget: 
            tooltip_parts.append(self.temp_widget['markup'])
        if self.disk_widgets: 
            tooltip_parts.extend([disk['markup'] for disk in self.disk_widgets])
        if self.ram_widget: 
            tooltip_parts.append(self.ram_widget['markup'])
        if self.cpu_widget: 
            tooltip_parts.append(self.cpu_widget['markup'])
        if self.gpu_widgets: 
            tooltip_parts.extend([gpu['markup'] for gpu in self.gpu_widgets])
        
        self.button.set_tooltip_markup(" - ".join(tooltip_parts))

        return True

    def destroy(self):
        """Destroy the metrics small component."""
        if self.hide_timer:
            GLib.source_remove(self.hide_timer)
            self.hide_timer = None
        
        super().destroy()


class BatteryButton(BaseComponent):
    """Battery button component."""
    
    def __init__(self, **kwargs):
        super().__init__()
        
        self.button = Button(name="metrics-small", **kwargs)

        # Create battery widgets using reusable builder
        self.bat_widget = ReusableUIBuilder.create_metric_widget(icons.battery, "bat")

        # Replace the circle's child with our battery icon
        self.bat_widget['icon'] = Label(name="metrics-icon", markup=icons.battery)
        self.bat_widget['circle'] = ReusableUIBuilder.create_circular_progress_bar(
            value=0,
            size=28,
            line_width=2,
            start_angle=150,
            end_angle=390,
            style_classes="bat",
            child=self.bat_widget['icon']
        )
        self.bat_widget['level'] = Label(name="metrics-level", style_classes="bat", label="100%")

        self.bat_widget['revealer'] = ReusableUIBuilder.create_revealer(
            self.bat_widget['level'],
            "slide-left",
            False
        )

        self.bat_widget['container'] = Box(
            name="metrics-bat-box",
            orientation="h",
            spacing=0,
            children=[self.bat_widget['circle'], self.bat_widget['revealer']],
        )

        self.button.add(self.bat_widget['container'])

        # Setup battery updates
        self.provider = MetricsProvider()
        self._update_battery(None, self.provider.get_battery())

        # Connect timer for updates
        timer_id = GLib.timeout_add_seconds(2, self._periodic_update)
        self.add_timer(timer_id)

    def _periodic_update(self):
        """Periodically update battery information."""
        self._update_battery(None, self.provider.get_battery())
        return True

    def _format_percentage(self, value: float) -> str:
        """Format percentage value."""
        return f"{int(value)}%"

    def _update_battery(self, sender, battery_data):
        """Update battery display."""
        value, charging, time = battery_data
        if value == 0:
            self.button.set_visible(False)
        else:
            self.button.set_visible(True)
            self.bat_widget['circle'].set_value(value / 100)
            
        percentage = int(value)
        self.bat_widget['level'].set_label(self._format_percentage(percentage))
        
        if percentage <= 30:
            self.bat_widget['circle'].set_style("border: 3px solid #ffa500;")
            self.bat_widget['icon'].set_style("color: #ffa500;")
            self.bat_widget['circle'].add_style_class("battery-low")
            self.bat_widget['circle'].remove_style_class("battery-normal")
        else:
            self.bat_widget['circle'].set_style("border: 3px solid var(--green);")
            self.bat_widget['icon'].set_style("color: #d3d3d3;")
            self.bat_widget['circle'].add_style_class("battery-normal")
            self.bat_widget['circle'].remove_style_class("battery-low")

        # Format time
        if time < 60:
            time_status = f"{int(time)}сек"
        elif time < 60 * 60:
            time_status = f"{int(time / 60)}мин"
        else:
            time_status = f"{int(time / 60 / 60)}ч"

        # Set appropriate icon and status
        if percentage == 100 and not charging:
            self.bat_widget['icon'].set_markup(icons.battery)
            charging_status = f"{icons.bat_full} Полностью заряжено - осталось {time_status}"
        elif percentage == 100 and charging:
            self.bat_widget['icon'].set_markup(icons.battery)
            charging_status = f"{icons.bat_full} Полностью заряжено"
        elif charging:
            self.bat_widget['icon'].set_markup(icons.charging)
            charging_status = f"{icons.bat_charging} Заряжается - осталось {time_status}"
        elif percentage <= 30 and not charging:
            self.bat_widget['icon'].set_markup(icons.alert)
            charging_status = f"{icons.bat_low} Низкий заряд - осталось {time_status}"
        elif not charging:
            self.bat_widget['icon'].set_markup(icons.discharging)
            charging_status = f"{icons.bat_discharging} Разряжается - осталось {time_status}"
        else:
            self.bat_widget['icon'].set_markup(icons.battery)
            charging_status = "Батарея"

        self.button.set_tooltip_markup(charging_status)

    def destroy(self):
        """Destroy the battery button component."""
        super().destroy()


class NetworkApplet(BaseComponent):
    """Network applet component."""
    
    def __init__(self, **kwargs):
        super().__init__()
        
        self.button = Button(name="button-bar", **kwargs)
        self.client = NetworkClient()

        self.is_mouse_over = False
        self._revealed = False

        self.download_label = Label(name="download-label", markup="0 B/s")
        self.upload_label = Label(name="upload-label", markup="0 B/s")
        self.wifi_label = Label(name="network-icon-label", markup=icons.world_off)

        self.download_revealer = ReusableUIBuilder.create_revealer(
            Box(children=[
                Label(name="download-icon-label", markup=icons.download),
                self.download_label,
            ]),
            "slide-right",
            False
        )

        self.upload_revealer = ReusableUIBuilder.create_revealer(
            Box(children=[
                self.upload_label,
                Label(name="upload-icon-label", markup=icons.upload),
            ]),
            "slide-left",
            False
        )

        self.button.add(Box(
            orientation="h",
            children=[self.upload_revealer, self.wifi_label, self.download_revealer],
        ))

        self.last_counters = psutil.net_io_counters()
        self.last_time = __import__('time').time()

        timer_id = GLib.timeout_add(1000, self.update_network)
        self.add_timer(timer_id)
        
        self._enter_handler = self.button.connect("enter-notify-event", self._on_enter)
        self._leave_handler = self.button.connect("leave-notify-event", self._on_leave)
        
        # Track signal handlers
        self.add_signal_handler(self.button, self._enter_handler)
        self.add_signal_handler(self.button, self._leave_handler)

    def update_network(self):
        """Update network statistics."""
        import time
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
        """Update reveal state."""
        if self._revealed == self.is_mouse_over:
            return
        self._revealed = self.is_mouse_over
        self.download_revealer.set_reveal_child(self._revealed)
        self.upload_revealer.set_reveal_child(self._revealed)

    def _update_wifi_icon(self):
        """Update WiFi icon based on connection status."""
        nc = self.client

        if nc.ethernet_device and nc.ethernet_device.internet in ("activated", "activating"):
            self.wifi_label.set_markup(icons.world)
            self.button.set_tooltip_text("Ethernet Connection")
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
        self.button.set_tooltip_text(ssid)

    def _set_disconnected(self):
        """Set disconnected state."""
        self.wifi_label.set_markup(icons.world_off)
        self.button.set_tooltip_text("Disconnected")

    def format_speed(self, speed: float) -> str:
        """Format network speed."""
        if speed < 1024:
            return f"{speed:.0f} B/s"
        if speed < 1024 * 1024:
            return f"{speed / 1024:.1f} KB/s"
        return f"{speed / (1024 * 1024):.1f} MB/s"

    def _on_enter(self, *_):
        """Handle mouse enter."""
        self.is_mouse_over = True
        self._update_reveal()

    def _on_leave(self, *_):
        """Handle mouse leave."""
        self.is_mouse_over = False
        self._update_reveal()

    def destroy(self):
        """Destroy the network applet component."""
        if self.client:
            self.client.destroy()
        super().destroy()


class Battery(BaseComponent):
    """Battery container component."""
    
    def __init__(self, **kwargs):
        super().__init__()
        
        self.container = Box(
            orientation="h",
            spacing=0,
            **kwargs
        )
        
        self.battery_button = BatteryButton()
        
        from fabric.widgets.revealer import Revealer
        self.power_modes_revealer = Revealer(
            name="metrics-power-modes-revealer",
            transition_duration=250,
            transition_type="slide-left",
            child=Box(name="power-mode-switcher", orientation="h", spacing=2),
            child_revealed=False,
        )

        self.container.add(self.battery_button)
        self.container.add(self.power_modes_revealer)

        self._event_handlers = []
        self._setup_event_handlers()
        
        self.hide_timer = None
        self.is_mouse_inside = False

    def _setup_event_handlers(self):
        """Setup event handlers for the battery container."""
        events = [
            ("enter-notify-event", self.on_container_enter),
            ("leave-notify-event", self.on_container_leave),
        ]
        
        for event, handler in events:
            handler_id = self.container.connect(event, handler)
            self._event_handlers.append((self.container, handler_id))
            
            handler_id = self.battery_button.button.connect(event, handler)
            self._event_handlers.append((self.battery_button.button, handler_id))
            
            handler_id = self.power_modes_revealer.child.connect(event, handler)
            self._event_handlers.append((self.power_modes_revealer.child, handler_id))

    def on_container_enter(self, widget, event):
        """Handle container enter event."""
        self.is_mouse_inside = True
        if self.hide_timer:
            GLib.source_remove(self.hide_timer)
            self.hide_timer = None

        self.power_modes_revealer.set_reveal_child(True)
        return False

    def on_container_leave(self, widget, event):
        """Handle container leave event."""
        self.is_mouse_inside = False
        if self.hide_timer:
            GLib.source_remove(self.hide_timer)
        self.hide_timer = GLib.timeout_add(1000, self._hide_power_modes)
        return False

    def _hide_power_modes(self):
        """Hide power modes after delay."""
        if not self.is_mouse_inside:
            self.power_modes_revealer.set_reveal_child(False)
        self.hide_timer = None
        return False

    def destroy(self):
        """Destroy the battery component."""
        if self.hide_timer:
            GLib.source_remove(self.hide_timer)
            self.hide_timer = None
        
        for obj, handler_id in self._event_handlers:
            try:
                obj.disconnect(handler_id)
            except Exception:
                pass
        self._event_handlers.clear()
        
        if self.battery_button:
            self.battery_button.destroy()
        
        super().destroy()