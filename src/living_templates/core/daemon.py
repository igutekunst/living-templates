"""Core daemon for Living Templates."""

import asyncio
import hashlib
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .config import ConfigManager
from .models import NodeInstance, NodeType, NodeValue, TemplateNode
from .storage import ContentStore, Database, SymlinkManager
from .template_engine import TemplateEngine


class FileWatcher(FileSystemEventHandler):
    """File system event handler for watching dependencies."""
    
    def __init__(self, daemon: 'LivingTemplatesDaemon'):
        """Initialize file watcher.
        
        Args:
            daemon: Reference to the main daemon
        """
        self.daemon = daemon
        self.watched_files: Dict[str, List[str]] = {}  # file_path -> [node_ids]
    
    def add_file_watch(self, file_path: str, node_id: str) -> None:
        """Add a file to watch for a specific node."""
        if file_path not in self.watched_files:
            self.watched_files[file_path] = []
        if node_id not in self.watched_files[file_path]:
            self.watched_files[file_path].append(node_id)
    
    def remove_file_watch(self, file_path: str, node_id: str) -> None:
        """Remove file watch for a specific node."""
        if file_path in self.watched_files:
            if node_id in self.watched_files[file_path]:
                self.watched_files[file_path].remove(node_id)
            if not self.watched_files[file_path]:
                del self.watched_files[file_path]
    
    def on_modified(self, event) -> None:
        """Handle file modification events."""
        if not event.is_directory:
            file_path = event.src_path
            if file_path in self.watched_files:
                # Schedule rebuild for all nodes watching this file
                for node_id in self.watched_files[file_path]:
                    if self.daemon.event_loop and not self.daemon.event_loop.is_closed():
                        asyncio.run_coroutine_threadsafe(
                            self.daemon.handle_file_change(node_id, file_path),
                            self.daemon.event_loop
                        )


