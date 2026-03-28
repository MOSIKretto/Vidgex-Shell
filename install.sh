#!/bin/bash

set -e          # Exit immediately if a command fails
set -u          # Treat unset variables as errors
set -o pipefail # Prevent errors in a pipeline from being masked

# ═══════════════════════════════════════════════════════════════════════════════
# ЦВЕТА И СТИЛИ
# ═══════════════════════════════════════════════════════════════════════════════
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
GRAY='\033[0;90m'
NC='\033[0m' # No Color
BOLD='\033[1m'

LIGHT_CYAN='\033[1;36m'
LIGHT_PURPLE='\033[1;35m'
LIGHT_BLUE='\033[1;34m'

# ═══════════════════════════════════════════════════════════════════════════════
# КОНФИГУРАЦИЯ
# ═══════════════════════════════════════════════════════════════════════════════
INSTALL_DIR="$HOME/.config/Vidgex-Shell"
REPO_URL="https://github.com/MOSIKretto/Vidgex-Shell.git"
REPO_BRANCH="main"
HYPRLAND_CONF="$HOME/.config/hypr/hyprland.conf"
LANG_CHOICE="EN"

AUTOLAYOUT_SCRIPT="$INSTALL_DIR/autolayout.py"
AUTOLAYOUT_SERVICE="$HOME/.config/systemd/user/autolayout.service"

PACKAGES=(
  hyprland
  hypridle
  hyprpicker
  hyprshot
  hyprsunset

  # Fabric
  fabric-cli-git
  python-fabric-git

  # GTK/GObject
  gobject-introspection
  python-gobject
  vte3

  # Утилиты
  awww-git
  brightnessctl
  cliphist
  libnotify
  swappy
  gpu-screen-recorder
  tesseract
  tesseract-data-eng
  tesseract-data-rus
  gnome-bluetooth-3.0
  playerctl
  power-profiles-daemon
  upower
  gray-git

  # Python зависимости
  python-setproctitle
  python-requests
  python-psutil
  python-numpy
  python-pillow
  python-opencv
  python-pywayland
  python-onnxruntime-cpu
  python-dbus
  python-evdev
  python-mutagen
  python-materialyoucolor
  python-qrcode
)

# ═══════════════════════════════════════════════════════════════════════════════
# ЛОКАЛИЗАЦИЯ
# ═══════════════════════════════════════════════════════════════════════════════

declare -A MSG_RU=(
  ["welcome"]="Добро пожаловать в установщик"
  ["invalid_choice"]="Неверный выбор. Используется English."
  ["not_root"]="Пожалуйста, не запускайте этот скрипт от имени root."
  ["checking_git"]="Проверка наличия git..."
  ["installing_git"]="Установка git..."
  ["cloning_repo"]="Клонирование репозитория Vidgex-Shell..."
  ["repo_exists"]="Репозиторий уже существует."
  ["update_repo"]="Обновить существующий репозиторий? [y/N]"
  ["updating_repo"]="Обновление репозитория..."
  ["repo_updated"]="Репозиторий обновлён!"
  ["repo_skipped"]="Обновление пропущено."
  ["repo_cloned"]="Репозиторий успешно клонирован!"
  ["checking_aur"]="Проверка AUR-хелпера..."
  ["installing_yay"]="Установка yay-bin..."
  ["using_helper"]="Используется AUR-хелпер:"
  ["installing_packages"]="Установка пакетов (Hyprland + зависимости)..."
  ["installing_fonts"]="Установка шрифтов..."
  ["downloading_fonts"]="Скачивание шрифтов..."
  ["extracting_fonts"]="Распаковка шрифтов..."
  ["fonts_exist"]="Шрифты уже установлены. Пропуск."
  ["copying_local_fonts"]="Копирование локальных шрифтов..."
  ["local_fonts_exist"]="Локальные шрифты уже установлены. Пропуск."
  ["config_network"]="Настройка сетевых сервисов..."
  ["disabling_iwd"]="Отключение iwd..."
  ["iwd_disabled"]="iwd уже отключён."
  ["enabling_nm"]="Включение NetworkManager..."
  ["nm_enabled"]="NetworkManager уже включён."
  ["starting_nm"]="Запуск NetworkManager..."
  ["nm_running"]="NetworkManager уже запущен."
  ["config_hyprland"]="Настройка Hyprland конфигурации..."
  ["hyprland_backup"]="Создание резервной копии старого конфига..."
  ["hyprland_backup_created"]="Резервная копия создана:"
  ["hyprland_no_backup"]="Старый конфиг не найден, бэкап не требуется."
  ["hyprland_creating"]="Применение конфигурации Vidgex-Shell..."
  ["hyprland_created"]="Конфигурация Hyprland применена!"
  ["hyprland_already_configured"]="Vidgex-Shell уже настроен в hyprland.conf"
  ["hyprland_skip"]="Пропуск перезаписи конфигурации."
  ["starting_shell"]="Запуск Vidgex-Shell..."
  ["install_complete"]="Установка завершена!"
  ["press_continue"]="Нажмите Enter для продолжения..."
  ["cleanup"]="Очистка временных файлов..."
  ["relogin_hint"]="Перезайдите в Hyprland для применения"
  ["restart_prompt"]="Для перезагрузки ПК нажмите Enter"
  ["restarting"]="Перезагрузка..."
  ["detecting_gpu"]="Определение видеокарты..."
  ["gpu_detected"]="Обнаружена видеокарта"
  ["gpu_not_detected"]="Не удалось определить видеокарту"
  ["gpu_skip"]="Пропуск настройки GPU"
  ["gpu_installing_nvidia"]="Установка nvidia-utils..."
  ["gpu_nvidia_ok"]="NVIDIA драйверы настроены успешно!"
  ["gpu_nvidia_failed"]="Не удалось установить nvidia-utils"
  ["gpu_installing_intel"]="Установка intel-gpu-tools..."
  ["gpu_intel_configuring"]="Настройка прав доступа для intel_gpu_top..."
  ["gpu_intel_ok"]="Intel GPU tools настроены успешно!"
  ["gpu_intel_already_configured"]="intel_gpu_top уже настроен"
  ["gpu_intel_install_failed"]="Не удалось установить intel-gpu-tools"
  ["gpu_intel_cap_failed"]="Не удалось установить cap_perfmon"
  ["gpu_intel_manual_fix"]="Выполните вручную: sudo setcap cap_perfmon=+ep /usr/bin/intel_gpu_top"
  ["gpu_amd_ok"]="AMD GPU обнаружен"
  ["gpu_amd_sysfs"]="Используется встроенная поддержка через sysfs"
  ["gpu_amd_sysfs_ok"]="Интерфейс sysfs доступен"
  ["gpu_amd_sysfs_not_found"]="Интерфейс gpu_busy_percent не найден"
  # ── Autolayout ──
  ["autolayout_title"]="Настройка Autolayout (переключатель раскладки)..."
  ["autolayout_adding_group"]="Добавление пользователя в группу input..."
  ["autolayout_group_exists"]="Пользователь уже в группе input."
  ["autolayout_group_added"]="Пользователь добавлен в группу input!"
  ["autolayout_udev_rule"]="Создание udev-правила для /dev/uinput..."
  ["autolayout_udev_exists"]="udev-правило уже существует."
  ["autolayout_udev_created"]="udev-правило создано!"
  ["autolayout_module_load"]="Загрузка модуля ядра uinput..."
  ["autolayout_module_exists"]="Модуль uinput уже загружен."
  ["autolayout_module_loaded"]="Модуль uinput загружен и добавлен в автозагрузку!"
  ["autolayout_udev_reload"]="Применение udev-правил..."
  ["autolayout_udev_reloaded"]="udev-правила применены!"
  ["autolayout_service"]="Создание systemd user-сервиса autolayout..."
  ["autolayout_service_exists"]="Сервис autolayout уже создан."
  ["autolayout_service_created"]="Сервис autolayout создан и включён!"
  ["autolayout_done"]="Autolayout настроен! (права применятся после перелогина)"
  ["autolayout_note"]="Примечание: группа input применится после перезагрузки/перелогина"
)

