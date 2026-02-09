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
REPO_BRANCH="develop"
HYPRLAND_CONF="$HOME/.config/hypr/hyprland.conf"
MATUGEN_SRC="$INSTALL_DIR/helper-folder/matugen"
MATUGEN_DST="$HOME/.config/matugen"
LANG_CHOICE="EN"

PACKAGES=(
  # Hyprland и основные компоненты
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

  # Утилиты
  awww-git
  brightnessctl
  cliphist
  libnotify
  matugen
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

  nvidia-utils
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
  ["installing_paru"]="Установка paru-bin..."
  ["using_helper"]="Используется AUR-хелпер:"
  ["installing_packages"]="Установка пакетов (Hyprland + зависимости)..."
  ["installing_fonts"]="Установка шрифтов..."
  ["downloading_fonts"]="Скачивание шрифтов..."
  ["extracting_fonts"]="Распаковка шрифтов..."
  ["fonts_exist"]="Шрифты уже установлены. Пропуск."
  ["copying_local_fonts"]="Копирование локальных шрифтов..."
  ["local_fonts_exist"]="Локальные шрифты уже установлены. Пропуск."
  ["copying_matugen"]="Копирование конфигурации matugen..."
  ["matugen_exists"]="Конфигурация matugen уже существует."
  ["matugen_copied"]="Конфигурация matugen скопирована!"
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
  ["installing_paru"]="Installing paru-bin..."
  ["using_helper"]="Using AUR helper:"
  ["installing_packages"]="Installing packages (Hyprland + dependencies)..."
  ["installing_fonts"]="Installing fonts..."
  ["downloading_fonts"]="Downloading fonts..."
  ["extracting_fonts"]="Extracting fonts..."
  ["fonts_exist"]="Fonts already installed. Skipping."
  ["copying_local_fonts"]="Copying local fonts..."
  ["local_fonts_exist"]="Local fonts already installed. Skipping."
  ["copying_matugen"]="Copying matugen configuration..."
  ["matugen_exists"]="Matugen configuration already exists."
  ["matugen_copied"]="Matugen configuration copied!"
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
  echo -e "${GRAY}════════════════════════════════════════════════════════════════${NC}"
}

# ═══════════════════════════════════════════════════════════════════════════════
# АНИМАЦИЯ БАННЕРА
# ═══════════════════════════════════════════════════════════════════════════════

