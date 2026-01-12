from fabric.audio.service import Audio
from fabric.widgets.box import Box
from fabric.widgets.label import Label
from fabric.widgets.scale import Scale
from fabric.widgets.scrolledwindow import ScrolledWindow
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

class MixerSlider(Scale):
    def __init__(self, stream, label_ref, **kwargs):
        super().__init__(
            name="control-slider",
            orientation="h",
            h_expand=True,
            has_origin=True,
            increments=(0.01, 0.05),
            style_classes=["no-icon"],
            **kwargs,
        )
        self.stream = stream
        self.label_ref = label_ref
        self._updating = False
        
        # Начальное состояние
        vol = stream.volume
        self.set_value(vol / 100)
        self.set_size_request(-1, 30)
        
        # Сигналы
        self._val_id = self.connect("value-changed", self.on_value_changed)
        self._str_id = stream.connect("changed", self.on_stream_changed)

        # Стиль без лишних вызовов lower()
        st_type = getattr(stream, "type", "").lower()
        self.add_style_class("mic" if ("mic" in st_type or "input" in st_type) else "vol")
        
        self.update_ui(vol)

    def update_ui(self, vol):
        # Округление без модуля math
        v_int = int(vol + 0.5)
        txt = f"{v_int}%"
        self.set_tooltip_text(txt)
        self.label_ref.set_text(f"[{txt}] {self.stream.description}")
        
        if self.stream.muted:
            self.add_style_class("muted")
        else:
            self.remove_style_class("muted")

    def on_value_changed(self, _):
        if self._updating: return
        
        vol = self.get_value() * 100
        self.stream.volume = vol
        self.update_ui(vol)
        
        # Вместо таймера используем кратковременную смену состояния, 
        # если CSS это поддерживает, или просто убираем тяжелый GObject.timeout
        self.add_style_class("active")

    def on_stream_changed(self, stream):
        self._updating = True
        vol = stream.volume
        self.set_value(vol / 100)
        self.update_ui(vol)
        self._updating = False

    def destroy(self):
        self.stream.disconnect(self._str_id)
        self.disconnect(self._val_id)
        self.stream = None
        self.label_ref = None
        super().destroy()

class MixerSection(Box):
    def __init__(self, title, **kwargs):
        super().__init__(name="mixer-section", orientation="v", spacing=8, h_expand=True)
        self.title_label = Label(name="mixer-section-title", label=title, h_expand=True)
        self.content_box = Box(name="mixer-content", orientation="v", spacing=8, h_expand=True)
        self.add(self.title_label)
        self.add(self.content_box)
        self.widgets = {}

    def update_streams(self, streams):
        new_set = set(streams)
        old_set = set(self.widgets.keys())

        # Удаление
        for s in (old_set - new_set):
            res = self.widgets.pop(s)
            res[0].destroy() # slider
            self.content_box.remove(res[2]) # container

        # Добавление/Обновление
        for s in streams:
            if s in self.widgets:
                self.widgets[s][0].on_stream_changed(s)
            else:
                cnt = Box(orientation="v", spacing=4, h_expand=True)
                lbl = Label(
                    name="mixer-stream-label",
                    h_expand=True,
                    ellipsization="end",
                    max_chars_width=45,
                    height_request=20,
                )
                sld = MixerSlider(s, lbl)
                cnt.pack_start(lbl, False, False, 0)
                cnt.pack_start(sld, False, False, 0)
                self.content_box.add(cnt)
                self.widgets[s] = (sld, lbl, cnt)
        
        self.show_all()

class Mixer(Box):
    def __init__(self, **kwargs):
        super().__init__(name="mixer", orientation="v", spacing=8, h_expand=True, v_expand=True)
        
        try:
            self.audio = Audio()
        except:
            self.add(Label(label="Audio Error"))
            return

        self.main_container = Box(orientation="h", spacing=8, h_expand=True, v_expand=True)
        self.main_container.set_homogeneous(True)

        # Создание секций через вспомогательную функцию для уменьшения дублирования кода
        self.out_sec = self._setup_scrolled("Выходы")
        self.in_sec = self._setup_scrolled("Входы")

        self.add(self.main_container)
        self.set_size_request(-1, 395)

        self.audio.connect("changed", self.update_mixer)
        self.audio.connect("stream-added", self.update_mixer)
        self.audio.connect("stream-removed", self.update_mixer)
        self.update_mixer()
        self.show_all()

    def _setup_scrolled(self, title):
        sw = ScrolledWindow(h_expand=True, v_expand=False, v_policy="auto", h_policy="never")
        sw.set_size_request(-1, 150)
        sec = MixerSection(title)
        sw.add(sec)
        self.main_container.add(sw)
        return sec

    def update_mixer(self, *args):
        # Сбор данных без лишних промежуточных списков
        self.out_sec.update_streams([self.audio.speaker] + list(self.audio.applications) if self.audio.speaker else list(self.audio.applications))
        self.in_sec.update_streams([self.audio.microphone] + list(self.audio.recorders) if self.audio.microphone else list(self.audio.recorders))

    def destroy(self):
        self.audio.disconnect_by_func(self.update_mixer)
        super().destroy()