declare -A MSG_EN=(
  ["welcome"]="Welcome to the installer"
  ["invalid_choice"]="Invalid choice. Using English."
  ["not_root"]="Please do not run this script as root."
  ["checking_git"]="Checking for git..."
  ["installing_git"]="Installing git..."
  ["cloning_repo"]="Cloning Vidgex-Shell repository..."
  ["repo_exists"]="Repository already exists."
  ["update_repo"]="Update existing repository? [y/N]"
  ["updating_repo"]="Updating repository..."
  ["repo_updated"]="Repository updated!"
  ["repo_skipped"]="Update skipped."
  ["repo_cloned"]="Repository cloned successfully!"
  ["checking_aur"]="Checking AUR helper..."
  ["installing_yay"]="Installing yay-bin..."
  ["using_helper"]="Using AUR helper:"
  ["installing_packages"]="Installing packages (Hyprland + dependencies)..."
  ["installing_fonts"]="Installing fonts..."
  ["downloading_fonts"]="Downloading fonts..."
  ["extracting_fonts"]="Extracting fonts..."
  ["fonts_exist"]="Fonts already installed. Skipping."
  ["copying_local_fonts"]="Copying local fonts..."
  ["local_fonts_exist"]="Local fonts already installed. Skipping."
  ["config_network"]="Configuring network services..."
  ["disabling_iwd"]="Disabling iwd..."
  ["iwd_disabled"]="iwd is already disabled."
  ["enabling_nm"]="Enabling NetworkManager..."
  ["nm_enabled"]="NetworkManager is already enabled."
  ["starting_nm"]="Starting NetworkManager..."
  ["nm_running"]="NetworkManager is already running."
  ["config_hyprland"]="Configuring Hyprland..."
  ["hyprland_backup"]="Creating backup of old config..."
  ["hyprland_backup_created"]="Backup created:"
  ["hyprland_no_backup"]="No old config found, backup not needed."
  ["hyprland_creating"]="Applying Vidgex-Shell configuration..."
  ["hyprland_created"]="Hyprland configuration applied!"
  ["hyprland_already_configured"]="Vidgex-Shell already configured in hyprland.conf"
  ["hyprland_skip"]="Skipping configuration overwrite."
  ["starting_shell"]="Starting Vidgex-Shell..."
  ["install_complete"]="Installation complete!"
  ["press_continue"]="Press Enter to continue..."
  ["cleanup"]="Cleaning up temporary files..."
  ["relogin_hint"]="Re-login to Hyprland to apply"
  ["restart_prompt"]="Press Enter to reboot PC"
  ["restarting"]="Rebooting..."
  ["detecting_gpu"]="Detecting GPU..."
  ["gpu_detected"]="Detected GPU"
  ["gpu_not_detected"]="Failed to detect GPU"
  ["gpu_skip"]="Skipping GPU configuration"
  ["gpu_installing_nvidia"]="Installing nvidia-utils..."
  ["gpu_nvidia_ok"]="NVIDIA drivers configured successfully!"
  ["gpu_nvidia_failed"]="Failed to install nvidia-utils"
  ["gpu_installing_intel"]="Installing intel-gpu-tools..."
  ["gpu_intel_configuring"]="Configuring permissions for intel_gpu_top..."
  ["gpu_intel_ok"]="Intel GPU tools configured successfully!"
  ["gpu_intel_already_configured"]="intel_gpu_top already configured"
  ["gpu_intel_install_failed"]="Failed to install intel-gpu-tools"
  ["gpu_intel_cap_failed"]="Failed to set cap_perfmon"
  ["gpu_intel_manual_fix"]="Run manually: sudo setcap cap_perfmon=+ep /usr/bin/intel_gpu_top"
  ["gpu_amd_ok"]="AMD GPU detected"
  ["gpu_amd_sysfs"]="Using built-in sysfs support"
  ["gpu_amd_sysfs_ok"]="sysfs interface available"
  ["gpu_amd_sysfs_not_found"]="gpu_busy_percent interface not found"
  # ── Autolayout ──
  ["autolayout_title"]="Configuring Autolayout (keyboard layout switcher)..."
  ["autolayout_adding_group"]="Adding user to input group..."
  ["autolayout_group_exists"]="User is already in input group."
  ["autolayout_group_added"]="User added to input group!"
  ["autolayout_udev_rule"]="Creating udev rule for /dev/uinput..."
  ["autolayout_udev_exists"]="udev rule already exists."
  ["autolayout_udev_created"]="udev rule created!"
  ["autolayout_module_load"]="Loading uinput kernel module..."
  ["autolayout_module_exists"]="uinput module already loaded."
  ["autolayout_module_loaded"]="uinput module loaded and added to autostart!"
  ["autolayout_udev_reload"]="Reloading udev rules..."
  ["autolayout_udev_reloaded"]="udev rules reloaded!"
  ["autolayout_service"]="Creating systemd user service for autolayout..."
  ["autolayout_service_exists"]="Autolayout service already created."
  ["autolayout_service_created"]="Autolayout service created and enabled!"
  ["autolayout_done"]="Autolayout configured! (permissions apply after re-login)"
  ["autolayout_note"]="Note: input group takes effect after reboot/re-login"
)

