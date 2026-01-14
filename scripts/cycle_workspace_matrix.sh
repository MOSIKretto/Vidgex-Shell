#!/usr/bin/env bash

# Конфигурация сетки
ROWS=3
COLS=3

# Проверка аргументов
if [ "$#" -ne 1 ]; then
    exit 1
fi
DIRECTION=$1

# --- 1. Получение текущего Workspace ID (без JSON парсера) ---
# Мы берем вывод hyprctl, ищем строку "id": X, берем второе слово и удаляем запятую.
CURRENT_WS=$(hyprctl activeworkspace -j | grep '"id":' | head -n 1 | awk '{print $2}' | tr -d ',')

# Валидация и Fallback (как в Python скрипте: return 5)
# Если пусто или не число, ставим 5
if [[ -z "$CURRENT_WS" ]] || ! [[ "$CURRENT_WS" =~ ^[0-9]+$ ]]; then
    CURRENT_WS=5
fi

# Проверяем, входит ли workspace в наш диапазон 1-9. Если нет — сбрасываем на 5 (центр)
if (( CURRENT_WS < 1 || CURRENT_WS > 9 )); then
    CURRENT_WS=5
fi

# --- 2. Вычисление позиции в матрице (Row/Col) ---
# Поскольку матрица 1-9 последовательная, мы можем использовать математику
# вместо перебора массива.
# Приводим к 0-индексу для расчетов
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
        # Добавляем COLS перед модулем, чтобы избежать отрицательных чисел
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

# --- 4. Преобразование обратно в ID ---
# Формула: (Row * Cols) + Col + 1
NEXT_WS=$(( (NEXT_ROW * COLS) + NEXT_COL + 1 ))

# --- 5. Выполнение команд Hyprland ---
if [[ "$DIRECTION" == "nextU" ]] || [[ "$DIRECTION" == "nextD" ]]; then
    # Включаем вертикальную анимацию
    hyprctl keyword animation workspaces,1,6,overshot,slidevert > /dev/null
    
    # Переключаем воркспейс
    hyprctl dispatch workspace "$NEXT_WS" > /dev/null
    
    # Возвращаем обычную анимацию (slide)
    # Небольшая задержка не нужна, так как hyprctl обрабатывает команды последовательно,
    # но команда keyword применяется мгновенно.
    hyprctl keyword animation workspaces,1,6,overshot,slide > /dev/null
else
    # Обычное переключение
    hyprctl dispatch workspace "$NEXT_WS" > /dev/null
fi