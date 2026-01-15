from fabric.hyprland.widgets import get_hyprland_connection
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label

from utils.hyprland_direct import get_hyprland_client


class Workspaces(Box):
    def __init__(self, monitor_id=0, **kwargs):
        super().__init__(name="workspaces", **kwargs)
        self.monitor_id = monitor_id
        self.conn = get_hyprland_connection()
        
        # Cache for workspace information to reduce API calls
        self._workspace_cache = {}
        self._active_workspace = None
        self._all_workspaces = []
        
        # Create container for workspace buttons
        self._container = Box(orientation="h", spacing=2)
        self.add(self._container)
        
        # Setup Hyprland event handlers for direct communication
        self._setup_hyprland_handlers()
        
        # Initial workspace setup
        self._refresh_workspaces()

    def _setup_hyprland_handlers(self):
        """Setup direct Hyprland event handlers to avoid polling"""
        events = [
            ("event::workspace", self._on_workspace_changed),
            ("event::createworkspace", self._on_workspace_created),
            ("event::destroyworkspace", self._on_workspace_destroyed),
            ("event::movewindow", self._on_window_moved),
        ]
        
        for event_name, handler in events:
            self.conn.connect(event_name, handler)

    def _get_hyprland_workspaces(self):
        """Direct communication with Hyprland to get workspace information"""
        try:
            client = get_hyprland_client()
            return client.get_workspaces()
        except Exception:
            return []

    def _get_hyprland_active_workspace(self):
        """Direct communication with Hyprland to get active workspace"""
        try:
            client = get_hyprland_client()
            return client.get_active_workspace()
        except Exception:
            return {"id": 1, "name": "1"}

    def _refresh_workspaces(self):
        """Refresh workspace display based on current Hyprland state"""
        workspaces_data = self._get_hyprland_workspaces()
        active_workspace_data = self._get_hyprland_active_workspace()
        
        self._all_workspaces = workspaces_data
        self._active_workspace = active_workspace_data["id"]
        
        # Clear existing buttons
        for child in self._container.get_children():
            child.destroy()
        
        # Create new workspace buttons
        # We'll create a reasonable range of workspaces instead of all of them
        workspace_ids = sorted([ws["id"] for ws in workspaces_data])
        
        # If no workspaces exist yet, create a default range
        if not workspace_ids:
            workspace_ids = list(range(1, 6))  # Default to 5 workspaces
        
        for ws_id in workspace_ids:
            btn = self._create_workspace_button(ws_id)
            self._container.add(btn)
        
        self._update_workspace_states()
        self.show_all()

    def _create_workspace_button(self, workspace_id):
        """Create a single workspace button"""
        btn = Button(
            name="workspace-btn",
            child=Label(label=str(workspace_id)),
            tooltip_text=f"Workspace {workspace_id}",
        )
        
        # Store workspace ID in button for later reference
        btn.workspace_id = workspace_id
        
        # Connect click event to switch workspace
        btn.connect("clicked", self._on_workspace_clicked)
        
        return btn

    def _on_workspace_clicked(self, button):
        """Handle workspace button click"""
        workspace_id = button.workspace_id
        # Use direct Hyprland command to switch workspace
        self.conn.send_command(f"dispatch workspace {workspace_id}")

    def _update_workspace_states(self):
        """Update visual state of workspace buttons"""
        for child in self._container.get_children():
            ws_id = child.workspace_id
            if ws_id == self._active_workspace:
                child.add_style_class("active")
            else:
                child.remove_style_class("active")
                
            # Check if workspace has windows
            has_windows = any(
                ws.get("id") == ws_id and ws.get("windows", 0) > 0
                for ws in self._all_workspaces
            )
            
            if has_windows:
                child.add_style_class("occupied")
            else:
                child.remove_style_class("occupied")

    def _on_workspace_changed(self, *args):
        """Handle workspace change event from Hyprland"""
        try:
            client = get_hyprland_client()
            active_ws = client.get_active_workspace()
            self._active_workspace = active_ws["id"]
        except Exception:
            pass
        
        self._update_workspace_states()

    def _on_workspace_created(self, conn, event_data):
        """Handle workspace creation event"""
        try:
            # Get the workspace ID from event data or query Hyprland
            workspace_id = int(event_data.data.split(" ")[1])  # Parse from event data
            # Refresh the entire workspace list to maintain order
            self._refresh_workspaces()
        except:
            # If parsing fails, refresh anyway
            self._refresh_workspaces()

    def _on_workspace_destroyed(self, conn, event_data):
        """Handle workspace destruction event"""
        try:
            workspace_id = int(event_data.data.split(" ")[1])  # Parse from event data
            # Find and remove the specific button
            for child in self._container.get_children():
                if hasattr(child, 'workspace_id') and child.workspace_id == workspace_id:
                    child.destroy()
                    break
            # Update states after removal
            self._update_workspace_states()
        except:
            # If parsing fails, refresh everything
            self._refresh_workspaces()

    def _on_window_moved(self, *args):
        """Handle window movement event - affects workspace occupancy"""
        # Refresh workspace data since window counts may have changed
        self._all_workspaces = self._get_hyprland_workspaces()
        self._update_workspace_states()

    def destroy(self):
        """Clean up resources"""
        # Disconnect from Hyprland events
        # The connection cleanup happens at application level
        super().destroy()