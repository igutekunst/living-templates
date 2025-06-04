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
                    asyncio.create_task(self.daemon.rebuild_node_instances(node_id))


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
        
        return node_id
    
    async def unregister_node(self, node_id: str) -> None:
        """Unregister a node.
        
        Args:
            node_id: ID of the node to unregister
        """
        # Remove all instances
        if node_id in self.node_instances:
            for instance in self.node_instances[node_id]:
                await self._remove_instance(instance)
            del self.node_instances[node_id]
        
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
    
    def _generate_node_id(self, config_path: Path) -> str:
        """Generate a node ID from configuration path."""
        # Use hash of absolute path for consistent IDs
        path_str = str(config_path.resolve())
        return hashlib.md5(path_str.encode()).hexdigest()[:12]
    
    async def _load_existing_state(self) -> None:
        """Load existing nodes and instances from database."""
        # This would load existing instances from the database
        # For now, we'll start fresh each time
        pass
    
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
                    self.file_watcher.add_file_watch(input_value, node.id)
    
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