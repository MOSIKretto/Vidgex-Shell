from fabric.audio.service import Audio
from fabric.widgets.box import Box
from fabric.widgets.label import Label
from fabric.widgets.scale import Scale
from fabric.widgets.scrolledwindow import ScrolledWindow

from gi.repository import Gtk

# Синглтон для Audio
_audio_instance = None

def _get_audio():
    global _audio_instance
    if _audio_instance is None:
        _audio_instance = Audio()
    return _audio_instance


# Константы
_SLIDER_HEIGHT = 30
_LABEL_HEIGHT = 20
_SECTION_HEIGHT = 150
_MAX_LABEL_CHARS = 45


class MixerSlider(Scale):
    def __init__(self, stream, **kwargs):
        super().__init__(
            name="control-slider",
            orientation="h",
            h_expand=True,
            h_align="fill",
            has_origin=True,
            increments=(0.01, 0.1),
            style_classes=["no-icon"],
            **kwargs,
        )

        self.stream = stream
        self._updating = False
        
        volume = stream.volume
        self.set_value(volume * 0.01)
        self.set_size_request(-1, _SLIDER_HEIGHT)
        self.set_tooltip_text(f"{volume:.0f}%")

        self.connect("value-changed", self._on_value_changed)
        stream.connect("changed", self._on_stream_changed)

        # Определяем тип стиля
        stream_type = getattr(stream, "type", "")
        if stream_type:
            type_lower = stream_type.lower()
            if "microphone" in type_lower or "input" in type_lower:
                self.add_style_class("mic")
            else:
                self.add_style_class("vol")
        else:
            self.add_style_class("vol")

        # Начальное состояние muted
        if stream.muted:
            self.add_style_class("muted")

    def _on_value_changed(self, _):
        if self._updating:
            return
        stream = self.stream
        if stream:
            new_volume = self.value * 100
            stream.volume = new_volume
            self.set_tooltip_text(f"{new_volume:.0f}%")

    def _on_stream_changed(self, stream):
        self._updating = True
        volume = stream.volume
        self.value = volume * 0.01
        self.set_tooltip_text(f"{volume:.0f}%")
        
        if stream.muted:
            self.add_style_class("muted")
        else:
            self.remove_style_class("muted")
        
        self._updating = False


class MixerSection(Box):
    def __init__(self, title, **kwargs):
        super().__init__(
            name="mixer-section",
            orientation="v",
            spacing=8,
            h_expand=True,
            v_expand=False,
        )

        self.title_label = Label(
            name="mixer-section-title",
            label=title,
            h_expand=True,
            h_align="fill",
        )

        self.content_box = Box(
            name="mixer-content",
            orientation="v",
            spacing=8,
            h_expand=True,
            v_expand=False,
        )

        self.add(self.title_label)
        self.add(self.content_box)
        
        self._stream_widgets = {}

    def update_streams(self, streams):
        content = self.content_box
        old_widgets = self._stream_widgets
        new_widgets = {}
        
        # Собираем текущие stream id
        current_ids = set()
        for stream in streams:
            stream_id = id(stream)
            current_ids.add(stream_id)
            
            if stream_id in old_widgets:
                # Переиспользуем существующий виджет
                container, label = old_widgets[stream_id]
                new_widgets[stream_id] = (container, label)
                # Обновляем только label
                volume = stream.volume
                vol_int = int(volume + 0.5)  # Быстрее чем math.ceil для положительных
                label.set_label(f"[{vol_int}%] {stream.description}")
            else:
                # Создаём новый виджет
                container, label = self._create_stream_widget(stream)
                new_widgets[stream_id] = (container, label)
                content.add(container)
        
        # Удаляем старые виджеты
        for stream_id, (container, _) in old_widgets.items():
            if stream_id not in current_ids:
                content.remove(container)
                container.destroy()
        
        self._stream_widgets = new_widgets
        content.show_all()

    def _create_stream_widget(self, stream):
        volume = stream.volume
        vol_int = int(volume + 0.5)
        
        container = Box(
            orientation="v",
            spacing=4,
            h_expand=True,
            v_expand=False,
        )

        label = Label(
            name="mixer-stream-label",
            label=f"[{vol_int}%] {stream.description}",
            h_expand=True,
            h_align="start",
            v_align="center",
            ellipsization="end",
            max_chars_width=_MAX_LABEL_CHARS,
            height_request=_LABEL_HEIGHT,
        )

        slider = MixerSlider(stream)
        
        # Подключаем обновление label при изменении stream
        stream.connect("changed", self._on_stream_volume_changed, label)

        container.add(label)
        container.add(slider)
        
        return container, label

    def _on_stream_volume_changed(self, stream, label):
        volume = stream.volume
        vol_int = int(volume + 0.5)
        label.set_label(f"[{vol_int}%] {stream.description}")


class Mixer(Box):
    def __init__(self, **kwargs):
        super().__init__(
            name="mixer",
            orientation="v",
            spacing=8,
            h_expand=True,
            v_expand=True,
        )

        try:
            self.audio = _get_audio()
        except Exception as e:
            self.add(Label(
                label=f"Audio service unavailable: {e}",
                h_align="center",
                v_align="center",
                h_expand=True,
                v_expand=True,
            ))
            return

        self._setup_ui()
        self._connect_signals()
        self._update_mixer()
        self.show_all()

    def _setup_ui(self):
        main_container = Box(
            orientation="h",
            spacing=8,
            h_expand=True,
            v_expand=True,
        )
        main_container.set_homogeneous(True)

        # Outputs
        self.outputs_section = MixerSection("Outputs")
        outputs_scrolled = ScrolledWindow(
            name="outputs-scrolled",
            h_expand=True,
            v_expand=False,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            child=self.outputs_section,
        )
        outputs_scrolled.set_size_request(-1, _SECTION_HEIGHT)
        outputs_scrolled.set_max_content_height(_SECTION_HEIGHT)

        # Inputs
        self.inputs_section = MixerSection("Inputs")
        inputs_scrolled = ScrolledWindow(
            name="inputs-scrolled",
            h_expand=True,
            v_expand=False,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            child=self.inputs_section,
        )
        inputs_scrolled.set_size_request(-1, _SECTION_HEIGHT)
        inputs_scrolled.set_max_content_height(_SECTION_HEIGHT)

        main_container.add(outputs_scrolled)
        main_container.add(inputs_scrolled)

        self.add(main_container)
        self.set_size_request(-1, _SECTION_HEIGHT * 2)

    def _connect_signals(self):
        audio = self.audio
        handler = self._update_mixer
        audio.connect("changed", handler)
        audio.connect("stream-added", handler)
        audio.connect("stream-removed", handler)

    def _update_mixer(self):
        audio = self.audio
        
        # Outputs
        outputs = []
        speaker = audio.speaker
        if speaker:
            outputs.append(speaker)
        applications = audio.applications
        if applications:
            outputs.extend(applications)
        
        # Inputs
        inputs = []
        microphone = audio.microphone
        if microphone:
            inputs.append(microphone)
        recorders = audio.recorders
        if recorders:
            inputs.extend(recorders)
        
        self.outputs_section.update_streams(outputs)
        self.inputs_section.update_streams(inputs)