# ═══════════════════════════════════════════════════════════════════════════════
# ФУНКЦИИ
# ═══════════════════════════════════════════════════════════════════════════════

msg() {
  local key="$1"
  if [ "$LANG_CHOICE" = "RU" ]; then
    echo "${MSG_RU[$key]}"
  else
    echo "${MSG_EN[$key]}"
  fi
}

print_info() {
  echo -e "${BLUE}[ℹ]${NC} $1"
}

print_success() {
  echo -e "${GREEN}[✓]${NC} $1"
}

print_warning() {
  echo -e "${YELLOW}[⚠]${NC} $1"
}

print_error() {
  echo -e "${RED}[✗]${NC} $1"
}

print_step() {
  echo -e "\n${PURPLE}[→]${NC} ${BOLD}$1${NC}"
}

print_separator() {
  echo -e "${GRAY}══════════════════════════════════════════════════════════════════${NC}"
}

# ═══════════════════════════════════════════════════════════════════════════════
# АНИМАЦИЯ БАННЕРА
# ═══════════════════════════════════════════════════════════════════════════════

show_animated_banner() {
  clear

  # 1. МАССИВЫ ДЛЯ ВЕРХНЕГО ЛОГОТИПА
  local banner_lines=(
    "        #########################################################################################        "
    "  #####################################################################################################  "
    " ####################################################################################################### "
    "###   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ###"
    "###---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---###"
    "###   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ###"
    "###    @№@        @@/    \\@@##    /@@@@@@@@\\         /@@@@@@*@@@@/   /@@@№№@@@@@/    @@@      /@|     ###"
    "###     @@\\      @№|      @@@     @@@@###@@@\\      /@!58@@@@@@@/     @@@@#=@@@/      |@@\\     @@|     ###"
    "###     @@#      #@/      @@@     @@@     \\@@\\    /@<@          /    @!?               \\@\\  *@/       ###"
    "###      @#@    @#|       |@|     @&@      @@@    @&@          /@    @@@@#@@@@@/        |&#@@/        ###"
    "###      @№\\    |@/       |#@     @@?      #@@    *!?         /@@    @@##@@@/           |#@@/         ###"
    "###       @\\@  /#|        |#@     @@@    /#@@/    @>@@       /@*@    @&&               /@№  @@\\       ###"
    "###        @@##@/         #@@     @@@@@@/@@@/      @@@@&@?@@@@@/     @@@&?\"@@@/      /@#     @!@\\     ###"
    "###         @@@/         #@@@\\    \\@@@>-@@@/         \\@,,@@@@@/      \\@@@@@@@@@@/    /@@      @@|     ###"
    "###   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ###"
    "###---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---###"
    "###   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ###"
    " ####################################################################################################### "
    "  #####################################################################################################  "
    "        #########################################################################################        "
  )

  local colors=("$GRAY" "$GRAY" "$CYAN" "$CYAN" "$LIGHT_CYAN" "$CYAN"
                "$PURPLE" "$LIGHT_PURPLE" "$PURPLE" "$LIGHT_PURPLE"
                "$PURPLE" "$LIGHT_PURPLE" "$PURPLE" "$LIGHT_PURPLE"
                "$CYAN" "$LIGHT_CYAN" "$CYAN" "$CYAN" "$GRAY" "$GRAY")

  # 2. МАССИВЫ ДЛЯ НИЖНЕЙ ТАБЛИЧКИ
  local box_raw=(
    "                  ╔═══════════════════════════════════════════════════════════════╗"
    "                  ║                                                               ║"
    "                  ║                V I D G E X  -  S H E L L                      ║"
    "                  ║                   Installation Script                         ║"
    "                  ║                                                               ║"
    "                  ╚═══════════════════════════════════════════════════════════════╝"
  )

  local box_colored=(
    "${WHITE}${BOLD}                  ╔═══════════════════════════════════════════════════════════════╗${NC}"
    "${WHITE}${BOLD}                  ║                                                               ║${NC}"
    "${WHITE}${BOLD}                  ║                ${PURPLE}V I D G E X  -  S H E L L${WHITE}                      ║${NC}"
    "${WHITE}${BOLD}                  ║                   ${GRAY}Installation Script${WHITE}                         ║${NC}"
    "${WHITE}${BOLD}                  ║                                                               ║${NC}"
    "${WHITE}${BOLD}                  ╚═══════════════════════════════════════════════════════════════╝${NC}"
  )

  local g_formats=('\033[46;30m' '\033[45;30m' '\033[47;30m' '\033[100;30m' '\033[7m' '\033[1;36m')

  echo ""

  # --- АНИМАЦИЯ ЛОГОТИПА ---
  for i in "${!banner_lines[@]}"; do
    local line="${banner_lines[$i]}"
    local final_color="${colors[$i]}"
    local len=${#line}

    local format="${g_formats[$((RANDOM % ${#g_formats[@]}))]}"
    local glitched=""
    local type=$((RANDOM % 5))
    
    if [ $type -eq 0 ]; then
      local shift=$((RANDOM % 20 + 15))
      glitched="$(printf '%*s' "$shift" '')████████████${line:0:$((len-shift-12))}"
    elif [ $type -eq 1 ]; then
      local shift=$((RANDOM % 20 + 15))
      glitched="${line:$shift}████████████$(printf '%*s' "$shift" '')"
    elif [ $type -eq 2 ]; then
      local cut=$((RANDOM % 40 + 20))
      glitched="${line:0:cut}▓▓▓▓████████▓▓▓▓${line:$((cut+16))}"
    elif [ $type -eq 3 ]; then
      glitched="$(printf '█%.0s' {1..105})"
    else
      glitched="${line// /█}"
      glitched="${glitched//@/▓}"
      glitched="${glitched//#/▒}"
    fi

    # Строго обрезаем длину глитча, чтобы он не вылезал за пределы строки
    glitched="${glitched:0:$len}"

    echo -ne "${format}${glitched}${NC}\r"
    sleep 0.03
    # Добавлен \033[K - зачистка остатков строки перед выводом чистой
    echo -e "${final_color}${line}\033[K${NC}"
    sleep 0.01
  done

  echo ""
  
  # Промежуточный "удар" глитчем
  echo -ne "\033[46;30m$(printf '█%.0s' {1..105})${NC}\r"
  sleep 0.04
  # Полная очистка всей строки \033[2K
  echo -ne "\033[2K\r" 

  # --- АНИМАЦИЯ ТАБЛИЧКИ ---
  for i in "${!box_raw[@]}"; do
    local line="${box_raw[$i]}"
    local final_line="${box_colored[$i]}"
    local len=${#line}

    local format="${g_formats[$((RANDOM % ${#g_formats[@]}))]}"
    local glitched=""
    local type=$((RANDOM % 5))
    
    if [ $type -eq 0 ]; then
      local shift=$((RANDOM % 15 + 10))
      glitched="$(printf '%*s' "$shift" '')████████${line:0:$((len-shift-8))}"
    elif [ $type -eq 1 ]; then
      local shift=$((RANDOM % 15 + 10))
      glitched="${line:$shift}████████$(printf '%*s' "$shift" '')"
    elif [ $type -eq 2 ]; then
      local cut=$((RANDOM % 30 + 15))
      glitched="${line:0:cut}▓▓████▓▓${line:$((cut+8))}"
    elif [ $type -eq 3 ]; then
      glitched="$(printf '█%.0s' {1..105})"
    else
      glitched="${line// /█}"
      glitched="${glitched//═/▓}"
      glitched="${glitched//║/▒}"
    fi

    # Строго обрезаем
    glitched="${glitched:0:$len}"

    echo -ne "${format}${glitched}${NC}\r"
    sleep 0.03
    # Добавлен \033[K для защиты от остаточных пикселей глитча
    echo -e "${final_line}\033[K"
    sleep 0.01
  done

  echo ""
}

show_banner() {
  clear
  echo -e "${CYAN}"
  cat << 'EOF'
        #########################################################################################
  #####################################################################################################
 #######################################################################################################
###   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ###
###---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---###
###   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ###
###    @№@        @@/    \@@##    /@@@@@@@@\         /@@@@@@*@@@@/   /@@@№№@@@@@/    @@@      /@|     ###
###     @@\      @№|      @@@     @@@@###@@@\      /@!58@@@@@@@/     @@@@#=@@@/      |@@\     @@|     ###
###     @@#      #@/      @@@     @@@     \@@\    /@<@          /    @!?               \@\  *@/       ###
###      @#@    @#|       |@|     @&@      @@@    @&@          /@    @@@@#@@@@@/        |&#@@/        ###
###      @№\    |@/       |#@     @@?      #@@    *!?         /@@    @@##@@@/           |#@@/         ###
###       @\@  /#|        |#@     @@@    /#@@/    @>@@       /@*@    @&&               /@№  @@\       ###
###        @@##@/         #@@     @@@@@@/@@@/      @@@@&@?@@@@@/     @@@&?"@@@/      /@#     @!@\     ###
###         @@@/         #@@@\    \@@@>-@@@/         \@,,@@@@@/      \@@@@@@@@@@/    /@@      @@|     ###
###   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ###
###---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---###
###   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ###
 #######################################################################################################
  #####################################################################################################
        #########################################################################################
EOF
  echo -e "${NC}"
  echo ""
  echo -e "${WHITE}${BOLD}                  ╔═══════════════════════════════════════════════════════════════╗${NC}"
  echo -e "${WHITE}${BOLD}                  ║                                                               ║${NC}"
  echo -e "${WHITE}${BOLD}                  ║                ${PURPLE}V I D G E X  -  S H E L L${WHITE}                      ║${NC}"
  echo -e "${WHITE}${BOLD}                  ║                   ${GRAY}Installation Script${WHITE}                         ║${NC}"
  echo -e "${WHITE}${BOLD}                  ║                                                               ║${NC}"
  echo -e "${WHITE}${BOLD}                  ╚═══════════════════════════════════════════════════════════════╝${NC}"
  echo ""
}

select_language() {
  echo -e "${WHITE}${BOLD}┌───────────────────────────────────────────┐${NC}"
  echo -e "${WHITE}${BOLD}│                                           │${NC}"
  echo -e "${WHITE}${BOLD}│   ${CYAN}Выберите язык / Select language:${WHITE}        │${NC}"
  echo -e "${WHITE}${BOLD}│                                           │${NC}"
  echo -e "${WHITE}${BOLD}├───────────────────────────────────────────┤${NC}"
  echo -e "${WHITE}${BOLD}│                                           │${NC}"
  echo -e "${WHITE}${BOLD}│      ${GREEN}[1]${WHITE} 🇷🇺  Русский                      │${NC}"
  echo -e "${WHITE}${BOLD}│                                           │${NC}"
  echo -e "${WHITE}${BOLD}│      ${GREEN}[2]${WHITE} 🇬🇧  English                      │${NC}"
  echo -e "${WHITE}${BOLD}│                                           │${NC}"
  echo -e "${WHITE}${BOLD}└───────────────────────────────────────────┘${NC}"
  echo ""
  echo -ne "${YELLOW}►${NC} Введите номер / Enter number ${GRAY}[1/2]:${NC} "

  read -r choice

  case $choice in
    1)
      LANG_CHOICE="RU"
      echo ""
      print_success "Выбран язык: Русский"
      ;;
    2)
      LANG_CHOICE="EN"
      echo ""
      print_success "Selected language: English"
      ;;
    *)
      LANG_CHOICE="EN"
      echo ""
      print_warning "Invalid choice. Using English."
      ;;
  esac

  echo ""
  echo -ne "${GRAY}$(msg "press_continue")${NC}"
  read -r
}

