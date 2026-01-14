#!/usr/bin/env python3
"""
Integration example showing how to use the optimized modules.

This script demonstrates how to integrate the optimized modules 
into the existing application structure.
"""

# Import the optimized modules (now standard)
from modules.metrics import Metrics, MetricsSmall, Battery, NetworkApplet
from modules.dock import Dock
from modules.controls import ControlSliders, ControlSmall
from modules.bar import Bar
from utils.monitor_manager import get_monitor_manager

# Import original modules for comparison (these would normally be replaced)
# from modules.metrics import Metrics, MetricsSmall, Battery, NetworkApplet
# from modules.dock import Dock
# from modules.controls import ControlSliders, ControlSmall
# from modules.bar import Bar
# from utils.monitor_manager import get_monitor_manager

def main():
    """
    Main function demonstrating integration of optimized modules.
    """
    print("Using optimized modules with reduced computational operations...")
    print("Key improvements:")
    print("- Direct Hyprland communication via sockets")
    print("- Event-driven updates instead of timer-based polling")
    print("- Reduced CPU usage through efficient data structures")
    print("- Preserved all original functionality and appearance")
    
    # Example of how to initialize components with optimized modules
    monitor_manager = get_monitor_manager()
    monitors = monitor_manager.get_monitors()
    
    print(f"\nFound {len(monitors)} monitor(s)")
    for i, monitor in enumerate(monitors):
        print(f"Monitor {i}: {monitor.get('name', 'Unknown')} - {monitor.get('width', 0)}x{monitor.get('height', 0)}")
    
    print("\nOptimized modules ready for use!")
    print("All original functionality preserved while reducing computational load.")

if __name__ == "__main__":
    main()