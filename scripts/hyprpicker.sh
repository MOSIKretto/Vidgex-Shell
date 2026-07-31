#!/usr/bin/env bash
sleep 0.5

ICON_PATH="/tmp/picked_color.png"

pick_color() {
    local format="$1"
    local title="$2"
    local color=""
    local full_color=""

    color=$(hyprpicker -n -f "$format")

    if [[ -z "$color" ]]; then
        notify-send -a "Vidgex-Shell" "Color Picker Canceled" "No color selected"
        exit 1
    fi

    if [[ "$format" == "hex" ]]; then
        full_color="$color"
    else
        full_color="${format}(${color})"
    fi

    echo -n "$full_color" | wl-copy

    magick -size 64x64 "xc:${full_color}" "$ICON_PATH" 2>/dev/null

    if [[ -f "$ICON_PATH" ]]; then
        notify-send -a "Vidgex-Shell" -i "$ICON_PATH" "${title} Success" "${full_color} Copied to Clipboard"
    else
        notify-send -a "Vidgex-Shell" "${title} Success" "${full_color} Copied to Clipboard"
    fi
}

case "$1" in
-rgb)
    pick_color "rgb" "RGB"
    ;;
-hsv)
    pick_color "hsv" "HSV"
    ;;
-hex)
    pick_color "hex" "HEX"
    ;;
*)
    echo "Usage: $0 [-rgb|-hex|-hsv]"
    exit 1
    ;;
esac