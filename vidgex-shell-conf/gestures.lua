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

-- Переключение между рабочими столами
hl.gesture({
    fingers = 3,
    direction = "right",
    action = function()
        hl.exec_cmd("fabric-cli exec vidgex-shell 'from modules.Bar.workspaces import matrix_nav; matrix_nav(\"workspace\", \"nextL\")'")
    end
})

hl.gesture({
    fingers = 3,
    direction = "left",
    action = function()
        hl.exec_cmd("fabric-cli exec vidgex-shell 'from modules.Bar.workspaces import matrix_nav; matrix_nav(\"workspace\", \"nextR\")'")
    end
})

hl.gesture({
    fingers = 3,
    direction = "up",
    action = function()
        hl.exec_cmd("fabric-cli exec vidgex-shell 'from modules.Bar.workspaces import matrix_nav; matrix_nav(\"workspace\", \"nextD\")'")
    end
})

hl.gesture({
    fingers = 3,
    direction = "down",
    action = function()
        hl.exec_cmd("fabric-cli exec vidgex-shell 'from modules.Bar.workspaces import matrix_nav; matrix_nav(\"workspace\", \"nextU\")'")
    end
})

-- Панель
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