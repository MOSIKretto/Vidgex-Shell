#!/usr/bin/env python3
import evdev
import evdev.ecodes as e
import subprocess
import signal
import sys
import json
import logging
import time
import os

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
log = logging.getLogger('autolayout')

# ================= ПРОВЕРКА ОКРУЖЕНИЯ =================

def check_hyprctl():
    """Проверяет, доступен ли Hyprland (защита от запуска через обычный sudo)"""
    try:
        subprocess.run(["hyprctl", "devices"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    except Exception:
        log.error("❌ ОШИБКА: hyprctl недоступен!")
        log.error("Если вы запускаете скрипт через sudo, переменные окружения Wayland теряются.")
        log.error("👉 ЗАПУСКАЙТЕ ТАК:  sudo -E python3 script.py")
        sys.exit(1)

# ================= МАППИНГ =================

KEY_MAP = {
    16: 'q', 17: 'w', 18: 'e', 19: 'r', 20: 't', 21: 'y', 22: 'u', 23: 'i',
    24: 'o', 25: 'p', 26: '[', 27: ']',
    30: 'a', 31: 's', 32: 'd', 33: 'f', 34: 'g', 35: 'h', 36: 'j', 37: 'k',
    38: 'l', 39: ';', 40: "'",
    44: 'z', 45: 'x', 46: 'c', 47: 'v', 48: 'b', 49: 'n', 50: 'm', 51: ',',
    52: '.', 53: '/', 41: '`',
}

EN_TO_RU = {
    'q': 'й', 'w': 'ц', 'e': 'у', 'r': 'к', 't': 'е', 'y': 'н', 'u': 'г',
    'i': 'ш', 'o': 'щ', 'p': 'з', '[': 'х', ']': 'ъ',
    'a': 'ф', 's': 'ы', 'd': 'в', 'f': 'а', 'g': 'п', 'h': 'р', 'j': 'о',
    'k': 'л', 'l': 'д', ';': 'ж', "'": 'э',
    'z': 'я', 'x': 'ч', 'c': 'с', 'v': 'м', 'b': 'и', 'n': 'т', 'm': 'ь',
    ',': 'б', '.': 'ю', '/': '.', '`': 'ё',
}

EN_VOWELS = set('aeiouy')
RU_VOWELS = set('аеёиоуыэюя')
EN_PUNCT_AS_RU_LETTER = set("[];',.`")

MODIFIER_SCANCODES = {29, 97, 56, 100, 125, 126, 1, 15, 58}
SHIFT_SCANCODES = {42, 54}
BOUNDARY_SCANCODES = {57, 28}
BACKSPACE_SC = 14

# ================= АНАЛИЗАТОР =================

class SmartAnalyzer:
    """Улучшенный математический анализатор с правилами орфографии"""

    @classmethod
    def analyze(cls, word: str) -> str | None:
        word = word.lower().rstrip("?/:!*()-,.;")
        if len(word) < 1:
            return None

        en_score = cls._score_english(word)
        ru_score = cls._score_russian(word)

        diff = ru_score - en_score
        
        # Пороги: для коротких слов мы теперь допускаем чуть меньший отрыв, 
        # потому что добавили сильные штрафы за орфографию
        threshold = 15.0 if len(word) <= 2 else (10.0 if len(word) <= 4 else 5.0)

        log.debug(f"Слово: '{word}' | EN: {en_score}, RU: {ru_score} | Diff: {diff}")

        if diff > threshold:
            return 'ru'
        elif diff < -threshold:
            return 'en'
        return None

    @classmethod
    def _score_english(cls, word: str) -> float:
        score = 0.0
        n = len(word)

        # 1. Гласные
        vowels = sum(1 for c in word if c in EN_VOWELS)
        ratio = vowels / n if n > 0 else 0
        if vowels == 0 and n >= 2: score -= 30
        elif 0.2 <= ratio <= 0.5: score += 10

        # 2. Пунктуация внутри слова = точно не EN
        for i, c in enumerate(word):
            if c in EN_PUNCT_AS_RU_LETTER:
                score -= 50 if i < n - 1 else 20

        # 3. Длинные цепочки согласных
        max_cons = cls._max_consecutive(word, lambda c: c.isalpha() and c not in EN_VOWELS)
        if max_cons >= 5: score -= 30
        elif max_cons == 4: score -= 10

        # 4. Бонус за типичные английские связки (this, false)
        common_en_pairs = ['th', 'he', 'in', 'er', 're', 'nd', 'ed', 'ng', 'al', 'se', 'is', 'to', 'of', 'it']
        for pair in common_en_pairs:
            if pair in word:
                score += 10

        # 5. Двойные буквы (cool, hello)
        for double in ('ll', 'ee', 'oo', 'ss', 'tt', 'ff', 'rr', 'nn', 'mm', 'pp', 'cc'):
            if double in word:
                score += 15

        # 6. АНГЛИЙСКАЯ ФОНОТАКТИКА (спасает 'yj' -> 'но')
        # Английские слова почти никогда не заканчиваются на j, v, q
        if word[-1] in 'jvq':
            score -= 25
            
        # Немая 'e' на конце
        if n >= 3 and word[-1] == 'e' and word[-2] not in EN_VOWELS:
            score += 10

        return score

    @classmethod
    def _score_russian(cls, word: str) -> float:
        score = 0.0
        n = len(word)

        ru = ''.join(EN_TO_RU.get(c, '\x00') for c in word)
        
        if '\x00' in ru:
            score -= 25 * ru.count('\x00')

        ru_clean = ru.replace('\x00', '')
        nc = len(ru_clean)
        if nc == 0: return score

        # 1. Гласные
        vowels = sum(1 for c in ru_clean if c in RU_VOWELS)
        ratio = vowels / nc if nc > 0 else 0
        
        if vowels == 0 and nc >= 2: score -= 30
        elif ratio > 0.6 and nc >= 4: score -= 15
        elif 0.2 <= ratio <= 0.55: score += 10

        # 2. Спецсимволы EN раскладки (; ' , .) = Точно русский
        punct_count = sum(1 for c in word if c in EN_PUNCT_AS_RU_LETTER)
        if punct_count > 0: score += 40 * punct_count

        # 3. РУССКАЯ ОРФОГРАФИЯ (Штрафы за невозможные связки)
        # Спасает 'ершы' (this) и 'шы' (is)
        for bad_pair in ('жы', 'шы', 'чя', 'щя', 'чю', 'щю'):
            if bad_pair in ru_clean:
                score -= 40  # Жесточайший штраф за нарушение правил 1 класса

        allowed_ru_vowel_pairs = {
            'ия', 'ие', 'ее', 'ая', 'ое', 'ую', 'юю', 'яя', 'ау', 'уа', 
            'ии', 'ои', 'аи', 'еи', 'уи', 'ае', 'оа', 'ио', 'ею', 'аю', 'ою', 'уе'
        }

        for i in range(nc - 1):
            a, b = ru_clean[i], ru_clean[i + 1]
            
            if a in RU_VOWELS and b in RU_VOWELS:
                if (a + b) not in allowed_ru_vowel_pairs:
                    score -= 25

            if a == b and a not in 'нсвмплк' and a not in RU_VOWELS:
                score -= 25
            if a not in RU_VOWELS and b == 'й' and a not in 'ьъ':
                score -= 20
            if a in 'бвгджзкпстфхцчшщ' and b in 'щц':
                score -= 15

        if ru_clean[0] in 'ыьъ':
            score -= 30

        alternations = sum(1 for i in range(nc - 1) if (ru_clean[i] in RU_VOWELS) != (ru_clean[i + 1] in RU_VOWELS))
        if nc >= 3 and alternations / (nc - 1) > 0.4: 
            score += 5

        return score

    @staticmethod
    def _max_consecutive(s: str, pred) -> int:
        mx = cur = 0
        for c in s:
            if pred(c):
                cur += 1
                mx = max(mx, cur)
            else:
                cur = 0
        return mx

# ================= HYPRLAND =================

class HyprlandManager:
    def __init__(self):
        self.en_index = "0"
        self.ru_index = "1"
        self._setup()

    def _setup(self):
        try:
            out = subprocess.check_output(["hyprctl", "getoption", "input:kb_layout", "-j"], text=True)
            layouts = json.loads(out)["str"].split(",")
            for i, layout in enumerate(layouts):
                name = layout.strip().lower()
                if "ru" in name: self.ru_index = str(i)
                elif "us" in name or "en" in name: self.en_index = str(i)
        except Exception:
            pass

    def get_layout(self) -> str:
        """Находит текущую раскладку основной (main) клавиатуры"""
        try:
            out = subprocess.check_output(["hyprctl", "devices", "-j"], text=True)
            devices = json.loads(out)
            for kbd in devices.get("keyboards", []):
                if kbd.get("main"): # Берем только основную клавиатуру
                    keymap = kbd.get("active_keymap", "").lower()
                    if "ru" in keymap or "рус" in keymap: return 'ru'
                    return 'en'
            return 'en'
        except Exception:
            return 'en'

    def switch_layout(self, lang: str):
        idx = self.ru_index if lang == 'ru' else self.en_index
        try:
            # all переключает разом и физическую, и нашу виртуальную клавиатуру
            subprocess.run(["hyprctl", "switchxkblayout", "all", idx], capture_output=True)
        except Exception as ex:
            log.error(f"Ошибка переключения: {ex}")

# ================= KEYSTROKE =================

class KeyStroke:
    __slots__ = ('scancode', 'shifted')
    def __init__(self, scancode: int, shifted: bool):
        self.scancode = scancode
        self.shifted = shifted
    @property
    def char(self) -> str:
        return KEY_MAP.get(self.scancode, '')

# ================= AUTOLAYOUT =================

class Autolayout:
    def __init__(self):
        self.hypr = HyprlandManager()
        self.device = self._find_keyboard()
        self.ui = evdev.UInput.from_device(self.device, name="autolayout-vkbd")

        self.buffer: list[KeyStroke] = []
        self.shift_pressed = False
        self.ctrl_pressed = False
        self.alt_pressed = False
        self.is_correcting = False
        self.modifier_used = False

    def _find_keyboard(self) -> evdev.InputDevice:
        candidates = []
        for path in evdev.list_devices():
            try:
                dev = evdev.InputDevice(path)
                caps = dev.capabilities()
                if e.EV_KEY not in caps: continue
                keys = caps[e.EV_KEY]
                if not all(sc in keys for sc in [30, 31, 32, 33, 28]): continue
                if "autolayout" in dev.name.lower(): continue
                candidates.append(dev)
            except: pass

        if not candidates: raise RuntimeError("Клавиатура не найдена!")
        best = max(candidates, key=lambda d: len(d.capabilities().get(e.EV_KEY, [])))
        log.info(f"✅ Устройство перехвачено: {best.name}")
        return best

    def run(self):
        self.device.grab()
        log.info("🚀 Пишите текст (Space / Enter триггерят проверку)")
        try:
            for event in self.device.read_loop():
                if self.is_correcting: continue
                self._handle(event)
        except KeyboardInterrupt: pass
        finally:
            try: self.device.ungrab()
            except: pass
            self.ui.close()
            self.device.close()

    def _emit(self, etype, code, value):
        self.ui.write(etype, code, value)
        self.ui.syn()

    def _pass_event(self, event):
        self.ui.write_event(event)
        self.ui.syn()

    def _handle(self, event):
        if event.type != e.EV_KEY:
            self._pass_event(event)
            return

        sc = event.code
        state = event.value

        if sc in SHIFT_SCANCODES:
            self.shift_pressed = (state != 0)
            self._pass_event(event)
            return

        if sc in {29, 97, 56, 100} or sc in MODIFIER_SCANCODES:
            if sc in {29, 97}: self.ctrl_pressed = (state != 0)
            if sc in {56, 100}: self.alt_pressed = (state != 0)
            if state == 1:
                self.modifier_used = True
                self.buffer.clear()
            self._pass_event(event)
            return

        if state != 1: 
            self._pass_event(event)
            return

        if self.ctrl_pressed or self.alt_pressed:
            self.buffer.clear()
            self.modifier_used = True
            self._pass_event(event)
            return

        if sc == BACKSPACE_SC:
            if self.buffer: self.buffer.pop()
            self._pass_event(event)
            return

        if sc in BOUNDARY_SCANCODES:
            if self.modifier_used or not self.buffer:
                self.buffer.clear()
                self.modifier_used = False
                self._pass_event(event)
                return

            self._check_and_correct(boundary_sc=sc)
            self.modifier_used = False
            return

        if sc in KEY_MAP:
            self.buffer.append(KeyStroke(sc, self.shift_pressed))
            self.modifier_used = False
            self._pass_event(event)
            return

        self.buffer.clear()
        self.modifier_used = False
        self._pass_event(event)

    def _check_and_correct(self, boundary_sc: int):
        raw_word = ''.join(ks.char for ks in self.buffer)
        has_mixed = any(ks.shifted for ks in self.buffer[1:])
        all_shifted = all(ks.shifted for ks in self.buffer)
        
        # Если слово вида camelCase - пропускаем
        if has_mixed and not all_shifted:
            self.buffer.clear()
            self._emit(e.EV_KEY, boundary_sc, 1)
            self._emit(e.EV_KEY, boundary_sc, 0)
            return

        target = SmartAnalyzer.analyze(raw_word)
        current = self.hypr.get_layout()

        if target and target != current:
            log.info(f"🔄 ИСПРАВЛЕНИЕ: '{raw_word}' → Раскладка {target.upper()}")
            self._execute_correction(target, boundary_sc)
        else:
            self.buffer.clear()
            self._emit(e.EV_KEY, boundary_sc, 1)
            self._emit(e.EV_KEY, boundary_sc, 0)

    def _execute_correction(self, lang: str, boundary_sc: int):
        self.is_correcting = True
        strokes = list(self.buffer)
        self.buffer.clear()

        try:
            if self.shift_pressed:
                self._emit(e.EV_KEY, e.KEY_LEFTSHIFT, 0)

            # 1. Стираем неверный текст
            for _ in range(len(strokes)):
                self._emit(e.EV_KEY, e.KEY_BACKSPACE, 1)
                self._emit(e.EV_KEY, e.KEY_BACKSPACE, 0)
                time.sleep(0.01)

            # 2. Переключаем язык
            self.hypr.switch_layout(lang)
            
            # ВАЖНО: Wayland нужно время, чтобы переключить layout для виртуальной клавиатуры
            time.sleep(0.15) 

            # 3. Печатаем заново
            for ks in strokes:
                if ks.shifted: self._emit(e.EV_KEY, e.KEY_LEFTSHIFT, 1)
                self._emit(e.EV_KEY, ks.scancode, 1)
                time.sleep(0.005)
                self._emit(e.EV_KEY, ks.scancode, 0)
                time.sleep(0.005)
                if ks.shifted: self._emit(e.EV_KEY, e.KEY_LEFTSHIFT, 0)

            if self.shift_pressed:
                self._emit(e.EV_KEY, e.KEY_LEFTSHIFT, 1)

            time.sleep(0.02)
            
            # 4. Нажимаем пробел (или Enter)
            self._emit(e.EV_KEY, boundary_sc, 1)
            self._emit(e.EV_KEY, boundary_sc, 0)

        finally:
            self.is_correcting = False


def main():
    check_hyprctl()
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))

    try:
        app = Autolayout()
        app.run()
    except Exception as ex:
        log.error(f"❌ Критическая ошибка: {ex}")
        sys.exit(1)

if __name__ == "__main__":
    main()