# ═══════════════════════════════════════════════════════════════════════════════
# НАСТРОЙКА HYPRLAND (с проверкой на существующую конфигурацию)
# ═══════════════════════════════════════════════════════════════════════════════
configure_hyprland() {
  print_step "$(msg "config_hyprland")"

  if [ ! -d "$HOME/.config/hypr" ]; then
    mkdir -p "$HOME/.config/hypr"
    print_info "Created ~/.config/hypr directory"
  fi

  if [ -f "$HYPRLAND_CONF" ]; then
    if grep -qF "source = ~/.config/Vidgex-Shell/vidgex-shell-conf/vidgex-shell.conf" "$HYPRLAND_CONF" 2>/dev/null; then
      print_success "$(msg "hyprland_already_configured")"
      print_info "$(msg "hyprland_skip")"
      echo -e "         ${GRAY}→ $HYPRLAND_CONF${NC}"
      return 0
    fi
  fi

  if [ -f "$HYPRLAND_CONF" ]; then
    BACKUP_FILE="${HYPRLAND_CONF}.backup.$(date +%Y%m%d_%H%M%S)"
    print_info "$(msg "hyprland_backup")"
    cp "$HYPRLAND_CONF" "$BACKUP_FILE"
    print_success "$(msg "hyprland_backup_created")"
    echo -e "         ${GRAY}→ $BACKUP_FILE${NC}"
  else
    print_info "$(msg "hyprland_no_backup")"
  fi

  print_info "$(msg "hyprland_creating")"

  cat > "$HYPRLAND_CONF" <<'EOF'
#################################
### LAZARETTO HYPRLAND CONFIG ###
#################################

################
### МОНИТОРЫ ###
################
monitor = ,preferred,auto,1

########################
### АНИМАЦИИ И ЦВЕТА ###
########################

decoration {
    rounding = 12

    active_opacity = 1.0
    inactive_opacity = 0.8

    blur {
        enabled = yes
        size = 1
        passes = 3
        new_optimizations = yes
        contrast = 1
        brightness = 1
    }

    shadow {
        enabled = true
        range = 30
        render_power = 5
        offset = 0 5
        color = rgba(00000070)
    }
}

dwindle {
    pseudotile = true
    preserve_split = true
}

master {
    mfact = 0.5
}

misc {
    vfr = true
    vrr = 2

    animate_manual_resizes = false
    animate_mouse_windowdragging = false

    disable_splash_rendering = true
    disable_hyprland_logo = true
    force_default_wallpaper = 0

    allow_session_lock_restore = true
    middle_click_paste = false
    focus_on_activate = false
    session_lock_xray = true

    mouse_move_enables_dpms = true
    key_press_enables_dpms = true
    enable_swallow = true

	background_color = rgb(1f1e1e)
}

cursor {
    no_warps = true
}

xwayland {
    enabled = true
    force_zero_scaling = true
}

################################
### НАСТРОЙКИ ГОРЯЧИХ КЛАВИШ ###
################################
# source = ~/.config/hypr/bind.conf

# Базовые бинды
bind = SUPER SHIFT, Q, killactive # Закрыть окно
bind = SUPER ALT, T, exec, kitty # Терминал

bind = SUPER, F, exec, firefox # Браузер


debug {
    damage_tracking = 2
}

# Vidgex Shell
source = ~/.config/Vidgex-Shell/vidgex-shell-conf/vidgex-shell.conf
EOF

  print_success "$(msg "hyprland_created")"
  echo -e "         ${GRAY}→ $HYPRLAND_CONF${NC}"
}

