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
    result = subprocess.run(
        ['hyprctl', 'activeworkspace', '-j'], 
        capture_output=True, 
        text=True
    )
    
    if result.returncode == 0 and result.stdout.strip():
        return json.loads(result.stdout)['id']
    return 5  # Default fallback

def find_workspace_position(ws_id):
    for row_idx, row in enumerate(MATRIX):
        try:
            col_idx = row.index(ws_id)
            return row_idx, col_idx
        except ValueError:
            continue
    return 1, 1  # Default fallback

if __name__ == '__main__':
    if len(sys.argv) != 2: 
        sys.exit(1)
    
    direction = sys.argv[1]
    current_ws = get_current_workspace()
    current_row, current_col = find_workspace_position(current_ws)
    
    # Calculate next workspace
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
        subprocess.run(['hyprctl', 'keyword', 'animation workspaces,1,6,overshot,slidevert'])
        subprocess.run(['hyprctl', 'dispatch', 'workspace', str(next_ws)])
        subprocess.run(['hyprctl', 'keyword', 'animation workspaces,1,6,overshot,slide'])
    else:
        subprocess.run(['hyprctl', 'dispatch', 'workspace', str(next_ws)])
