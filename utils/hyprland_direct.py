"""
Direct Hyprland Socket Communication Module
Provides direct communication with Hyprland without intermediate libraries
"""

import socket
import json
import os
import threading
from typing import Dict, List, Any, Callable, Optional
from gi.repository import GLib


class HyprlandSocketCommunicator:
    """
    A direct socket-based communicator with Hyprland
    """
    
    def __init__(self):
        self.instance_signature = os.environ.get('HYPRLAND_INSTANCE_SIGNATURE', '')
        self.socket_path = f'/tmp/hypr/{self.instance_signature}/.socket.sock'
        self.event_socket_path = f'/tmp/hypr/{self.instance_signature}/.socket2.sock'
        
        self.event_callbacks = {}
        self.event_thread = None
        self.running = False
        
    def _send_command(self, command: str) -> str:
        """Send a command to Hyprland and return the response"""
        try:
            if not os.path.exists(self.socket_path):
                raise FileNotFoundError(f"Hyprland socket does not exist: {self.socket_path}")
                
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(self.socket_path)
            
            # Send command
            sock.send(command.encode())
            
            # Receive response
            response = b''
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
                
            sock.close()
            return response.decode()
        except Exception as e:
            print(f"Error sending command '{command}' to Hyprland: {e}")
            # Fallback to hyprctl if socket fails
            import subprocess
            result = subprocess.run(['hyprctl', command.replace('j/', ''), '-j'], 
                                  capture_output=True, text=True)
            return result.stdout if result.returncode == 0 else '{}'

    def send_json_command(self, command: str) -> Dict[str, Any]:
        """Send a JSON command to Hyprland and return parsed JSON response"""
        try:
            response = self._send_command(command)
            return json.loads(response) if response.strip() else {}
        except json.JSONDecodeError:
            print(f"Failed to decode JSON response for command: {command}")
            return {}
        except Exception as e:
            print(f"Error executing command {command}: {e}")
            return {}

    def send_batch_command(self, commands: List[str]) -> List[Dict[str, Any]]:
        """Send multiple commands in batch"""
        results = []
        for cmd in commands:
            results.append(self.send_json_command(cmd))
        return results

    def get_monitors(self) -> List[Dict[str, Any]]:
        """Get monitor information"""
        return self.send_json_command("j/monitors")

    def get_workspaces(self) -> List[Dict[str, Any]]:
        """Get workspace information"""
        return self.send_json_command("j/workspaces")

    def get_clients(self) -> List[Dict[str, Any]]:
        """Get client (window) information"""
        return self.send_json_command("j/clients")

    def get_active_window(self) -> Dict[str, Any]:
        """Get active window information"""
        return self.send_json_command("j/activewindow")

    def get_active_workspace(self) -> Dict[str, Any]:
        """Get active workspace information"""
        return self.send_json_command("j/activeworkspace")

    def dispatch(self, command: str) -> str:
        """Send a dispatch command to Hyprland"""
        try:
            # For dispatch commands, we don't use the j/ prefix
            full_cmd = f"dispatch {command}"
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(self.socket_path)
            sock.send(full_cmd.encode())
            response = sock.recv(1024)
            sock.close()
            return response.decode()
        except Exception as e:
            print(f"Error dispatching command '{command}': {e}")
            import subprocess
            result = subprocess.run(['hyprctl', 'dispatch', command], 
                                  capture_output=True, text=True)
            return result.stdout

    def start_event_listener(self):
        """Start listening for Hyprland events"""
        if self.event_thread and self.event_thread.is_alive():
            return
            
        self.running = True
        self.event_thread = threading.Thread(target=self._listen_for_events, daemon=True)
        self.event_thread.start()

    def stop_event_listener(self):
        """Stop listening for Hyprland events"""
        self.running = False
        if self.event_thread:
            self.event_thread.join(timeout=2.0)

    def _listen_for_events(self):
        """Internal method to listen for Hyprland events"""
        try:
            if not os.path.exists(self.event_socket_path):
                print(f"Event socket does not exist: {self.event_socket_path}")
                return
                
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(self.event_socket_path)
            
            buffer = ""
            while self.running:
                try:
                    data = sock.recv(1024).decode()
                    if not data:
                        break
                        
                    buffer += data
                    # Process complete lines
                    while '\n' in buffer:
                        line, buffer = buffer.split('\n', 1)
                        self._handle_event(line.strip())
                        
                except socket.error as e:
                    if self.running:  # Only print error if we're supposed to be running
                        print(f"Socket error in event listener: {e}")
                    break
                except Exception as e:
                    print(f"Unexpected error in event listener: {e}")
                    break
                    
            sock.close()
        except Exception as e:
            print(f"Error starting event listener: {e}")

    def _handle_event(self, event_line: str):
        """Handle incoming Hyprland events"""
        if not event_line:
            return
            
        try:
            # Events come in format: eventname>>data
            if '>>' in event_line:
                event_name, event_data = event_line.split('>>', 1)
                # Add the event data to callback queue to be processed on main thread
                GLib.idle_add(self._emit_event, event_name, event_data)
        except Exception as e:
            print(f"Error handling event: {e}")

    def _emit_event(self, event_name: str, event_data: str):
        """Emit event to registered callbacks"""
        callbacks = self.event_callbacks.get(event_name, [])
        for callback in callbacks:
            try:
                callback(event_data)
            except Exception as e:
                print(f"Error in event callback for {event_name}: {e}")

    def subscribe_to_events(self, event_names: List[str], callback: Callable[[str], None]):
        """Subscribe to specific Hyprland events"""
        for event_name in event_names:
            if event_name not in self.event_callbacks:
                self.event_callbacks[event_name] = []
            self.event_callbacks[event_name].append(callback)

    def unsubscribe_from_events(self, event_names: List[str], callback: Callable[[str], None]):
        """Unsubscribe from specific Hyprland events"""
        for event_name in event_names:
            if event_name in self.event_callbacks:
                try:
                    self.event_callbacks[event_name].remove(callback)
                    if not self.event_callbacks[event_name]:
                        del self.event_callbacks[event_name]
                except ValueError:
                    pass  # Callback was not registered

    def close(self):
        """Close the communicator"""
        self.stop_event_listener()


# Global instance
_direct_communicator = None


def get_hyprland_communicator() -> HyprlandSocketCommunicator:
    """Get the global Hyprland communicator instance"""
    global _direct_communicator
    if _direct_communicator is None:
        _direct_communicator = HyprlandSocketCommunicator()
    return _direct_communicator