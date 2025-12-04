from gi.repository import Gdk

import subprocess
import json


def get_current_workspace():
    try:
        result = subprocess.run(
            ["hyprctl", "activeworkspace"],
            capture_output=True,
            text=True
        )
        parts = result.stdout.split()
        for i, part in enumerate(parts):
            if part == "ID" and i + 1 < len(parts):
                return int(parts[i+1])
    except Exception as e:
        print(f"Error getting current workspace: {e}")
    return -1

def get_screen_dimensions():
    try:
        workspace_id = get_current_workspace()
        
        result = subprocess.run(
            ["hyprctl", "-j", "monitors"],
            capture_output=True,
            text=True
        )
        monitors = json.loads(result.stdout)
        
        for monitor in monitors:
            workspace_data = monitor.get("activeWorkspace", {})
            if workspace_data.get("id") == workspace_id:
                width = monitor.get("width", Gdk.Screen.get_default().get_width())
                height = monitor.get("height", Gdk.Screen.get_default().get_height())
                return width, height
                
        if monitors:
            first_monitor = monitors[0]
            width = first_monitor.get("width", Gdk.Screen.get_default().get_width())
            height = first_monitor.get("height", Gdk.Screen.get_default().get_height())
            return width, height
    except Exception as e:
        print(f"Error getting screen dimensions: {e}")
    
    return Gdk.Screen.get_default().get_width(), Gdk.Screen.get_default().get_height()

def check_occlusion(occlusion_region, workspace=None):
    if workspace is None:
        workspace = get_current_workspace()
    
    if isinstance(occlusion_region, tuple) and len(occlusion_region) == 2:
        side, size = occlusion_region
        if isinstance(side, str):
            screen_width, screen_height = get_screen_dimensions()
            
            if side.lower() == "bottom":
                x = 0
                y = screen_height - size
                width = screen_width
                height = size
                occlusion_region = (x, y, width, height)
            elif side.lower() == "top":
                occlusion_region = (0, 0, screen_width, size)
            elif side.lower() == "left":
                occlusion_region = (0, 0, size, screen_height)
            elif side.lower() == "right":
                x = screen_width - size
                occlusion_region = (x, 0, size, screen_height)
    
    if not isinstance(occlusion_region, tuple) or len(occlusion_region) != 4:
        print(f"Invalid occlusion region format: {occlusion_region}")
        return False

    try:
        result = subprocess.run(
            ["hyprctl", "-j", "clients"],
            capture_output=True,
            text=True
        )
        clients = json.loads(result.stdout)
    except Exception as e:
        print(f"Error retrieving client windows: {e}")
        return False

    occ_x, occ_y, occ_width, occ_height = occlusion_region
    occ_x2 = occ_x + occ_width
    occ_y2 = occ_y + occ_height

    screen_width, screen_height = get_screen_dimensions()

    for client in clients:
        if not client.get("mapped", False):
            continue

        client_workspace = client.get("workspace", {})
        if client_workspace.get("id") != workspace:
            continue

        position = client.get("at")
        size = client.get("size")
        if not position or not size:
            continue

        x, y = position
        width, height = size
        win_x1, win_y1 = x, y
        win_x2, win_y2 = x + width, y + height

        if width == screen_width and height == screen_height and x == 0 and y == 0:
            if occ_y == 0 and occ_height > 0:
                return True

        overlap_x = not (win_x2 <= occ_x or win_x1 >= occ_x2)
        overlap_y = not (win_y2 <= occ_y or win_y1 >= occ_y2)
        if overlap_x and overlap_y:
            return True

    return False