show_animated_banner() {
  clear
  
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
  
  echo ""
  
  for i in "${!banner_lines[@]}"; do
    local line="${banner_lines[$i]}"
    local color="${colors[$i]}"
    
    local glitch_chars='░▒▓█#@'
    local glitched=""
    for ((j=0; j<${#line}; j++)); do
      if [ $((RANDOM % 3)) -eq 0 ]; then
        glitched+="${glitch_chars:$((RANDOM % ${#glitch_chars})):1}"
      else
        glitched+="${line:$j:1}"
      fi
    done
    echo -ne "${GRAY}${glitched}${NC}\r"
    sleep 0.02
    
    echo -e "${color}${line}${NC}"
    sleep 0.03
  done
  
  echo ""
  sleep 0.2
  
  echo -e "${WHITE}${BOLD}                  ╔═══════════════════════════════════════════════════════════════╗${NC}"
  echo -e "${WHITE}${BOLD}                  ║                                                               ║${NC}"
  echo -e "${WHITE}${BOLD}                  ║               ${PURPLE}V I D G E X  -  S H E L L${WHITE}                       ║${NC}"
  echo -e "${WHITE}${BOLD}                  ║                   ${GRAY}Installation Script${WHITE}                         ║${NC}"
  echo -e "${WHITE}${BOLD}                  ║                                                               ║${NC}"
  echo -e "${WHITE}${BOLD}                  ╚═══════════════════════════════════════════════════════════════╝${NC}"
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
  echo -e "${WHITE}${BOLD}                  ║               ${PURPLE}V I D G E X  -  S H E L L${WHITE}                       ║${NC}"
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
# КОПИРОВАНИЕ MATUGEN
# ═══════════════════════════════════════════════════════════════════════════════
copy_matugen_config() {
  print_step "$(msg "copying_matugen")"
  
  if [ -d "$MATUGEN_DST" ]; then
    print_success "$(msg "matugen_exists")"
    echo -e "         ${GRAY}→ $MATUGEN_DST${NC}"
  else
    if [ -d "$MATUGEN_SRC" ]; then
      cp -r "$MATUGEN_SRC" "$MATUGEN_DST"
      print_success "$(msg "matugen_copied")"
      echo -e "         ${GRAY}→ $MATUGEN_DST${NC}"
    else
      print_warning "matugen source not found in helper-folder"
    fi
  fi
}

# ═══════════════════════════════════════════════════════════════════════════════
# НАСТРОЙКА HYPRLAND (с проверкой на существующую конфигурацию)
# ═══════════════════════════════════════════════════════════════════════════════
configure_hyprland() {
  print_step "$(msg "config_hyprland")"
  
  # Создаём директорию если не существует
  if [ ! -d "$HOME/.config/hypr" ]; then
    mkdir -p "$HOME/.config/hypr"
    print_info "Created ~/.config/hypr directory"
  fi
  
  # Проверяем, есть ли уже строка source в конфиге (точное совпадение)
  if [ -f "$HYPRLAND_CONF" ]; then
    if grep -qF "source = ~/.config/Vidgex-Shell/vidgex-shell-conf/vidgex-shell.conf" "$HYPRLAND_CONF" 2>/dev/null; then
      print_success "$(msg "hyprland_already_configured")"
      print_info "$(msg "hyprland_skip")"
      echo -e "         ${GRAY}→ $HYPRLAND_CONF${NC}"
      return 0
    fi
  fi
  
  # Создаём бэкап если старый конфиг существует
  if [ -f "$HYPRLAND_CONF" ]; then
    BACKUP_FILE="${HYPRLAND_CONF}.backup.$(date +%Y%m%d_%H%M%S)"
    print_info "$(msg "hyprland_backup")"
    cp "$HYPRLAND_CONF" "$BACKUP_FILE"
    print_success "$(msg "hyprland_backup_created")"
    echo -e "         ${GRAY}→ $BACKUP_FILE${NC}"
  else
    print_info "$(msg "hyprland_no_backup")"
  fi
  
  # Создаём новый конфиг Vidgex-Shell
  print_info "$(msg "hyprland_creating")"
  
  cat > "$HYPRLAND_CONF" <<'EOF'
monitor = ,preferred,auto,1

########################
### АНИМАЦИИ И ЦВЕТА ###
########################

decoration {
    rounding = 12

    active_opacity = 1.0
    inactive_opacity = 0.9

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
    swallow_regex = ^(kitty)$

    background_color = rgb(1f1e1e)
}

cursor {
    no_warps = true
}

xwayland {
    enabled = true
    force_zero_scaling = true
}

device {
    name = epic-mouse-v1
    sensitivity = -0.5
}

###################################
### ОКНА И РАБОЧИЕ ПРОСТРАНСТВА ###
###################################
windowrule = opacity 0.9, match:class kitty, match:focus 1
windowrule = opacity 0.7, match:class kitty, match:focus 0


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
# 3. 🔍 ПРОВЕРКА И УСТАНОВКА GIT
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
# 4. 📦 КЛОНИРОВАНИЕ РЕПОЗИТОРИЯ (ветка develop)
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
# 5. 🔧 УСТАНОВКА AUR-ХЕЛПЕРА (paru по умолчанию)
# ═══════════════════════════════════════════════════════════════════════════════
print_step "$(msg "checking_aur")"

aur_helper="paru"

if command -v paru &>/dev/null; then
  aur_helper="paru"
  print_success "$(msg "using_helper") ${CYAN}paru${NC}"
elif command -v yay &>/dev/null; then
  aur_helper="yay"
  print_warning "$(msg "using_helper") ${YELLOW}yay${NC} (paru not found)"
else
  print_info "$(msg "installing_paru")"
  tmpdir=$(mktemp -d)
  git clone --depth=1 https://aur.archlinux.org/paru-bin.git "$tmpdir/paru-bin"
  (cd "$tmpdir/paru-bin" && makepkg -si --noconfirm)
  rm -rf "$tmpdir"
  aur_helper="paru"
  print_success "$(msg "using_helper") ${CYAN}paru${NC}"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# 6. 📥 УСТАНОВКА ПАКЕТОВ (включая Hyprland)
# ═══════════════════════════════════════════════════════════════════════════════
print_step "$(msg "installing_packages")"
echo -e "         ${GRAY}hyprland, fabric, matugen, tesseract...${NC}"
$aur_helper -Syy --needed --noconfirm "${PACKAGES[@]}" || true
print_success "$(msg "installing_packages")"

# ═══════════════════════════════════════════════════════════════════════════════
# 7. 🔤 УСТАНОВКА ШРИФТОВ
# ═══════════════════════════════════════════════════════════════════════════════
print_step "$(msg "installing_fonts")"

# Проверяем наличие ouch и устанавливаем если нужно
if ! command -v ouch &>/dev/null; then
  print_info "Installing ouch for archive extraction..."
  $aur_helper -S --needed --noconfirm ouch
  # Обновляем PATH для текущей сессии
  export PATH="$HOME/.local/bin:$PATH"
  hash -r  # Обновляем кэш команд bash
fi

FONT_URL="https://github.com/zed-industries/zed-fonts/releases/download/1.2.0/zed-sans-1.2.0.zip"
FONT_DIR="$HOME/.fonts/zed-sans"
TEMP_ZIP="/tmp/zed-sans-1.2.0.zip"

if [ ! -d "$FONT_DIR" ]; then
  print_info "$(msg "downloading_fonts")"
  curl -L -o "$TEMP_ZIP" "$FONT_URL"
  
  print_info "$(msg "extracting_fonts")"
  mkdir -p "$FONT_DIR"
  
  # Используем ouch для распаковки
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
# 8. 📁 КОПИРОВАНИЕ MATUGEN КОНФИГА
# ═══════════════════════════════════════════════════════════════════════════════
copy_matugen_config

# ═══════════════════════════════════════════════════════════════════════════════
# 9. 🌐 НАСТРОЙКА СЕТИ
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
# 10. ⚙️ НАСТРОЙКА HYPRLAND КОНФИГА
# ═══════════════════════════════════════════════════════════════════════════════
configure_hyprland

# ═══════════════════════════════════════════════════════════════════════════════
# 11. 🚀 ЗАПУСК VIDGEX-SHELL
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
echo -e "${GRAY}║${NC}  ${WHITE}Matugen cfg:${NC}  ${CYAN}~/.config/matugen${NC}                               ${GRAY}║${NC}"
echo -e "${GRAY}║${NC}  ${WHITE}Branch:${NC}       ${CYAN}develop${NC}                                         ${GRAY}║${NC}"
echo -e "${GRAY}║                                                                ║${NC}"
echo -e "${GRAY}╠════════════════════════════════════════════════════════════════╣${NC}"
echo -e "${GRAY}║                                                                ║${NC}"
echo -e "${GRAY}║${NC}  ${YELLOW}💡 $(msg "relogin_hint")${NC}                      ${GRAY}║${NC}"
echo -e "${GRAY}║                                                                ║${NC}"
echo -e "${GRAY}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""