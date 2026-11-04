# modules/Bar/QuickPanel/toolsTab.py
import os, subprocess, threading, time
from datetime import datetime
from pathlib import Path

from gi.repository import GLib
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label

import services.icons as icons

def _get_pictures_dir() -> Path:
    pic_dir = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
    if pic_dir: return Path(pic_dir)
    if "XDG_PICTURES_DIR" in os.environ: return Path(os.environ["XDG_PICTURES_DIR"])
    return Path.home() / "Pictures"
def _get_videos_dir() -> Path:
    vid_dir = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_VIDEOS)
    if vid_dir: return Path(vid_dir)
    if "XDG_VIDEOS_DIR" in os.environ: return Path(os.environ["XDG_VIDEOS_DIR"])
    return Path.home() / "Videos"
def _send_notification(summary: str, body: str = "", icon=None, actions=None) -> str:
    cmd = ["notify-send","-a","Vidgex-Shell",summary]
    if body: cmd.append(body)
    if icon: cmd.extend(["-i", str(icon)])
    if actions:
        for act_id, act_label in actions: cmd.extend(["-A", f"{act_id}={act_label}"])
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.stdout.strip()
def _copy_to_clipboard(file_path: Path):
    with open(file_path,"rb") as f: subprocess.run(["wl-copy"], stdin=f)
def _is_recording() -> bool:
    # Единственная точка проверки "идёт ли запись" — раньше один и тот
    # же subprocess.run(["pgrep", ...]) был продублирован здесь и в
    # ToolsTab._refresh_dyn().
    return subprocess.run(["pgrep", "-f", "gpu-screen-recorder"], capture_output=True).returncode == 0