class LivingTemplatesDaemon:
    """Main daemon for Living Templates."""
    
    def __init__(self, config_dir: Optional[Path] = None):
        """Initialize daemon.
        
        Args:
            config_dir: Configuration directory. Defaults to ~/.living-templates
        """
        self.config_manager = ConfigManager(config_dir)
        self.db = Database(self.config_manager.db_path)
        self.content_store = ContentStore(self.config_manager.store_path)
        self.symlink_manager = SymlinkManager()
        self.template_engine = TemplateEngine()
        
        # File watching
        self.file_watcher = FileWatcher(self)
        self.observer = Observer()
        
        # Runtime state
        self.running = False
        self.node_instances: Dict[str, List[NodeInstance]] = {}  # node_id -> instances
        self.event_loop = None  # Store reference to the event loop
    
    async def initialize(self) -> None:
        """Initialize the daemon."""
        await self.db.initialize()
        
        # Load existing nodes and instances
        await self._load_existing_state()
    
    async def start(self) -> None:
        """Start the daemon."""
        if self.running:
            return
        
        await self.initialize()
        
        # Store reference to the current event loop
        self.event_loop = asyncio.get_running_loop()
        
        # Start file watching
        self.observer.schedule(self.file_watcher, "/", recursive=True)
        self.observer.start()
        
        self.running = True
        
        # Write PID file
        pid_file = self.config_manager.daemon_pid_path
        pid_file.write_text(str(os.getpid()))
    
    async def stop(self) -> None:
        """Stop the daemon."""
        if not self.running:
            return
        
        self.running = False
        
        # Stop file watching
        self.observer.stop()
        self.observer.join()
        
        # Remove PID file
        pid_file = self.config_manager.daemon_pid_path
        if pid_file.exists():
            pid_file.unlink()
    
    async def register_node(self, config_path: Path) -> str:
        """Register a new node from configuration file.
        
        Args:
            config_path: Path to the configuration file
            
        Returns:
            Node ID
        """
        # Parse configuration
        config, content = self.config_manager.load_node_config(config_path)
        
        # Generate node ID from config path
        node_id = self._generate_node_id(config_path)
        
        # Create node
        node = TemplateNode(
            id=node_id,
            config=config,
            config_path=config_path
        )
        
        # Store in database
        await self.db.store_node(node)
        
        # Watch the config file itself for changes
        if config_path.exists():
            self.file_watcher.add_file_watch(str(config_path.resolve()), node_id)
        
        return node_id
    
    async def unregister_node(self, node_id: str) -> None:
        """Unregister a node.
        
        Args:
            node_id: ID of the node to unregister
        """
        # Get node info before removing
        node = await self.db.get_node(node_id)
        
        # Remove all instances
        if node_id in self.node_instances:
            for instance in self.node_instances[node_id]:
                await self._remove_instance(instance)
            del self.node_instances[node_id]
        
        # Remove config file watch
        if node and node.config_path:
            self.file_watcher.remove_file_watch(str(node.config_path.resolve()), node_id)
        
        # Remove from database
        await self.db.remove_node(node_id)
    
    async def create_instance(
        self, 
        node_id: str, 
        output_path: str, 
        input_values: Dict[str, Any]
    ) -> str:
        """Create a new instance of a node.
        
        Args:
            node_id: ID of the node to instantiate
            output_path: Where to create the output file
            input_values: Input values for the instance
            
        Returns:
            Instance ID
        """
        # Get node configuration
        node = await self.db.get_node(node_id)
        if not node:
            raise ValueError(f"Node not found: {node_id}")
        
        # Generate instance ID
        instance_id = str(uuid.uuid4())
        
        # Create instance
        instance = NodeInstance(
            id=instance_id,
            node_id=node_id,
            input_values=input_values,
            output_path=output_path
        )
        
        # Store instance
        await self.db.store_node_instance(instance)
        
        # Add to runtime state
        if node_id not in self.node_instances:
            self.node_instances[node_id] = []
        self.node_instances[node_id].append(instance)
        
        # Set up file watching for file inputs
        await self._setup_file_watching(node, instance)
        
        # Initial build
        await self._build_instance(node, instance)
        
        return instance_id
    
    async def rebuild_node_instances(self, node_id: str) -> None:
        """Rebuild all instances of a node.
        
        Args:
            node_id: ID of the node to rebuild
        """
        if node_id not in self.node_instances:
            return
        
        node = await self.db.get_node(node_id)
        if not node:
            return
        
        for instance in self.node_instances[node_id]:
            await self._build_instance(node, instance)
    
    async def handle_file_change(self, node_id: str, file_path: str) -> None:
        """Handle a file change event.
        
        Args:
            node_id: ID of the node that watches this file
            file_path: Path of the file that changed
        """
        node = await self.db.get_node(node_id)
        if not node:
            return
        
        # Check if this is the config file itself
        if node.config_path and str(node.config_path.resolve()) == file_path:
            # Config file changed - reload configuration and rebuild all instances
            try:
                # Reload configuration
                config, content = self.config_manager.load_node_config(node.config_path)
                
                # Update node in database
                updated_node = TemplateNode(
                    id=node_id,
                    config=config,
                    config_path=node.config_path,
                    created_at=node.created_at  # Preserve original creation time
                )
                await self.db.store_node(updated_node)
                
                # Rebuild all instances with new config
                await self.rebuild_node_instances(node_id)
                
                print(f"Reloaded config and rebuilt instances for node {node_id}")
                
            except Exception as e:
                print(f"Error reloading config for node {node_id}: {e}")
        else:
            # Input file changed - just rebuild instances
            await self.rebuild_node_instances(node_id)
    
    async def get_status(self) -> Dict[str, Any]:
        """Get daemon status."""
        nodes = await self.db.list_nodes()
        total_instances = sum(len(instances) for instances in self.node_instances.values())
        
        return {
            "daemon_running": self.running,
            "active_nodes": len(nodes),
            "total_instances": total_instances,
            "last_update": datetime.now().isoformat(),
            "version": "0.1.0"
        }
    
    async def list_nodes(self) -> List[TemplateNode]:
        """List all registered nodes."""
        return await self.db.list_nodes()
    
    async def get_node_inputs(self, node_id: str) -> Dict[str, Any]:
        """Get input information for a specific node.
        
        Args:
            node_id: ID of the node
            
        Returns:
            Dictionary containing input specifications and current instances
        """
        node = await self.db.get_node(node_id)
        if not node:
            raise ValueError(f"Node not found: {node_id}")
        
        # Get input specifications from node config
        input_specs = {}
        for input_name, input_spec in node.config.inputs.items():
            input_specs[input_name] = {
                "type": input_spec.type.value,
                "description": input_spec.description,
                "default": input_spec.default,
                "required": input_spec.required
            }
        
        # Get current instances and their input values
        instances = []
        if node_id in self.node_instances:
            for instance in self.node_instances[node_id]:
                instances.append({
                    "instance_id": instance.id,
                    "output_path": instance.output_path,
                    "input_values": instance.input_values
                })
        
        return {
            "node_id": node_id,
            "config_path": str(node.config_path) if node.config_path else None,
            "input_specifications": input_specs,
            "active_instances": instances
        }
    
    async def get_watched_files(self, node_id: Optional[str] = None) -> Dict[str, Any]:
        """Get information about watched files.
        
        Args:
            node_id: Optional node ID to filter by. If None, returns all watched files.
            
        Returns:
            Dictionary containing watched file information
        """
        if node_id:
            # Filter for specific node
            watched_files = {}
            for file_path, watching_nodes in self.file_watcher.watched_files.items():
                if node_id in watching_nodes:
                    watched_files[file_path] = [node_id]
            
            return {
                "node_id": node_id,
                "watched_files": watched_files,
                "total_files": len(watched_files)
            }
        else:
            # Return all watched files
            return {
                "watched_files": dict(self.file_watcher.watched_files),
                "total_files": len(self.file_watcher.watched_files),
                "total_watchers": sum(len(nodes) for nodes in self.file_watcher.watched_files.values())
            }
    
    async def get_node_file_inputs(self, node_id: str) -> List[Dict[str, Any]]:
        """Get file inputs for a specific node across all instances.
        
        Args:
            node_id: ID of the node
            
        Returns:
            List of file input information
        """
        node = await self.db.get_node(node_id)
        if not node:
            raise ValueError(f"Node not found: {node_id}")
        
        file_inputs = []
        
        # Check all instances of this node
        if node_id in self.node_instances:
            for instance in self.node_instances[node_id]:
                for input_name, input_value in instance.input_values.items():
                    input_spec = node.config.inputs.get(input_name)
                    if input_spec and input_spec.type.value == "file":
                        file_path = str(input_value) if isinstance(input_value, str) else str(input_value)
                        file_exists = Path(file_path).exists() if isinstance(input_value, str) else False
                        is_watched = file_path in self.file_watcher.watched_files
                        
                        file_inputs.append({
                            "instance_id": instance.id,
                            "input_name": input_name,
                            "file_path": file_path,
                            "exists": file_exists,
                            "is_watched": is_watched,
                            "output_path": instance.output_path
                        })
        
        return file_inputs
    
    def _generate_node_id(self, config_path: Path) -> str:
        """Generate a node ID from configuration path."""
        # Use hash of absolute path for consistent IDs
        path_str = str(config_path.resolve())
        return hashlib.md5(path_str.encode()).hexdigest()[:12]
    
    async def _load_existing_state(self) -> None:
        """Load existing nodes and instances from database."""
        # Load all nodes and set up config file watching
        nodes = await self.db.list_nodes()
        for node in nodes:
            if node.config_path and node.config_path.exists():
                self.file_watcher.add_file_watch(str(node.config_path.resolve()), node.id)
        
        # Load all instances and set up file watching
        instances = await self.db.get_node_instances()
        for instance in instances:
            # Add to runtime state
            if instance.node_id not in self.node_instances:
                self.node_instances[instance.node_id] = []
            self.node_instances[instance.node_id].append(instance)
            
            # Set up file watching for this instance
            node = await self.db.get_node(instance.node_id)
            if node:
                await self._setup_file_watching(node, instance)
    
    async def _setup_file_watching(self, node: TemplateNode, instance: NodeInstance) -> None:
        """Set up file watching for an instance.
        
        Args:
            node: The node configuration
            instance: The instance to watch files for
        """
        # Watch files specified in input values
        for input_name, input_value in instance.input_values.items():
            input_spec = node.config.inputs.get(input_name)
            if input_spec and input_spec.type.value == "file":
                if isinstance(input_value, str) and Path(input_value).exists():
                    # Convert to absolute path for consistent watching
                    abs_path = str(Path(input_value).resolve())
                    self.file_watcher.add_file_watch(abs_path, node.id)
    
    async def _build_instance(self, node: TemplateNode, instance: NodeInstance) -> None:
        """Build/rebuild an instance.
        
        Args:
            node: The node configuration
            instance: The instance to build
        """
        if node.config.node_type == NodeType.TEMPLATE:
            await self._build_template_instance(node, instance)
        else:
            # Other node types not implemented yet
            pass
    
    async def _build_template_instance(self, node: TemplateNode, instance: NodeInstance) -> None:
        """Build a template instance.
        
        Args:
            node: The template node
            instance: The instance to build
        """
        # Resolve input values
        context = await self._resolve_input_values(node, instance)
        
        # Render template
        rendered_content = self.template_engine.render(
            node.config.template_content or "",
            context
        )
        
        # Store content
        content_hash, content_path = self.content_store.store_content(rendered_content)
        
        # Create symlink
        target_path = Path(instance.output_path)
        self.symlink_manager.create_symlink(target_path, content_path)
        
        # Store metadata
        for output_name in node.config.outputs:
            value = NodeValue(
                node_id=node.id,
                output_name=output_name,
                value_hash=content_hash,
                value_data=rendered_content,
                content_path=str(content_path)
            )
            await self.db.store_node_value(value)
        
        # Store symlink info
        await self.db.store_symlink(
            str(target_path),
            content_hash,
            instance.id
        )
    
    async def _resolve_input_values(
        self, 
        node: TemplateNode, 
        instance: NodeInstance
    ) -> Dict[str, Any]:
        """Resolve input values for an instance.
        
        Args:
            node: The node configuration
            instance: The instance
            
        Returns:
            Resolved input values
        """
        context = {}
        
        for input_name, input_spec in node.config.inputs.items():
            if input_name in instance.input_values:
                # Use provided value
                value = instance.input_values[input_name]
            elif input_spec.default is not None:
                # Use default value
                value = input_spec.default
            else:
                # Required input missing
                raise ValueError(f"Required input '{input_name}' not provided")
            
            # Handle file inputs
            if input_spec.type.value == "file" and isinstance(value, str):
                file_path = Path(value)
                if file_path.exists():
                    context[input_name] = str(file_path.resolve())
                else:
                    context[input_name] = value
            else:
                context[input_name] = value
        
        return context
    
    async def _remove_instance(self, instance: NodeInstance) -> None:
        """Remove an instance and clean up.
        
        Args:
            instance: The instance to remove
        """
        # Remove symlink
        target_path = Path(instance.output_path)
        self.symlink_manager.remove_symlink(target_path)
        
        # Remove from file watching
        node = await self.db.get_node(instance.node_id)
        if node:
            for input_name, input_value in instance.input_values.items():
                input_spec = node.config.inputs.get(input_name)
                if input_spec and input_spec.type.value == "file":
                    if isinstance(input_value, str):
                        self.file_watcher.remove_file_watch(input_value, node.id) 