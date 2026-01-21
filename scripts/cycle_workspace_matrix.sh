#!/usr/bin/env bash

# Конфигурация сетки
COLS=3 ROWS=3

# Получаем ID текущего воркспейса.
# Используем встроенный regex BASH (быстрее внешних утилит).
# Если regex не совпал или ID вне диапазона 1-9 -> fallback на 5.
[[ $(hyprctl activeworkspace -j) =~ \"id\":\ *([0-9]+) ]] && ws=${BASH_REMATCH[1]} || ws=5
(( ws < 1 || ws > COLS * ROWS )) && ws=5

# Вычисляем текущие координаты (row, col) на основе 0-индексации
(( row = (ws - 1) / COLS, col = (ws - 1) % COLS ))

# Вычисляем новые координаты в зависимости от аргумента
case $1 in
    nextR) (( col = (col + 1) % COLS )) ;;        # Вправо (циклично в строке)
    nextL) (( col = (col + COLS - 1) % COLS )) ;; # Влево (циклично в строке)
    nextD) (( row = (row + 1) % ROWS )) ;;        # Вниз (циклично по столбцу)
    nextU) (( row = (row + ROWS - 1) % ROWS )) ;; # Вверх (циклично по столбцу)
    *) exit 1 ;;
esac

# Преобразуем координаты обратно в ID воркспейса
(( next = row * COLS + col + 1 ))

# Выполняем переход.
# exec заменяет текущий процесс скрипта, экономя ресурсы.
if [[ $1 == next[UD] ]]; then
    # Для вертикального движения меняем анимацию на slidevert и обратно одной батч-командой
    exec hyprctl --batch "keyword animation workspaces,1,6,overshot,slidevert; dispatch workspace $next; keyword animation workspaces,1,6,overshot,slide" >/dev/null
else
    # Обычное горизонтальное переключение
    exec hyprctl dispatch workspace "$next" >/dev/null
fi