#!/usr/bin/env bash

ACTION=$1   # Какое действие: "workspace" или "movetoworkspace"
TARGET=$2   # Целевой рабочий стол: 1-9

# Проверка аргументов
if [[ ! $TARGET =~ ^[1-9]$ ]] || [[ -z $ACTION ]]; then exit 1; fi

# Задержка между шагами анимации (в секундах).
ANIM_DELAY=0.15

# 1. Получаем текущий рабочий стол
[[ $(hyprctl activeworkspace -j) =~ \"id\":\ *([0-9]+) ]] && CUR=${BASH_REMATCH[1]} || CUR=5
(( CUR < 1 || CUR > 9 )) && CUR=5

# Если мы уже на нужном столе - ничего не делаем
if (( CUR == TARGET )); then exit 0; fi

# 2. Переводим столы в координаты сетки 3x3 (от 0 до 2)
(( c_r = (CUR - 1) / 3, c_c = (CUR - 1) % 3 ))
(( t_r = (TARGET - 1) / 3, t_c = (TARGET - 1) % 3 ))

STEPS=()
r=$c_r
c=$c_c

# 3. Вычисляем путь 
if (( RANDOM % 2 == 0 )); then
    # Сначала по горизонтали
    if (( t_c > c )); then step_c=1; else step_c=-1; fi
    while (( c != t_c )); do
        (( c += step_c ))
        (( ws = r * 3 + c + 1 ))
        STEPS+=("H:$ws")
    done
    # Затем по вертикали
    if (( t_r > r )); then step_r=1; else step_r=-1; fi
    while (( r != t_r )); do
        (( r += step_r ))
        (( ws = r * 3 + c + 1 ))
        STEPS+=("V:$ws")
    done
else
    # Сначала по вертикали
    if (( t_r > r )); then step_r=1; else step_r=-1; fi
    while (( r != t_r )); do
        (( r += step_r ))
        (( ws = r * 3 + c + 1 ))
        STEPS+=("V:$ws")
    done
    # Затем по горизонтали
    if (( t_c > c )); then step_c=1; else step_c=-1; fi
    while (( c != t_c )); do
        (( c += step_c ))
        (( ws = r * 3 + c + 1 ))
        STEPS+=("H:$ws")
    done
fi

# 4. Выполняем шаги с нужными анимациями
for step in "${STEPS[@]}"; do
    dir=${step%:*}
    ws=${step#*:}
    
    if [[ $dir == "V" ]]; then
        # Вертикальный шаг
        hyprctl --batch "keyword animation workspaces,1,6,overshot,slidevert; dispatch $ACTION $ws; keyword animation workspaces,1,6,overshot,slide" >/dev/null
    else
        # Горизонтальный шаг
        hyprctl dispatch "$ACTION" "$ws" >/dev/null
    fi
    
    sleep $ANIM_DELAY
done