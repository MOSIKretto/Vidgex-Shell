#!/usr/bin/env bash

# Читаем аргументы
ACTION=$1  # Какое действие: "workspace" или "movetoworkspace"
DIR=$2     # Направление: "nextR", "nextL", "nextU", "nextD"

# Защита от пустого запуска
if [[ -z $ACTION || -z $DIR ]]; then exit 1; fi

# Конфигурация сетки
COLS=3 ROWS=3

# 1. Получение ID текущего воркспейса
[[ $(hyprctl activeworkspace -j) =~ \"id\":\ *([0-9]+) ]] && ws=${BASH_REMATCH[1]} || ws=5
(( ws < 1 || ws > COLS * ROWS )) && ws=5

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

# 4. Обратно в ID
(( next = row * COLS + col + 1 ))

# 5. Выполнение команды
if [[ $DIR == next[UD] ]]; then
    # Для вертикали: меняем анимацию -> выполняем действие -> возвращаем анимацию
    exec hyprctl --batch "keyword animation workspaces,1,6,overshot,slidevert; dispatch $ACTION $next; keyword animation workspaces,1,6,overshot,slide" >/dev/null
else
    # Горизонталь: просто выполняем действие
    exec hyprctl dispatch "$ACTION" "$next" >/dev/null
fi