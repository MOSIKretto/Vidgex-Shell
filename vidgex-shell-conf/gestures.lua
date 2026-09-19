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





-- #############
-- ### ЖЕСТЫ ###
-- #############

-- Матричное переключение рабочих столов (3 пальца)
hl.gesture({
    fingers = 3,
    direction = "right",
    action = function() hl.exec_cmd("~/.config/hypr/Vidgex-Shell/scripts/matrix_nav.sh workspace nextL") end
})
hl.gesture({
    fingers = 3,
    direction = "left",
    action = function() hl.exec_cmd("~/.config/hypr/Vidgex-Shell/scripts/matrix_nav.sh workspace nextR") end
})
hl.gesture({
    fingers = 3,
    direction = "up",
    action = function() hl.exec_cmd("~/.config/hypr/Vidgex-Shell/scripts/matrix_nav.sh workspace nextD") end
})
hl.gesture({
    fingers = 3,
    direction = "down",
    action = function() hl.exec_cmd("~/.config/hypr/Vidgex-Shell/scripts/matrix_nav.sh workspace nextU") end
})

-- Работа с Vidgex (4 пальца)
hl.gesture({
    fingers = 4,
    direction = "pinchout",
    action = function() hl.exec_cmd("fabric-cli exec vidgex-shell 'from modules.Dock.Desktop.infinite_desktop import toggle_overview; toggle_overview()'") end
})
hl.gesture({
    fingers = 4,
    direction = "down",
    action = function() hl.exec_cmd("fabric-cli exec vidgex-shell 'notch.toggle_notch(\"dashboard\")'") end
})
hl.gesture({
    fingers = 4,
    direction = "up",
    action = function() hl.exec_cmd("fabric-cli exec vidgex-shell 'notch.close_notch()'") end
})