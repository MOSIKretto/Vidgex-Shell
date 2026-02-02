import cv2
import numpy as np
import onnxruntime as ort
import urllib.request
import os
import time
import signal
from collections import deque

from fabric.utils.helpers import exec_shell_command_async


class SwipeDirection:
    __slots__ = ()
    UP = 'up'
    DOWN = 'down'
    LEFT = 'left'
    RIGHT = 'right'


class GestureCrosspad:
    __slots__ = (
        'smooth_lm', 'running', 'state', 'center_pos', 'current_pos',
        'activation_start', 'last_action_time', 'last_direction',
        'position_history', 'no_hand_frames', 'commands', 'palm_sess',
        'hand_sess', '_palm_buf', '_hand_buf', '_sq_buf', '_frame_skip',
        '_w', '_h', '_scale_w', '_scale_h', '_models_dir'
    )
    
    # Константы как class-level для избежания dict lookup
    PALM_SIZE = 192
    HAND_SIZE = 224
    PALM_THRESH = 0.5
    HAND_THRESH = 0.5
    
    TRIGGER_DIST_SQ = 10000  # 100^2
    ACTIVATION_DIST_SQ = 3600  # 60^2
    ACTION_COOLDOWN = 0.35
    ACTIVATION_TIME = 0.5
    LOST_TOLERANCE = 15
    
    # Для кулака: максимум 1 открытый палец (вместо MIN_FINGERS_OPEN = 5)
    MAX_FINGERS_FIST = 1
    
    FRAME_W = 320
    FRAME_H = 240
    
    MODELS_DIR = os.path.expanduser('~/.config/Vidgex-Shell/scripts/eyes-hands')
    
    def __init__(self, commands: dict = None):
        self.smooth_lm = None
        self.running = True
        
        self.state = 0
        self.center_pos = None
        self.current_pos = None
        self.activation_start = 0.0
        self.last_action_time = 0.0
        self.last_direction = None
        
        self.position_history = deque(maxlen=3)
        self.no_hand_frames = 0
        self._frame_skip = 0
        
        self._models_dir = self.MODELS_DIR
        
        self._palm_buf = np.empty((1, 3, self.PALM_SIZE, self.PALM_SIZE), dtype=np.float32)
        self._hand_buf = np.empty((1, 3, self.HAND_SIZE, self.HAND_SIZE), dtype=np.float32)
        self._sq_buf = np.zeros((300, 300, 3), dtype=np.uint8)
        
        self._w = self.FRAME_W
        self._h = self.FRAME_H
        self._scale_w = 1.0
        self._scale_h = 1.0
        
        default_script = os.path.expanduser('~/.config/Vidgex-Shell/scripts/cycle_workspace_matrix.sh')
        self.commands = commands or {
            SwipeDirection.RIGHT: f'{default_script} nextL',
            SwipeDirection.LEFT: f'{default_script} nextR',
            SwipeDirection.UP: f'{default_script} nextD',
            SwipeDirection.DOWN: f'{default_script} nextU',
        }
        
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        self.palm_sess = None
        self.hand_sess = None
    
    def _signal_handler(self, signum, frame):
        self.running = False
    
    def _init_models(self):
        """Ленивая инициализация моделей"""
        if self.palm_sess is not None:
            return
        
        os.makedirs(self._models_dir, exist_ok=True)
        
        palm_file = "palm_detection_full_inf_post_192x192.onnx"
        hand_file = "hand_landmark_sparse_Nx3x224x224.onnx"
        base_url = "https://github.com/PINTO0309/hand-gesture-recognition-using-onnx/raw/main/model/"
        
        palm_path = os.path.join(self._models_dir, palm_file)
        hand_path = os.path.join(self._models_dir, hand_file)
        
        for local_path, url_suffix in [
            (palm_path, f"palm_detection/{palm_file}"),
            (hand_path, f"hand_landmark/{hand_file}")
        ]:
            if not os.path.exists(local_path) or os.path.getsize(local_path) < 10000:
                print(f"Downloading model to {local_path}...")
                urllib.request.urlretrieve(base_url + url_suffix, local_path)

        opts = ort.SessionOptions()
        opts.log_severity_level = 4
        opts.intra_op_num_threads = 2
        opts.inter_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.enable_cpu_mem_arena = True
        opts.enable_mem_pattern = True
        opts.enable_mem_reuse = True
        
        providers = ['CPUExecutionProvider']
        self.palm_sess = ort.InferenceSession(palm_path, opts, providers=providers)
        self.hand_sess = ort.InferenceSession(hand_path, opts, providers=providers)

    def _preprocess_inplace(self, img, buf, size):
        """Препроцессинг с использованием предаллоцированного буфера"""
        resized = cv2.resize(img, (size, size), interpolation=cv2.INTER_NEAREST)
        cv2.cvtColor(resized, cv2.COLOR_BGR2RGB, dst=resized)
        np.multiply(resized, 1.0/255.0, out=buf[0].transpose(1, 2, 0), casting='unsafe')
        return buf

    @staticmethod
    def _dist_sq(p1, p2):
        """Квадрат расстояния - избегаем sqrt"""
        dx = p1[0] - p2[0]
        dy = p1[1] - p2[1]
        return dx * dx + dy * dy

    def _count_extended_fingers(self, lm):
        """Подсчет открытых пальцев"""
        wrist = lm[0]
        count = 0
        
        # Для кулака используем более строгий порог
        tips = (8, 12, 16, 20)
        pips = (6, 10, 14, 18)
        thresh_sq = 1.21  # 1.1^2 - более строгий порог для определения открытого пальца
        
        for tip, pip in zip(tips, pips):
            if self._dist_sq(lm[tip], wrist) > self._dist_sq(lm[pip], wrist) * thresh_sq:
                count += 1
        
        # Большой палец - проверка что он торчит в сторону
        if self._dist_sq(lm[4], lm[2]) > self._dist_sq(lm[3], lm[2]) * 1.15:
            count += 1
        
        return count
    
    def _is_valid_fist_shape(self, lm):
        """Проверка формы кулака - другие пропорции чем у открытой ладони"""
        palm_w_sq = self._dist_sq(lm[5], lm[17])
        
        # Для кулака берём расстояние до MCP среднего пальца (основание пальца)
        # вместо кончика, так как пальцы согнуты
        hand_l_sq = self._dist_sq(lm[0], lm[9])  # lm[9] = MCP среднего пальца
        
        if palm_w_sq < 100 or hand_l_sq < 64:  # 8^2 - кулак компактнее
            return False
        
        # Для кулака соотношение ближе к 1 (более квадратная форма)
        ratio_sq = hand_l_sq / palm_w_sq
        return 0.25 < ratio_sq < 6.25  # 0.5^2 и 2.5^2
    
    def _detect_fist(self, frame):
        """Детекция кулака вместо открытой ладони"""
        h, w = self._h, self._w
        
        palm_input = self._preprocess_inplace(frame, self._palm_buf, self.PALM_SIZE)
        palm_out = self.palm_sess.run(None, {'input': palm_input})[0]
        
        if palm_out is None or palm_out.size == 0:
            return None
        
        palm_out = palm_out.reshape(-1, palm_out.shape[-1]) if palm_out.ndim == 1 else palm_out
        
        mask = palm_out[:, 0] > self.PALM_THRESH
        if not np.any(mask):
            return None
        
        valid = palm_out[mask]
        
        best_result = None
        best_score = float('inf')  # Ищем минимум пальцев (лучший кулак)
        
        for palm in valid:
            size_norm = palm[3]
            
            if not (0.03 < size_norm < 0.7):
                continue
            
            cx = int(palm[1] * w)
            cy = int(palm[2] * h)
            
            roi = max(80, int(size_norm * max(w, h) * 3.0))
            half = roi >> 1
            
            x1 = max(0, cx - half)
            y1 = max(0, cy - half)
            x2 = min(w, cx + half)
            y2 = min(h, cy + half)
            
            if x2 <= x1 or y2 <= y1:
                continue
            
            crop = frame[y1:y2, x1:x2]
            ch, cw = crop.shape[:2]
            ms = max(ch, cw)
            
            if ms > self._sq_buf.shape[0]:
                self._sq_buf = np.zeros((ms, ms, 3), dtype=np.uint8)
            
            sq = self._sq_buf[:ms, :ms]
            sq.fill(0)
            
            ox = (ms - cw) >> 1
            oy = (ms - ch) >> 1
            sq[oy:oy+ch, ox:ox+cw] = crop
            
            hand_input = self._preprocess_inplace(sq, self._hand_buf, self.HAND_SIZE)
            outs = self.hand_sess.run(None, {'input': hand_input})
            
            if len(outs) < 2 or float(outs[1].flat[0]) < self.HAND_THRESH:
                continue
            
            raw = outs[0].ravel()
            if len(raw) < 63:
                continue
            
            pts = raw[:63].reshape(21, 3)[:, :2]
            mx_val = np.abs(pts).max()
            scale = ms / self.HAND_SIZE if mx_val > 1 else ms
            
            lm = pts * scale
            lm[:, 0] += x1 - ox
            lm[:, 1] += y1 - oy
            
            # Проверка формы кулака
            if not self._is_valid_fist_shape(lm):
                continue
            
            fingers = self._count_extended_fingers(lm)
            
            # ИЗМЕНЕНО: кулак = мало открытых пальцев (0 или 1)
            if fingers <= self.MAX_FINGERS_FIST and fingers < best_score:
                best_score = fingers
                best_result = (cx, cy, lm.astype(np.int16))
        
        if best_result is None:
            return None
        
        cx, cy, lm = best_result
        
        if self.smooth_lm is not None:
            np.add(lm * 0.4, self.smooth_lm * 0.6, out=lm, casting='unsafe')
        self.smooth_lm = lm
        
        center = np.array([cx * self._scale_w, cy * self._scale_h], dtype=np.float32)
        
        self.position_history.append(center)
        if len(self.position_history) >= 2:
            center = np.mean(self.position_history, axis=0)
        
        return center
    
    def _get_direction(self, center, current):
        dx = current[0] - center[0]
        dy = current[1] - center[1]
        dist_sq = dx * dx + dy * dy
        
        if dist_sq < self.TRIGGER_DIST_SQ:
            return None
        
        if abs(dx) > abs(dy):
            return SwipeDirection.RIGHT if dx > 0 else SwipeDirection.LEFT
        return SwipeDirection.DOWN if dy > 0 else SwipeDirection.UP
    
    def _process(self, hand_pos):
        self.current_pos = hand_pos
        self.no_hand_frames = 0
        now = time.monotonic()
        
        if self.state == 0:  # idle
            self.center_pos = hand_pos.copy()
            self.activation_start = now
            self.state = 1
            return None
        
        if self.state == 1:  # activating
            if self._dist_sq(hand_pos, self.center_pos) > self.ACTIVATION_DIST_SQ:
                self.center_pos = hand_pos.copy()
                self.activation_start = now
                return None
            
            if (now - self.activation_start) >= self.ACTIVATION_TIME:
                self.state = 2
                self.center_pos = hand_pos.copy()
            return None
        
        # state == 2 (active)
        direction = self._get_direction(self.center_pos, hand_pos)
        
        if direction is None:
            self.last_direction = None
            return None
        
        if (now - self.last_action_time) < self.ACTION_COOLDOWN:
            return None
        
        if direction != self.last_direction:
            self.last_action_time = now
            self.last_direction = direction
            self.center_pos = hand_pos.copy()
            return direction
        
        return None
    
    def _handle_no_hand(self):
        self.no_hand_frames += 1
        if self.no_hand_frames > self.LOST_TOLERANCE:
            self._reset_state()
    
    def _reset_state(self):
        self.state = 0
        self.center_pos = None
        self.current_pos = None
        self.last_direction = None
        self.activation_start = 0.0
        self.position_history.clear()
        self.smooth_lm = None
    
    def _execute(self, direction):
        cmd = self.commands.get(direction)
        if cmd:
            exec_shell_command_async(os.path.expanduser(cmd))
    
    def run(self):
        self._init_models()
        
        cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
        if not cap.isOpened():
            cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            return
        
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_FPS, 15)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        
        self._scale_w = 640 / self.FRAME_W
        self._scale_h = 480 / self.FRAME_H
        
        frame_small = np.empty((self.FRAME_H, self.FRAME_W, 3), dtype=np.uint8)
        
        try:
            while self.running:
                ret, frame = cap.read()
                if not ret:
                    continue
                
                self._frame_skip += 1
                if self.state == 0 and self._frame_skip < 2:
                    continue
                self._frame_skip = 0
                
                cv2.resize(frame, (self.FRAME_W, self.FRAME_H), dst=frame_small, interpolation=cv2.INTER_NEAREST)
                cv2.flip(frame_small, 1, dst=frame_small)
                
                # ИЗМЕНЕНО: детектируем кулак вместо ладони
                result = self._detect_fist(frame_small)
                
                if result is not None:
                    direction = self._process(result)
                    if direction:
                        self._execute(direction)
                else:
                    self._handle_no_hand()
                
                time.sleep(0.025 if self.state == 2 else 0.04)
        finally:
            cap.release()


def start_recognition():
    script = os.path.expanduser('~/.config/Vidgex-Shell/scripts/cycle_workspace_matrix.sh')
    
    commands = {
        SwipeDirection.RIGHT: f'{script} nextL',
        SwipeDirection.LEFT: f'{script} nextR',
        SwipeDirection.UP: f'{script} nextD',
        SwipeDirection.DOWN: f'{script} nextU',
    }
    
    GestureCrosspad(commands).run()