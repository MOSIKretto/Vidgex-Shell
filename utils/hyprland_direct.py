"""
Direct Hyprland communication utility to replace JSON parsing and reduce computational overhead.
"""
import socket
import struct
import threading
import time
from typing import Any, Dict, List, Optional, Union


class HyprlandSocket:
    """
    Direct socket communication with Hyprland to avoid JSON overhead
    """
    
    def __init__(self):
        self.sock = None
        self.lock = threading.Lock()
        self.socket_path = "/tmp/hypr/" + (self.get_instance_signature() or "") + "/.socket.sock"
        
    def get_instance_signature(self) -> Optional[str]:
        """Get Hyprland instance signature from environment"""
        import os
        return os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    
    def connect(self):
        """Establish connection to Hyprland socket"""
        if self.sock:
            return
            
        try:
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.connect(self.socket_path)
        except Exception as e:
            print(f"Failed to connect to Hyprland socket: {e}")
            self.sock = None
    
    def disconnect(self):
        """Close socket connection"""
        if self.sock:
            self.sock.close()
            self.sock = None
    
    def send_command(self, cmd: str) -> Optional[bytes]:
        """Send command directly to Hyprland socket"""
        if not self.sock:
            self.connect()
            if not self.sock:
                return None
                
        with self.lock:
            try:
                # Send command length first
                cmd_bytes = cmd.encode('utf-8')
                self.sock.send(struct.pack('I', len(cmd_bytes)))
                self.sock.send(cmd_bytes)
                
                # Receive response length
                resp_len_data = self.sock.recv(4)
                if not resp_len_data:
                    return None
                    
                resp_len = struct.unpack('I', resp_len_data)[0]
                
                # Receive response data
                response = b""
                remaining = resp_len
                while remaining > 0:
                    chunk = self.sock.recv(min(remaining, 8192))
                    if not chunk:
                        break
                    response += chunk
                    remaining -= len(chunk)
                    
                return response
            except Exception as e:
                print(f"Error sending command '{cmd}': {e}")
                # Attempt to reconnect
                try:
                    self.sock.close()
                except:
                    pass
                self.sock = None
                return None


class CachedHyprlandClient:
    """
    Hyprland client with caching to reduce API calls
    """
    
    def __init__(self):
        self.socket_client = HyprlandSocket()
        self.cache = {}
        self.cache_timestamps = {}
        self.cache_ttl = 0.5  # 500ms cache TTL
        
    def _is_cache_valid(self, key: str) -> bool:
        """Check if cache entry is still valid"""
        if key not in self.cache_timestamps:
            return False
        return time.time() - self.cache_timestamps[key] < self.cache_ttl
    
    def _get_cached_or_fetch(self, command: str, force_refresh: bool = False):
        """Get cached result or fetch from Hyprland"""
        if not force_refresh and self._is_cache_valid(command):
            return self.cache[command]
            
        result = self.socket_client.send_command(command)
        if result is not None:
            self.cache[command] = result
            self.cache_timestamps[command] = time.time()
            return result
        return self.cache.get(command)  # Return stale cache if available
    
    def get_clients(self, force_refresh: bool = False):
        """Get client list with caching"""
        data = self._get_cached_or_fetch("j/clients", force_refresh)
        if data:
            import json
            try:
                return json.loads(data.decode())
            except:
                return []
        return []
    
    def get_workspaces(self, force_refresh: bool = False):
        """Get workspace list with caching"""
        data = self._get_cached_or_fetch("j/workspaces", force_refresh)
        if data:
            import json
            try:
                return json.loads(data.decode())
            except:
                return []
        return []
    
    def get_active_workspace(self, force_refresh: bool = False):
        """Get active workspace with caching"""
        data = self._get_cached_or_fetch("j/activeworkspace", force_refresh)
        if data:
            import json
            try:
                return json.loads(data.decode())
            except:
                return {"id": 1, "name": "1"}
        return {"id": 1, "name": "1"}
    
    def get_monitors(self, force_refresh: bool = False):
        """Get monitor list with caching"""
        data = self._get_cached_or_fetch("j/monitors", force_refresh)
        if data:
            import json
            try:
                return json.loads(data.decode())
            except:
                return []
        return []
    
    def dispatch_command(self, cmd: str):
        """Send dispatch command to Hyprland"""
        # Dispatch commands are not cached
        return self.socket_client.send_command(f"dispatch {cmd}")

    def get_active_window(self, force_refresh: bool = False):
        """Get active window with caching"""
        data = self._get_cached_or_fetch("j/activewindow", force_refresh)
        if data:
            import json
            try:
                return json.loads(data.decode())
            except:
                return {}
        return {}


# Global instance
hyprland_client = CachedHyprlandClient()


def get_hyprland_client():
    """Get global Hyprland client instance"""
    return hyprland_client