def _run_ocr():
    time.sleep(0.5)
    p1 = subprocess.Popen(["hyprshot","-m","region","-z","-r","-s"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    p2 = subprocess.Popen(["tesseract","-l","eng+rus","-","-"], stdin=p1.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    p1.stdout.close()
    out,_ = p2.communicate()
    text = out.decode("utf-8").strip()
    subprocess.run(["wl-copy"], input=text.encode("utf-8"))
    _send_notification("OCR Success","Text Copied to Clipboard")
def _run_recording():
    save_dir = _get_videos_dir() / "Recordings"
    save_dir.mkdir(parents=True, exist_ok=True)
    if _is_recording():
        subprocess.run(["pkill","-SIGINT","-f","gpu-screen-recorder"]); time.sleep(1)
        mp4_files = list(save_dir.glob("*.mp4"))
        last_video = max(mp4_files, key=lambda f: f.stat().st_mtime) if mp4_files else None
        action = _send_notification("⬜ Recording stopped", actions=[("view","View"),("open","Open folder")])
        if action=="view" and last_video: subprocess.run(["xdg-open",str(last_video)])
        elif action in ("view","open"): subprocess.run(["xdg-open",str(save_dir)])
    else:
        output_file = save_dir / f"{datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}.mp4"
        _send_notification("🔴 Recording started")
        subprocess.Popen(["gpu-screen-recorder","-w","screen","-q","ultra","-a","default_output","-ac","opus","-cr","full","-f","60","-o",str(output_file)])
def _wait_for_file(file_path: Path, timeout: float = 6.0):
    count=0; max_steps=int(timeout/0.2)
    while count<max_steps:
        if file_path.is_file() and file_path.stat().st_size>0:
            lsof=subprocess.run(["lsof",str(file_path)], capture_output=True)
            if lsof.returncode!=0: time.sleep(0.2); return
        time.sleep(0.2); count+=1
def _apply_mockup(full_path: Path):
    temp_file=full_path.with_name(f"{full_path.stem}_temp.png"); mockup_file=full_path.with_name(f"{full_path.stem}_mockup.png")
    cmd1=["magick",str(full_path),"(","+clone","-alpha","extract","-draw","fill black polygon 0,0 0,20 20,0 fill white circle 20,20 20,0","("+ "+clone","-flip",")","-compose","Multiply","-composite","("+ "+clone","-flop",")","-compose","Multiply","-composite",")","-alpha","off","-compose","CopyOpacity","-composite",str(temp_file)]
    cmd2=["magick",str(temp_file),"("+ "+clone","-background","black","-shadow","60x20+0+10","-alpha","set","-channel","A","-evaluate","multiply","1","+channel",")","+swap","-background","none","-layers","merge","+repage",str(mockup_file)]
    subprocess.run(cmd1,capture_output=True); subprocess.run(cmd2,capture_output=True)
    mockup_file.replace(full_path); temp_file.unlink(missing_ok=True)
def _run_screenshot(mode: str, mockup: bool=False):
    time.sleep(0.5)
    save_dir=_get_pictures_dir()/ "Screenshots"; save_dir.mkdir(parents=True, exist_ok=True)
    save_file=f"{datetime.now().strftime('%y%m%d_%Hh%Mm%Ss')}_screenshot.png"; full_path=save_dir/save_file
    cmd=["hyprshot","-s"]
    if mode=="p": cmd+=["-z","-m","output","-o",str(save_dir),"-f",save_file]
    elif mode=="s": cmd+=["-z","-m","region","-o",str(save_dir),"-f",save_file]
    elif mode=="w": time.sleep(0.1); cmd+=["-m","window","-o",str(save_dir),"-f",save_file]
    subprocess.run(cmd); time.sleep(0.5); _wait_for_file(full_path)
    if mockup: _apply_mockup(full_path)
    _copy_to_clipboard(full_path)
    action=_send_notification("Screenshot saved",str(full_path),icon=full_path,actions=[("view","View"),("open","Open Folder")])
    if action=="view": subprocess.run(["xdg-open",str(full_path)])
    elif action=="open": subprocess.run(["xdg-open",str(full_path.parent)])

class ToolsTab(Box):
    _MENU_ITEMS = (
        (icons.ssregion,     ("screenshot","s"), "<b>Area screenshot</b>"),
        (icons.sswindow,     ("screenshot","w"), "<b>Window screenshot</b>"),
        (icons.ssfull,       ("screenshot","p"), "<b>Full screenshot</b>"),
        (icons.ocr,          ("ocr",),           "<b>OCR</b>"),
        (icons.screenrecord, ("record",),        "<b>Screen Recording</b>"),
    )
    CSS_BTN = "toolbox-icon-btn"
    CSS_ACTIVE = "active"
    def __init__(self, close_panel, **kwargs):
        super().__init__(name="tools-tab", orientation="v", spacing=0, v_expand=True, **kwargs)
        self._close_panel = close_panel
        self._dyn: dict[str, object] = {}
        # Разовые таймеры "обновить иконку записи" после клика по
        # Screen Recording — отслеживаются здесь и гарантированно
        # снимаются при разрушении виджета (см. _on_destroy), чтобы
        # колбэк не прилетел на уже уничтоженные Label/Button (п.4).
        self._pending_timers: set[int] = set()
        self.connect("destroy", self._on_destroy)
        row = Box(orientation="h", spacing=6, h_align="center", v_align="center")
        for icon_markup, action_tuple, tooltip in self._MENU_ITEMS:
            lbl = Label(markup=icon_markup or "")
            btn = Button(child=lbl, tooltip_markup=tooltip, can_focus=False)
            btn.get_style_context().add_class(self.CSS_BTN)
            is_record = action_tuple[0]=="record"
            btn.connect("clicked", self._on_action_clicked, action_tuple, is_record)
            if is_record:
                self._dyn["record"]=lbl; self._dyn["record_btn"]=btn
            row.add(btn)
        self.add(Box(orientation="v", v_expand=True))
        self.add(row)
        self.add(Box(orientation="v", v_expand=True))
    def activate(self) -> None:
        self._refresh_dyn()
    def deactivate(self) -> None: pass
    def _on_destroy(self, *_args) -> None:
        for tid in self._pending_timers:
            GLib.source_remove(tid)
        self._pending_timers.clear()
    def _schedule_refresh(self, delay_ms: int) -> None:
        holder: list = []
        def _fire():
            self._pending_timers.discard(holder[0])
            self._refresh_dyn()
            return False
        holder.append(GLib.timeout_add(delay_ms, _fire))
        self._pending_timers.add(holder[0])
    def _refresh_dyn(self, *_args) -> bool:
        is_rec = _is_recording()
        lbl=self._dyn.get("record")
        if lbl: lbl.set_markup((icons.stop if is_rec else icons.screenrecord) or "")
        btn=self._dyn.get("record_btn")
        if btn:
            ctx=btn.get_style_context()
            if is_rec: ctx.add_class(self.CSS_ACTIVE)
            else: ctx.remove_class(self.CSS_ACTIVE)
        return False
    def _dispatch_action(self, action_type_name: str, *args) -> None:
        if action_type_name=="ocr": threading.Thread(target=_run_ocr, daemon=True).start()
        elif action_type_name=="record": threading.Thread(target=_run_recording, daemon=True).start()
        elif action_type_name=="screenshot": threading.Thread(target=_run_screenshot, args=(args[0],), daemon=True).start()
    def _on_action_clicked(self, _button, action_tuple, is_record: bool) -> None:
        action_name,*args = action_tuple
        self._dispatch_action(action_name,*args)
        if is_record:
            for delay_ms in (100, 600, 1200):
                self._schedule_refresh(delay_ms)
        else:
            self._close_panel()