# ═══════════════════════════════════════════════════════════════════════════════
# ОПРЕДЕЛЕНИЕ И НАСТРОЙКА GPU
# ═══════════════════════════════════════════════════════════════════════════════
detect_and_configure_gpu() {
  print_step "$(msg "detecting_gpu")"

  local gpu_vendor=""
  local gpu_name=""

  if command -v lspci &>/dev/null; then
    local lspci_output
    lspci_output=$(lspci 2>/dev/null | grep -E "VGA|3D|Display" || true)

    if echo "$lspci_output" | grep -iq "nvidia"; then
      gpu_vendor="nvidia"
      gpu_name=$(echo "$lspci_output" | grep -i nvidia | head -1 | sed 's/.*: //')
    elif echo "$lspci_output" | grep -iq "intel.*graphics\|intel.*uhd\|intel.*iris"; then
      gpu_vendor="intel"
      gpu_name=$(echo "$lspci_output" | grep -iE "intel.*(graphics|uhd|iris|arc)" | head -1 | sed 's/.*: //')
    elif echo "$lspci_output" | grep -iqE "amd|radeon|advanced micro"; then
      gpu_vendor="amd"
      gpu_name=$(echo "$lspci_output" | grep -iE "amd|radeon" | head -1 | sed 's/.*: //')
    fi
  fi

  if [ -z "$gpu_vendor" ]; then
    shopt -s nullglob
    local vendor_files=(/sys/class/drm/card*/device/vendor)
    shopt -u nullglob

    for card_vendor in "${vendor_files[@]}"; do
      if [ -f "$card_vendor" ]; then
        local vendor_id
        vendor_id=$(cat "$card_vendor" 2>/dev/null || true)
        case "$vendor_id" in
          0x10de) gpu_vendor="nvidia" ;;
          0x8086) gpu_vendor="intel" ;;
          0x1002) gpu_vendor="amd" ;;
        esac
        [ -n "$gpu_vendor" ] && break
      fi
    done
  fi

  if [ -z "$gpu_vendor" ]; then
    print_warning "$(msg "gpu_not_detected")"
    print_info "$(msg "gpu_skip")"
    return 0
  fi

  print_success "$(msg "gpu_detected"): ${CYAN}${gpu_vendor^^}${NC}"
  [ -n "$gpu_name" ] && echo -e "         ${GRAY}→ $gpu_name${NC}"

  case "$gpu_vendor" in
    nvidia)
      print_info "$(msg "gpu_installing_nvidia")"
      if $aur_helper -S --needed --noconfirm nvidia-utils 2>/dev/null; then
        if command -v nvidia-smi &>/dev/null; then
          print_success "$(msg "gpu_nvidia_ok")"
        else
          print_warning "nvidia-utils установлен, но nvidia-smi недоступен"
        fi
      else
        print_warning "$(msg "gpu_nvidia_failed")"
      fi
      ;;

    intel)
      print_info "$(msg "gpu_installing_intel")"

      local intel_installed=false
      if pacman -Qq intel-gpu-tools &>/dev/null; then
        intel_installed=true
      else
        if sudo pacman -S --needed --noconfirm intel-gpu-tools 2>/dev/null; then
          intel_installed=true
        else
          print_warning "$(msg "gpu_intel_install_failed")"
          return 0
        fi
      fi

      if [ "$intel_installed" = true ] && [ -f /usr/bin/intel_gpu_top ]; then
        print_info "$(msg "gpu_intel_configuring")"

        if command -v getcap &>/dev/null; then
          local current_cap
          current_cap=$(getcap /usr/bin/intel_gpu_top 2>/dev/null || true)
          if [[ "$current_cap" == *"cap_perfmon"* ]]; then
            print_success "$(msg "gpu_intel_already_configured")"
          else
            if sudo setcap cap_perfmon=+ep /usr/bin/intel_gpu_top 2>/dev/null; then
              print_success "$(msg "gpu_intel_ok")"
            else
              print_warning "$(msg "gpu_intel_cap_failed")"
              echo -e "         ${GRAY}→ $(msg "gpu_intel_manual_fix")${NC}"
            fi
          fi
        else
          if sudo setcap cap_perfmon=+ep /usr/bin/intel_gpu_top 2>/dev/null; then
            print_success "$(msg "gpu_intel_ok")"
          else
            print_warning "$(msg "gpu_intel_cap_failed")"
          fi
        fi
      else
        print_warning "intel_gpu_top не найден после установки"
      fi
      ;;

    amd)
      print_success "$(msg "gpu_amd_ok")"
      print_info "$(msg "gpu_amd_sysfs")"

      local amd_sysfs_found=false
      for i in {0..8}; do
        if [ -f "/sys/class/drm/card${i}/device/gpu_busy_percent" ]; then
          amd_sysfs_found=true
          print_success "$(msg "gpu_amd_sysfs_ok")"
          echo -e "         ${GRAY}→ /sys/class/drm/card${i}/device/gpu_busy_percent${NC}"
          break
        fi
      done

      if [ "$amd_sysfs_found" = false ]; then
        print_warning "$(msg "gpu_amd_sysfs_not_found")"
        print_info "Опционально: можно установить radeontop или amdgpu_top"
      fi
      ;;
  esac

  echo ""
}

