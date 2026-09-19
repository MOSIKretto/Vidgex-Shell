#!/usr/bin/env bash

# Лог дебага (раскомментируйте строку ниже, чтобы смотреть логи в реальном времени через tail -f /tmp/matrix.log)
# exec > >(tee -i /tmp/matrix.log) 2>&1

ACTION=$1  # "workspace" или "movetoworkspace"
DIR=$2     # "nextR", "nextL", "nextU", "nextD"

if [[ -z $ACTION || -z $DIR ]]; then exit 1; fi

COLS=3
ROWS=3

# 1. Получение ID текущего воркспейса через встроенный Lua-вызов
ws=$(hyprctl repl 'hl.get_active_workspace().id' | tr -d '\n' | grep -oE '[0-9]+')
ws=${ws:-5}

if (( ws < 1 || ws > COLS * ROWS )); then ws=5; fi

# 2. Вычисление координат (0-индекс)
(( row = (ws - 1) / COLS, col = (ws - 1) % COLS ))

# 3. Вычисление новой позиции
case $DIR in
    nextR) (( col = (col + 1) % COLS )) ;;        # Вправо
    nextL) (( col = (col + COLS - 1) % COLS )) ;; # Влево
    nextD) (( row = (row + 1) % ROWS )) ;;        # Вниз
    nextU) (( row = (row + ROWS - 1) % ROWS )) ;; # Вверх
    *) exit 1 ;;
esac

# 4. Обратно в ID воркспейса
(( next = row * COLS + col + 1 ))

# 5. Выполнение смены воркспейса/переноса окна и анимаций
if [[ $DIR == next[UD] ]]; then
    # Включаем вертикальный сдвиг для всех типов анимаций воркспейсов
    hyprctl eval '
        hl.animation({ leaf = "workspaces", enabled = true, speed = 6, bezier = "overshot", style = "slidevert" })
        hl.animation({ leaf = "workspacesIn", enabled = true, speed = 6, bezier = "overshot", style = "slidevert" })
        hl.animation({ leaf = "workspacesOut", enabled = true, speed = 6, bezier = "overshot", style = "slidevert" })
    '
    
    # Ждем, пока композитор применит стили в памяти
    sleep 0.05
    
    # Выполняем действие
    if [[ $ACTION == "movetoworkspace" ]]; then
        hyprctl dispatch "hl.dsp.window.move({ workspace = \"$next\" })"
    else
        hyprctl dispatch "hl.dsp.focus({ workspace = \"$next\" })"
    fi
    
    # Возвращаем горизонтальный сдвиг обратно
    hyprctl eval '
        hl.animation({ leaf = "workspaces", enabled = true, speed = 6, bezier = "overshot", style = "slide" })
        hl.animation({ leaf = "workspacesIn", enabled = true, speed = 6, bezier = "overshot", style = "slide" })
        hl.animation({ leaf = "workspacesOut", enabled = true, speed = 6, bezier = "overshot", style = "slide" })
    '
else
    # Горизонтальное перемещение / перенос окна
    if [[ $ACTION == "movetoworkspace" ]]; then
        hyprctl dispatch "hl.dsp.window.move({ workspace = \"$next\" })"
    else
        hyprctl dispatch "hl.dsp.focus({ workspace = \"$next\" })"
    fi
fi
