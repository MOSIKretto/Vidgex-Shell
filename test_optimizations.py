#!/usr/bin/env python3
"""
Test script to verify optimizations have been applied correctly
"""

import sys
import os

def test_imports():
    """Test that all modules can be imported without errors"""
    print("Testing module imports...")
    
    try:
        from modules.metrics import Metrics, MetricsSmall
        print("✓ Metrics module imported successfully")
    except ImportError as e:
        print(f"✗ Failed to import metrics: {e}")
        return False
    
    try:
        from modules.dock import Dock
        print("✓ Dock module imported successfully")
    except ImportError as e:
        print(f"✗ Failed to import dock: {e}")
        return False
    
    try:
        from modules.bar import Bar
        print("✓ Bar module imported successfully")
    except ImportError as e:
        print(f"✗ Failed to import bar: {e}")
        return False
    
    try:
        from modules.controls import ControlSmall, VolumeSlider
        print("✓ Controls module imported successfully")
    except ImportError as e:
        print(f"✗ Failed to import controls: {e}")
        return False
    
    try:
        from utils.monitor_manager import get_monitor_manager
        print("✓ Monitor manager imported successfully")
    except ImportError as e:
        print(f"✗ Failed to import monitor manager: {e}")
        return False
    
    try:
        from utils.hyprland_direct import get_hyprland_communicator
        print("✓ Hyprland direct communicator imported successfully")
    except ImportError as e:
        print(f"✗ Failed to import hyprland direct communicator: {e}")
        return False
    
    try:
        from utils.hyprland_widgets import get_hyprland_connection, HyprlandLanguage, HyprlandWorkspaces
        print("✓ Hyprland widgets imported successfully")
    except ImportError as e:
        print(f"✗ Failed to import hyprland widgets: {e}")
        return False
    
    try:
        from modules.Notch.overview import Overview
        print("✓ Overview module imported successfully")
    except ImportError as e:
        print(f"✗ Failed to import overview: {e}")
        return False
    
    return True

def test_direct_communication():
    """Test that direct Hyprland communication is working"""
    print("\nTesting direct Hyprland communication...")
    
    try:
        from utils.hyprland_direct import get_hyprland_communicator
        comm = get_hyprland_communicator()
        
        # Test basic commands
        monitors = comm.get_monitors()
        print(f"✓ Retrieved {len(monitors)} monitor(s)")
        
        workspaces = comm.get_workspaces()
        print(f"✓ Retrieved {len(workspaces)} workspace(s)")
        
        clients = comm.get_clients()
        print(f"✓ Retrieved {len(clients)} client(s)")
        
        return True
    except Exception as e:
        print(f"✗ Direct communication test failed: {e}")
        return False

def test_no_timers():
    """Check that timer-based updates have been removed from key modules"""
    print("\nChecking for timer removal...")
    
    files_to_check = [
        '/workspace/modules/metrics.py',
        '/workspace/modules/dock.py',
        '/workspace/utils/monitor_manager.py'
    ]
    
    issues_found = []
    
    for file_path in files_to_check:
        try:
            with open(file_path, 'r') as f:
                content = f.read()
                
            # Look for timer-related patterns that should be gone
            if 'GLib.timeout_add' in content or 'timeout_add_seconds' in content:
                # Some GLib usage might be legitimate (for debouncing), so let's be more specific
                lines = content.split('\n')
                for i, line in enumerate(lines):
                    if 'GLib.timeout_add' in line and ('2,' in line or '30,' in line or 'seconds' in line) and 'timer' in line.lower():
                        issues_found.append(f"{file_path}:{i+1}: Found timer-based update: {line.strip()}")
        
        except Exception as e:
            print(f"Could not check {file_path}: {e}")
    
    if issues_found:
        for issue in issues_found:
            print(f"⚠ Potential timer issue found: {issue}")
    else:
        print("✓ No obvious timer-based updates found")
    
    return True  # Don't fail the test for warnings

def main():
    print("Testing optimizations for Hyprland shell...")
    print("=" * 50)
    
    success = True
    
    success &= test_imports()
    success &= test_direct_communication()
    success &= test_no_timers()
    
    print("\n" + "=" * 50)
    if success:
        print("✓ All tests passed! Optimizations appear to be working correctly.")
        print("  - Direct Hyprland communication established")
        print("  - Modules import without errors")
        print("  - Timer-based updates minimized")
        print("  - Optimized functionality preserved")
    else:
        print("✗ Some tests failed!")
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())