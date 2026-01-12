import subprocess
import sys
import json


MATRIX = [
    [1, 2, 3],
    [4, 5, 6],
    [7, 8, 9]
]

ROWS = 3
COLS = 3

def get_current_workspace():
    try:
        result = subprocess.run(
            ['hyprctl', 'activeworkspace', '-j'], 
            capture_output=True, 
            text=True,
            check=False
        )
        
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)['id']
    except (subprocess.SubprocessError, json.JSONDecodeError, KeyError):
        pass
    return None

def find_workspace_position(ws_id):
    for row_idx, row in enumerate(MATRIX):
        try:
            col_idx = row.index(ws_id)
            return row_idx, col_idx
        except ValueError:
            continue
    return None, None

if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(1)
    
    direction = sys.argv[1]
    current_ws = get_current_workspace() or 5
    current_row, current_col = find_workspace_position(current_ws)
    
    if current_row is None:
        current_row, current_col = 1, 1
        current_ws = 5
    
    # Calculate next workspace position based on direction
    if direction == "nextR":
        next_col = (current_col + 1) % COLS
        next_row = current_row
        
    elif direction == "nextL":
        next_col = (current_col - 1) % COLS
        next_row = current_row

    elif direction == "nextU":
        next_row = (current_row - 1) % ROWS
        next_col = current_col
        
    elif direction == "nextD":
        next_row = (current_row + 1) % ROWS
        next_col = current_col

    else:
        sys.exit(1)
    
    next_ws = MATRIX[next_row][next_col]
    if direction in ["nextU", "nextD"]:
        subprocess.run(['hyprctl', 'keyword', 'animation workspaces,1,6,overshot,slidevert'], check=False)
        subprocess.run(['hyprctl', 'dispatch', 'workspace', str(next_ws)], check=False)
        subprocess.run(['hyprctl', 'keyword', 'animation workspaces,1,6,overshot,slide'], check=False)
    else:
        subprocess.run(['hyprctl', 'dispatch', 'workspace', str(next_ws)], check=False)
