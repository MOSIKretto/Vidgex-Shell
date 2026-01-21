#!/usr/bin/env bash

# Конфигурация
COLS=3 ROWS=3

# 1. Получение ID.
# Используем встроенный regex BASH (быстрее внешних утилит).
# Если regex не нашел ID, считаем, что ws=5.
[[ $(hyprctl activeworkspace -j) =~ \"id\":\ *([0-9]+) ]] && ws=${BASH_REMATCH[1]} || ws=5

# Проверка границ (если мы на scratchpad или workspace > 9, сбрасываем на центр)
(( ws < 1 || ws > COLS * ROWS )) && ws=5

# 2. Вычисление координат (0-индекс)
(( row = (ws - 1) / COLS, col = (ws - 1) % COLS ))

# 3. Вычисление новой позиции
case $1 in
    nextR) (( col = (col + 1) % COLS )) ;;        # Вправо
    nextL) (( col = (col + COLS - 1) % COLS )) ;; # Влево
    nextD) (( row = (row + 1) % ROWS )) ;;        # Вниз
    nextU) (( row = (row + ROWS - 1) % ROWS )) ;; # Вверх
    *) exit 1 ;;
esac

# 4. Обратно в ID
(( next = row * COLS + col + 1 ))

# 5. Выполнение (movetoworkspace перемещает окно)
# Используем exec для замены процесса оболочки процессом hyprctl
if [[ $1 == next[UD] ]]; then
    # Вертикаль: меняем анимацию -> двигаем окно -> возвращаем анимацию (в одном запросе)
    exec hyprctl --batch "\
keyword animation workspaces,1,6,overshot,slidevert;\
dispatch movetoworkspace $next;\
keyword animation workspaces,1,6,overshot,slide" >/dev/null
else
    # Горизонталь: просто двигаем
    exec hyprctl dispatch movetoworkspace "$next" >/dev/null
fi