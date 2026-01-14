#!/usr/bin/env bash

# Конфигурация сетки
ROWS=3
COLS=3

# Проверка аргументов
if [ "$#" -ne 1 ]; then
    exit 1
fi
DIRECTION=$1

# --- 1. Получение текущего Workspace ID ---
# Используем grep/awk для извлечения ID активного воркспейса.
# Это работает быстрее, чем python/jq и не требует зависимостей.
CURRENT_WS=$(hyprctl activeworkspace -j | grep '"id":' | head -n 1 | awk '{print $2}' | tr -d ',')

# Валидация: если не число или пусто, ставим дефолт (5)
if [[ -z "$CURRENT_WS" ]] || ! [[ "$CURRENT_WS" =~ ^[0-9]+$ ]]; then
    CURRENT_WS=5
fi

# Если текущий воркспейс выходит за пределы сетки (например, scratchpad), сбрасываем на 5
if (( CURRENT_WS < 1 || CURRENT_WS > 9 )); then
    CURRENT_WS=5
fi

# --- 2. Вычисление текущей позиции (Row/Col) ---
# Индексы: 0..8
IDX=$((CURRENT_WS - 1))
CURRENT_ROW=$((IDX / COLS))
CURRENT_COL=$((IDX % COLS))

# --- 3. Вычисление следующей позиции ---
NEXT_ROW=$CURRENT_ROW
NEXT_COL=$CURRENT_COL

case $DIRECTION in
    "nextR")
        NEXT_COL=$(( (CURRENT_COL + 1) % COLS ))
        ;;
    "nextL")
        # Добавляем COLS, чтобы корректно обработать переход 0 -> 2
        NEXT_COL=$(( (CURRENT_COL - 1 + COLS) % COLS ))
        ;;
    "nextU")
        NEXT_ROW=$(( (CURRENT_ROW - 1 + ROWS) % ROWS ))
        ;;
    "nextD")
        NEXT_ROW=$(( (CURRENT_ROW + 1) % ROWS ))
        ;;
    *)
        exit 1
        ;;
esac

# --- 4. Преобразование позиции обратно в ID ---
# Формула: (Row * Cols) + Col + 1
NEXT_WS=$(( (NEXT_ROW * COLS) + NEXT_COL + 1 ))

# --- 5. Выполнение команд Hyprland ---
# Примечание: movetoworkspace перемещает активное окно на указанный WS

if [[ "$DIRECTION" == "nextU" ]] || [[ "$DIRECTION" == "nextD" ]]; then
    # Вертикальная анимация
    # Используем --batch для небольшой оптимизации, но разделяем команды анимации,
    # чтобы они применились в правильном порядке.
    
    # 1. Меняем анимацию на вертикальную
    hyprctl keyword animation workspaces,1,6,overshot,slidevert > /dev/null
    
    # 2. Перемещаем окно
    hyprctl dispatch movetoworkspace "$NEXT_WS" > /dev/null
    
    # 3. Возвращаем обычную анимацию
    hyprctl keyword animation workspaces,1,6,overshot,slide > /dev/null
else
    # Горизонтальное перемещение (без смены анимации)
    hyprctl dispatch movetoworkspace "$NEXT_WS" > /dev/null
fi