# ═══════════════════════════════════════════════════════════════════════════════
# НАСТРОЙКА AUTOLAYOUT (права /dev/input + /dev/uinput без sudo)
# ═══════════════════════════════════════════════════════════════════════════════
configure_autolayout_permissions() {
  print_step "$(msg "autolayout_title")"

  local current_user
  current_user=$(whoami)
  local needs_relogin=false

  # ── 1. Добавление пользователя в группу input ──
  print_info "$(msg "autolayout_adding_group")"

  if id -nG "$current_user" | grep -qw "input"; then
    print_success "$(msg "autolayout_group_exists")"
  else
    sudo usermod -aG input "$current_user"
    print_success "$(msg "autolayout_group_added")"
    needs_relogin=true
  fi

  # ── 2. udev-правило для /dev/uinput ──
  local udev_rule_file="/etc/udev/rules.d/99-uinput.rules"
  local udev_rule_content='KERNEL=="uinput", GROUP="input", MODE="0660"'

  print_info "$(msg "autolayout_udev_rule")"

  if [ -f "$udev_rule_file" ] && grep -qF "$udev_rule_content" "$udev_rule_file" 2>/dev/null; then
    print_success "$(msg "autolayout_udev_exists")"
  else
    echo "$udev_rule_content" | sudo tee "$udev_rule_file" > /dev/null
    print_success "$(msg "autolayout_udev_created")"
    echo -e "         ${GRAY}→ $udev_rule_file${NC}"
  fi

  # ── 3. Загрузка модуля ядра uinput ──
  local modules_file="/etc/modules-load.d/uinput.conf"

  print_info "$(msg "autolayout_module_load")"

  if lsmod | grep -q "^uinput"; then
    print_success "$(msg "autolayout_module_exists")"
  else
    sudo modprobe uinput
    print_success "modprobe uinput ✓"
  fi

  if [ -f "$modules_file" ] && grep -qF "uinput" "$modules_file" 2>/dev/null; then
    print_success "autoload uinput ✓"
  else
    echo "uinput" | sudo tee "$modules_file" > /dev/null
    print_success "$(msg "autolayout_module_loaded")"
    echo -e "         ${GRAY}→ $modules_file${NC}"
  fi

  # ── 4. Перезагрузка udev-правил ──
  print_info "$(msg "autolayout_udev_reload")"
  sudo udevadm control --reload-rules
  sudo udevadm trigger
  print_success "$(msg "autolayout_udev_reloaded")"

  # ── 5. Systemd user-сервис для autolayout ──
  print_info "$(msg "autolayout_service")"

  mkdir -p "$HOME/.config/systemd/user"

  if [ -f "$AUTOLAYOUT_SERVICE" ]; then
    print_success "$(msg "autolayout_service_exists")"
  else
    cat > "$AUTOLAYOUT_SERVICE" <<SVCEOF
[Unit]
Description=Autolayout - smart keyboard layout switcher
After=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 ${AUTOLAYOUT_SCRIPT}
Restart=on-failure
RestartSec=3

[Install]
WantedBy=graphical-session.target
SVCEOF

    systemctl --user daemon-reload
    systemctl --user enable autolayout.service
    print_success "$(msg "autolayout_service_created")"
    echo -e "         ${GRAY}→ $AUTOLAYOUT_SERVICE${NC}"
  fi

  # ── Итог ──
  echo ""
  print_success "$(msg "autolayout_done")"
  if [ "$needs_relogin" = true ]; then
    print_warning "$(msg "autolayout_note")"
  fi
}

