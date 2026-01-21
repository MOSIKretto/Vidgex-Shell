#!/usr/bin/env sh

sleep 0.5

if [ -z "$XDG_PICTURES_DIR" ]; then
    XDG_PICTURES_DIR="$HOME/Pictures"
fi

save_dir="${3:-$XDG_PICTURES_DIR/Screenshots}"
save_file=$(date +'%y%m%d_%Hh%Mm%Ss_screenshot.png')
full_path="$save_dir/$save_file"
mkdir -p "$save_dir"

mockup_mode="$2"

print_error() {
    cat <<EOF
    ./screenshot.sh <action> [mockup]
    ...valid actions are...
        p  : print selected screen
        s  : snip selected region
        w  : snip focused window
EOF
}

# Функция ожидания файла с увеличенным таймаутом
wait_for_file() {
    local file="$1"
    local timeout=30  # Увеличил таймаут
    local count=0
    
    while [ $count -lt $timeout ]; do
        if [ -f "$file" ] && [ -s "$file" ]; then
            # Проверяем, что файл не заблокирован (запись завершена)
            if ! lsof "$file" >/dev/null 2>&1; then
                sleep 0.2
                return 0
            fi
        fi
        sleep 0.2
        count=$((count + 1))
    done
    return 1
}

# Функция поиска созданного файла (fallback)
find_screenshot() {
    local dir="$1"
    local expected="$2"
    
    # Сначала проверяем ожидаемый файл
    if [ -f "$expected" ] && [ -s "$expected" ]; then
        echo "$expected"
        return 0
    fi
    
    # Ищем самый новый png файл за последнюю минуту
    local found
    found=$(find "$dir" -maxdepth 1 -name "*.png" -type f -mmin -1 -printf '%T@ %p\n' 2>/dev/null | \
            sort -rn | head -1 | cut -d' ' -f2-)
    
    if [ -n "$found" ] && [ -f "$found" ] && [ -s "$found" ]; then
        echo "$found"
        return 0
    fi
    
    return 1
}

copy_to_clipboard() {
    local file="$1"
    if command -v wl-copy >/dev/null 2>&1; then
        wl-copy < "$file"
    elif command -v xclip >/dev/null 2>&1; then
        xclip -selection clipboard -t image/png < "$file"
    fi
}

# Запоминаем время до скриншота
timestamp_before=$(date +%s)

case $1 in
    p)
        hyprshot -z -m output -o "$save_dir" -f "$save_file"
        screenshot_exit=$?
        ;;
    s)
        hyprshot -z -m region -o "$save_dir" -f "$save_file"
        screenshot_exit=$?
        ;;
    w)
        # Для window убираем -z флаг и добавляем небольшую задержку
        sleep 0.1
        hyprshot -m window -o "$save_dir" -f "$save_file"
        screenshot_exit=$?
        ;;
    *)
        print_error
        exit 1
        ;;
esac

# Даём время на запись
sleep 0.5

# Пробуем найти файл
actual_file=""

# Способ 1: Ждём ожидаемый файл
if wait_for_file "$full_path"; then
    actual_file="$full_path"
fi

# Способ 2: Ищем любой новый скриншот (fallback)
if [ -z "$actual_file" ]; then
    actual_file=$(find_screenshot "$save_dir" "$full_path")
fi

# Проверяем результат
if [ -z "$actual_file" ] || [ ! -f "$actual_file" ]; then
    # Проверяем код выхода - если пользователь отменил
    if [ $screenshot_exit -ne 0 ]; then
        notify-send -a "Vidgex-Shell" "Screenshot Aborted" "Cancelled by user"
    else
        notify-send -a "Vidgex-Shell" "Screenshot Failed" "File was not created (exit: $screenshot_exit)"
    fi
    exit 1
fi

# Обновляем full_path если нашли другой файл
full_path="$actual_file"

# Обработка mockup
if [ "$mockup_mode" = "mockup" ]; then
    temp_file="${full_path%.png}_temp.png"
    mockup_file="${full_path%.png}_mockup.png"
    mockup_success=true

    if ! magick "$full_path" \
        \( +clone -alpha extract -draw 'fill black polygon 0,0 0,20 20,0 fill white circle 20,20 20,0' \
           \( +clone -flip \) -compose Multiply -composite \
           \( +clone -flop \) -compose Multiply -composite \
        \) -alpha off -compose CopyOpacity -composite "$temp_file" 2>/dev/null; then
        mockup_success=false
    fi

    if [ "$mockup_success" = true ]; then
        if ! magick "$temp_file" \
            \( +clone -background black -shadow 60x20+0+10 -alpha set -channel A -evaluate multiply 1 +channel \) \
            +swap -background none -layers merge +repage "$mockup_file" 2>/dev/null; then
            mockup_success=false
        fi
    fi

    if [ "$mockup_success" = true ] && [ -f "$mockup_file" ]; then
        rm -f "$temp_file"
        mv "$mockup_file" "$full_path"
    else
        echo "Warning: Mockup processing failed, keeping original." >&2
        rm -f "$temp_file" "$mockup_file"
    fi
fi

# Копируем в буфер
copy_to_clipboard "$full_path"

# Уведомление
ACTION=$(notify-send -a "Vidgex-Shell" -i "$full_path" "Screenshot saved" "$full_path" \
    -A "view=View" -A "edit=Edit" -A "open=Open Folder")

case "$ACTION" in
    view) xdg-open "$full_path" ;;
    edit) swappy -f "$full_path" ;;
    open) xdg-open "$(dirname "$full_path")" ;;
esac