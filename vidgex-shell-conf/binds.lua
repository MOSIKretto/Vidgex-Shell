-- ##############################
-- ### ARLOTT HYPRLAND CONFIG ###
-- ##############################





--        #########################################################################################        
--  #####################################################################################################
-- #######################################################################################################
--###   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ###
--###---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---###
--###   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ###
--###    @№@        @@/    \@@##    /@@@@@@@@\         /@@@@@@*@@@@/   /@@@№№@@@@@/    @@@      /@|     ###
--###     @@\      @№|      @@@     @@@@###@@@\      /@!58@@@@@@@/     @@@@#=@@@/      |@@\     @@|     ###
--###     @@#      #@/      @@@     @@@     \@@\    /@<@          /    @!?               \@\  *@/       ###
--###      @#@    @#|       |@|     @&@      @@@    @&@          /@    @@@@#@@@@@/        |&#@@/        ###
--###      @№\    |@/       |#@     @@?      #@@    *!?         /@@    @@##@@@/           |#@@/         ###
--###       @\@  /#|        |#@     @@@    /#@@/    @>@@       /@*@    @&&               /@№  @@\       ###
--###        @@##@/         #@@     @@@@@@/@@@/      @@@@&@?@@@@@/     @@@&?"@@@/      /@#     @!@\     ###
--###         @@@/         #@@@\    \@@@>-@@@/         \@,,@@@@@/      \@@@@@@@@@@/    /@@      @@|     ###
--###   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ###
--###---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---###
--###   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ---   ###
-- #######################################################################################################
--  #####################################################################################################
--        #########################################################################################





-- #################################
-- ### НАСТРОЙКИ ГОРЯЧИХ КЛАВИШЬ ###
-- #################################


-- Фокус
hl.bind("ALT + left",  hl.dsp.focus({ direction = "left" }))
hl.bind("ALT + right", hl.dsp.focus({ direction = "right" }))
hl.bind("ALT + up",    hl.dsp.focus({ direction = "up" }))
hl.bind("ALT + down",  hl.dsp.focus({ direction = "down" }))


-- Перемещение / изменение размера окна мышью
hl.bind("SUPER + mouse:272", hl.dsp.window.drag(),   { mouse = true })
hl.bind("ALT + mouse:272",   hl.dsp.window.resize(), { mouse = true })


-- Переключение между рабочими столами
for i = 1, 9 do
    hl.bind("CTRL + ALT + " .. i,
        hl.dsp.exec_cmd("~/.config/hypr/Vidgex-Shell/scripts/jump_matrix.sh workspace " .. i))
end

hl.bind("CTRL + ALT + right", hl.dsp.exec_cmd("~/.config/hypr/Vidgex-Shell/scripts/matrix_nav.sh workspace nextR"))
hl.bind("CTRL + ALT + left",  hl.dsp.exec_cmd("~/.config/hypr/Vidgex-Shell/scripts/matrix_nav.sh workspace nextL"))
hl.bind("CTRL + ALT + up",    hl.dsp.exec_cmd("~/.config/hypr/Vidgex-Shell/scripts/matrix_nav.sh workspace nextU"))
hl.bind("CTRL + ALT + down",  hl.dsp.exec_cmd("~/.config/hypr/Vidgex-Shell/scripts/matrix_nav.sh workspace nextD"))


-- Перемещение окон
for i = 1, 9 do
    hl.bind("SUPER + CTRL + ALT + " .. i,
        hl.dsp.exec_cmd("~/.config/hypr/Vidgex-Shell/scripts/jump_matrix.sh movetoworkspace " .. i))
end

hl.bind("SUPER + CTRL + ALT + right", hl.dsp.exec_cmd("~/.config/hypr/Vidgex-Shell/scripts/matrix_nav.sh movetoworkspace nextR"))
hl.bind("SUPER + CTRL + ALT + left",  hl.dsp.exec_cmd("~/.config/hypr/Vidgex-Shell/scripts/matrix_nav.sh movetoworkspace nextL"))
hl.bind("SUPER + CTRL + ALT + up",    hl.dsp.exec_cmd("~/.config/hypr/Vidgex-Shell/scripts/matrix_nav.sh movetoworkspace nextU"))
hl.bind("SUPER + CTRL + ALT + down",  hl.dsp.exec_cmd("~/.config/hypr/Vidgex-Shell/scripts/matrix_nav.sh movetoworkspace nextD"))


-- Работа с Vidgex‑Shell
hl.bind("SUPER + SHIFT + W", hl.dsp.exec_cmd("fabric-cli exec vidgex-shell 'notch.main_window.wallpapers.random_wall(None, ext=True)'"))
hl.bind("SUPER + SHIFT + E", hl.dsp.exec_cmd("killall vidgex-shell; python3 ~/.config/hypr/Vidgex-Shell/main.py; fabric-cli exec vidgex-shell 'app.set_css()'"))

hl.bind("ALT + Q", hl.dsp.exec_cmd("fabric-cli exec vidgex-shell 'notch.toggle_notch(\"dashboard\")'"))
hl.bind("ALT + W", hl.dsp.exec_cmd("fabric-cli exec vidgex-shell 'notch.toggle_notch(\"network_applet\")'"))
hl.bind("ALT + E", hl.dsp.exec_cmd("fabric-cli exec vidgex-shell 'notch.toggle_notch(\"bluetooth\")'"))
hl.bind("ALT + R", hl.dsp.exec_cmd("fabric-cli exec vidgex-shell 'notch.toggle_notch(\"player\")'"))
hl.bind("ALT + T", hl.dsp.exec_cmd("fabric-cli exec vidgex-shell 'notch.toggle_notch(\"wallpapers\")'"))

hl.bind("ALT + A", hl.dsp.exec_cmd("fabric-cli exec vidgex-shell 'notch.toggle_notch(\"launcher\")'"))
hl.bind("ALT + S", hl.dsp.exec_cmd("fabric-cli exec vidgex-shell 'notch.toggle_notch(\"cliphist\")'"))

hl.bind("Print",   hl.dsp.exec_cmd("fabric-cli exec vidgex-shell 'toolbox.toggle_camera()'"))

-- Обзор
hl.bind("SUPER + SPACE", hl.dsp.exec_cmd("fabric-cli exec vidgex-shell 'from modules.Dock.Desktop.infinite_desktop import toggle_overview; toggle_overview()'")) 

-- Громкость
hl.bind("XF86AudioRaiseVolume",
    hl.dsp.exec_cmd("wpctl set-volume -l 1 @DEFAULT_AUDIO_SINK@ 5%+"),
    { locked = true, repeating = true })
hl.bind("XF86AudioLowerVolume",
    hl.dsp.exec_cmd("wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%-"),
    { locked = true, repeating = true })

    
-- Яркость
hl.bind("XF86MonBrightnessUp",
    hl.dsp.exec_cmd("brightnessctl set 5%+"),
    { locked = true, repeating = true })
hl.bind("XF86MonBrightnessDown",
    hl.dsp.exec_cmd("brightnessctl set 5%-"),
    { locked = true, repeating = true })