# ═══════════════════════════════════════════════════════════════════════════════
# ОСНОВНОЙ СКРИПТ
# ═══════════════════════════════════════════════════════════════════════════════

# 1. 🎨 Баннер
show_animated_banner

# 2. 🌐 Выбор языка
select_language

show_banner
echo -e "${GREEN}${BOLD}$(msg "welcome") Vidgex-Shell!${NC}"
echo ""
print_separator

# Проверка на root
if [ "$(id -u)" -eq 0 ]; then
  print_error "$(msg "not_root")"
  exit 1
fi

# ═══════════════════════════════════════════════════════════════════════════════
# ПРОВЕРКА И УСТАНОВКА GIT
# ═══════════════════════════════════════════════════════════════════════════════
print_step "$(msg "checking_git")"

if ! command -v git &>/dev/null; then
  print_info "$(msg "installing_git")"
  sudo pacman -S --noconfirm git
  print_success "git ✓"
else
  print_success "git ✓"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# КЛОНИРОВАНИЕ РЕПОЗИТОРИЯ
# ═══════════════════════════════════════════════════════════════════════════════
print_step "$(msg "cloning_repo")"
echo -e "         ${GRAY}$REPO_URL${NC}"
echo -e "         ${GRAY}branch: ${CYAN}$REPO_BRANCH${NC}"

if [ -d "$INSTALL_DIR" ]; then
  print_warning "$(msg "repo_exists")"
  echo -e "         ${GRAY}$INSTALL_DIR${NC}"
  echo ""
  echo -ne "${YELLOW}►${NC} $(msg "update_repo") "
  read -r update_choice

  case $update_choice in
    [Yy]|[Yy][Ee][Ss]|[Дд]|[Дд][Аа])
      print_info "$(msg "updating_repo")"
      cd "$INSTALL_DIR"
      git fetch --all
      git checkout "$REPO_BRANCH"
      git reset --hard "origin/$REPO_BRANCH"
      git pull origin "$REPO_BRANCH"
      print_success "$(msg "repo_updated")"
      ;;
    *)
      print_info "$(msg "repo_skipped")"
      ;;
  esac
else
  mkdir -p "$(dirname "$INSTALL_DIR")"
  git clone --depth=1 -b "$REPO_BRANCH" "$REPO_URL" "$INSTALL_DIR"
  print_success "$(msg "repo_cloned")"
  echo -e "         ${GRAY}→ $INSTALL_DIR${NC}"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# УСТАНОВКА AUR-ХЕЛПЕРА
# ═══════════════════════════════════════════════════════════════════════════════
print_step "$(msg "checking_aur")"

aur_helper="yay"

if command -v paru &>/dev/null; then
  aur_helper="paru"
  print_success "$(msg "using_helper") ${CYAN}paru${NC}"
elif command -v yay &>/dev/null; then
  print_success "$(msg "using_helper") ${CYAN}yay${NC}"
else
  print_info "$(msg "installing_yay")"

  if ! pacman -Qq base-devel &>/dev/null; then
    print_info "Installing base-devel..."
    sudo pacman -S --needed --noconfirm base-devel
  fi

  tmpdir=$(mktemp -d)
  git clone --depth=1 https://aur.archlinux.org/yay-bin.git "$tmpdir/yay-bin"
  (cd "$tmpdir/yay-bin" && makepkg -si --noconfirm)
  rm -rf "$tmpdir"

  print_success "$(msg "using_helper") ${CYAN}yay${NC}"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# УСТАНОВКА ПАКЕТОВ (включая python-evdev)
# ═══════════════════════════════════════════════════════════════════════════════
print_step "$(msg "installing_packages")"
echo -e "         ${GRAY}hyprland, fabric, tesseract, python-evdev...${NC}"
$aur_helper -Syy --needed --noconfirm "${PACKAGES[@]}" || true
print_success "$(msg "installing_packages")"

# ═══════════════════════════════════════════════════════════════════════════════
# ОПРЕДЕЛЕНИЕ И НАСТРОЙКА GPU
# ═══════════════════════════════════════════════════════════════════════════════
detect_and_configure_gpu

# ═══════════════════════════════════════════════════════════════════════════════
# УСТАНОВКА ШРИФТОВ
# ═══════════════════════════════════════════════════════════════════════════════
print_step "$(msg "installing_fonts")"

if ! command -v ouch &>/dev/null; then
  print_info "Installing ouch for archive extraction..."
  $aur_helper -S --needed --noconfirm ouch
  export PATH="$HOME/.local/bin:$PATH"
  hash -r
fi

FONT_URL="https://github.com/zed-industries/zed-fonts/releases/download/1.2.0/zed-sans-1.2.0.zip"
FONT_DIR="$HOME/.fonts/zed-sans"
TEMP_ZIP="/tmp/zed-sans-1.2.0.zip"

if [ ! -d "$FONT_DIR" ]; then
  print_info "$(msg "downloading_fonts")"
  curl -L -o "$TEMP_ZIP" "$FONT_URL"

  print_info "$(msg "extracting_fonts")"
  mkdir -p "$FONT_DIR"

  ouch decompress "$TEMP_ZIP" --dir "$FONT_DIR"

  print_info "$(msg "cleanup")"
  rm "$TEMP_ZIP"
  print_success "Zed Sans ✓"
else
  print_success "$(msg "fonts_exist")"
fi

if [ ! -d "$HOME/.fonts/tabler-icons" ]; then
  print_info "$(msg "copying_local_fonts")"
  mkdir -p "$HOME/.fonts/tabler-icons"
  cp -r "$INSTALL_DIR/helper-folder/tabler-icons"* "$HOME/.fonts" 2>/dev/null || true
  print_success "Tabler Icons ✓"
else
  print_success "$(msg "local_fonts_exist")"
fi

fc-cache -fv >/dev/null 2>&1 || true

# ═══════════════════════════════════════════════════════════════════════════════
# НАСТРОЙКА СЕТИ
# ═══════════════════════════════════════════════════════════════════════════════
print_step "$(msg "config_network")"

if systemctl is-enabled --quiet iwd 2>/dev/null || systemctl is-active --quiet iwd 2>/dev/null; then
  print_info "$(msg "disabling_iwd")"
  sudo systemctl disable --now iwd
else
  print_success "$(msg "iwd_disabled")"
fi

if ! systemctl is-enabled --quiet NetworkManager 2>/dev/null; then
  print_info "$(msg "enabling_nm")"
  sudo systemctl enable NetworkManager
else
  print_success "$(msg "nm_enabled")"
fi

if ! systemctl is-active --quiet NetworkManager 2>/dev/null; then
  print_info "$(msg "starting_nm")"
  sudo systemctl start NetworkManager
else
  print_success "$(msg "nm_running")"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# НАСТРОЙКА HYPRLAND КОНФИГА
# ═══════════════════════════════════════════════════════════════════════════════
configure_hyprland

# ═══════════════════════════════════════════════════════════════════════════════
# НАСТРОЙКА AUTOLAYOUT (ПРАВА ДЛЯ РАБОТЫ БЕЗ SUDO)
# ═══════════════════════════════════════════════════════════════════════════════
configure_autolayout_permissions

# ═══════════════════════════════════════════════════════════════════════════════
# ЗАПУСК VIDGEX-SHELL
# ═══════════════════════════════════════════════════════════════════════════════
print_step "$(msg "starting_shell")"
killall vidgex-shell 2>/dev/null || true
python "$INSTALL_DIR/main.py" >/dev/null 2>&1 &
disown
print_success "Vidgex-Shell started!"

# ═══════════════════════════════════════════════════════════════════════════════
# ЗАВЕРШЕНИЕ
# ═══════════════════════════════════════════════════════════════════════════════
echo ""
print_separator
echo ""
echo -e "${GREEN}${BOLD}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}${BOLD}║                                                                ║${NC}"
echo -e "${GREEN}${BOLD}║                    $(msg "install_complete")                        ║${NC}"
echo -e "${GREEN}${BOLD}║                                                                ║${NC}"
echo -e "${GREEN}${BOLD}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${GRAY}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GRAY}║                                                                ║${NC}"
echo -e "${GRAY}║${NC}  ${WHITE}Vidgex-Shell:${NC} ${CYAN}~/.config/Vidgex-Shell${NC}                          ${GRAY}║${NC}"
echo -e "${GRAY}║${NC}  ${WHITE}Hyprland cfg:${NC} ${CYAN}~/.config/hypr/hyprland.conf${NC}                    ${GRAY}║${NC}"
echo -e "${GRAY}║${NC}  ${WHITE}Branch:${NC}       ${CYAN}$REPO_BRANCH${NC}                                         ${GRAY}║${NC}"
echo -e "${GRAY}║                                                                ║${NC}"
echo -e "${GRAY}╠════════════════════════════════════════════════════════════════╣${NC}"
echo -e "${GRAY}║                                                                ║${NC}"
echo -e "${GRAY}║${NC}  ${YELLOW}💡 $(msg "relogin_hint")${NC}                      ${GRAY}║${NC}"
echo -e "${GRAY}║                                                                ║${NC}"
echo -e "${GRAY}║${NC}  ${CYAN}► $(msg "restart_prompt")${NC}                           ${GRAY}║${NC}"
echo -e "${GRAY}║                                                                ║${NC}"
echo -e "${GRAY}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""
read -r

print_info "$(msg "restarting")"
systemctl reboot