"""Core daemon for Living Templates."""

import asyncio
import hashlib
import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from aiohttp import web
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .config import ConfigManager
from .executor import ProgramExecutor
from .models import (
    ExecutionLog, InputMode, LogLevel, NodeInstance, NodeReference, NodeType, 
    NodeValue, OutputMode, TemplateNode, TailState, WebhookTrigger
)
from .storage import ContentStore, Database, SymlinkManager
from .tail_watcher import TailWatcher
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
        self.program_executor = ProgramExecutor()
        self.tail_watcher = TailWatcher()
        
        # File watching
        self.file_watcher = FileWatcher(self)
        self.observer = Observer()
        
        # API server
        self.app = None
        self.api_server = None
        self.api_port = 8765  # Default port for API
        
        # Runtime state
        self.running = False
        self.node_instances: Dict[str, List[NodeInstance]] = {}  # node_id -> instances
        self.event_loop = None  # Store reference to the event loop
        
        # Background tasks
        self.webhook_processor_task = None
    
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
        
        # Start API server
        await self._start_api_server()
        
        # Start file watching
        self.observer.schedule(self.file_watcher, "/", recursive=True)
        self.observer.start()
        
        # Start tail watcher
        await self.tail_watcher.start_watching()
        
        # Start webhook processor
        self.webhook_processor_task = asyncio.create_task(self._process_webhooks())
        
        self.running = True
        
        # Write PID file
        pid_file = self.config_manager.daemon_pid_path
        pid_file.write_text(str(os.getpid()))
    
    async def stop(self) -> None:
        """Stop the daemon."""
        if not self.running:
            return
        
        self.running = False
        
        # Stop background tasks
        if self.webhook_processor_task:
            self.webhook_processor_task.cancel()
            try:
                await self.webhook_processor_task
            except asyncio.CancelledError:
                pass
        
        # Stop tail watcher
        await self.tail_watcher.stop_watching()
        
        # Stop API server
        if self.api_server:
            await self.api_server.cleanup()
        
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
        
        # Set up node-specific watchers
        await self._setup_node_watchers(node)
        
        # Log registration
        await self._log(node_id, LogLevel.INFO, f"Node registered: {config_path}")
        
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
        
        # Remove tail watchers
        if node and node.config.node_type == NodeType.TAIL:
            for input_spec in node.config.inputs.values():
                if input_spec.type.value == "file":
                    # Remove from tail watcher
                    pass  # TailWatcher will clean up automatically
        
        # Remove from database
        await self.db.remove_node(node_id)
        
        # Log unregistration
        await self._log(node_id, LogLevel.INFO, "Node unregistered")
    
    async def create_instance(
        self, 
        node_id: str, 
        output_path: str, 
        input_values: Dict[str, Any]
    ) -> str:
        """Create a new instance of a node.
        
        Args:
            node_id: ID of the node
            output_path: Path where output should be written
            input_values: Input values for the instance
            
        Returns:
            Instance ID
        """
        instance_id = str(uuid.uuid4())
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
        
        # Get node and set up file watching for this instance
        node = await self.db.get_node(node_id)
        if node:
            await self._setup_file_watching(node, instance)
            
            # Build the instance
            await self._build_instance(node, instance)
        
        await self._log(node_id, LogLevel.INFO, f"Instance created: {instance_id}", {"output_path": output_path})
        
        return instance_id
    
    async def rebuild_node_instances(self, node_id: str) -> None:
        """Rebuild all instances of a node.
        
        Args:
            node_id: ID of the node to rebuild
        """
        node = await self.db.get_node(node_id)
        if not node:
            return
        
        if node_id in self.node_instances:
            for instance in self.node_instances[node_id]:
                await self._build_instance(node, instance)
        
        await self._log(node_id, LogLevel.INFO, "Node instances rebuilt")
    
    async def handle_file_change(self, node_id: str, file_path: str) -> None:
        """Handle a file change event.
        
        Args:
            node_id: ID of the node that watches the file
            file_path: Path of the changed file
        """
        await self._log(node_id, LogLevel.DEBUG, f"File change detected: {file_path}")
        
        # Check if it's the config file itself
        node = await self.db.get_node(node_id)
        if node and node.config_path and str(node.config_path) == file_path:
            # For program nodes, if the config file is the script file, we should reload but be careful about loops
            if node.config.node_type == NodeType.PROGRAM and node.config.script_path:
                script_path = Path(node.config.script_path)
                if not script_path.is_absolute():
                    script_path = Path.cwd() / script_path
                
                # If the config file is the same as the script file, avoid reload during execution
                if str(script_path.resolve()) == str(Path(file_path).resolve()):
                    await self._log(node_id, LogLevel.DEBUG, "Ignoring script file change during execution to prevent loops")
                    return
            
            # Config file changed, reload node
            try:
                await self.unregister_node(node_id)
                await self.register_node(node.config_path)
                await self._log(node_id, LogLevel.INFO, "Node reloaded due to config change")
            except Exception as e:
                await self._log(node_id, LogLevel.ERROR, f"Failed to reload node: {e}")
            return
        
        # Rebuild node instances that depend on this file
        await self.rebuild_node_instances(node_id)
    
    async def handle_tail_change(self, node_id: str, new_lines: List[str]) -> None:
        """Handle new lines from tail watcher.
        
        Args:
            node_id: ID of the tail node
            new_lines: New lines detected
        """
        await self._log(node_id, LogLevel.DEBUG, f"Tail change detected: {len(new_lines)} new lines")
        
        node = await self.db.get_node(node_id)
        if not node or node.config.node_type != NodeType.TAIL:
            return
        
        # Process new lines through transform if available
        if node.config.transform:
            try:
                # Execute transform code on each line
                processed_lines = []
                for line in new_lines:
                    # Create a simple execution environment
                    exec_globals = {"line": line}
                    exec(node.config.transform, exec_globals)
                    if "result" in exec_globals:
                        processed_lines.append(exec_globals["result"])
                
                new_lines = processed_lines
            except Exception as e:
                await self._log(node_id, LogLevel.ERROR, f"Transform error: {e}")
                return
        
        # Build instances with new data
        if node_id in self.node_instances:
            for instance in self.node_instances[node_id]:
                # Add new lines to context
                context = {"new_lines": new_lines, "line_count": len(new_lines)}
                
                # Handle output mode
                if node.config.output_mode == OutputMode.APPEND:
                    content = "\n".join(new_lines) + "\n"
                    target_path = Path(instance.output_path)
                    self.symlink_manager.append_to_file(target_path, content)
                elif node.config.output_mode == OutputMode.PREPEND:
                    content = "\n".join(new_lines) + "\n"
                    target_path = Path(instance.output_path)
                    self.symlink_manager.prepend_to_file(target_path, content)
                # For other modes, would need template processing
    
    async def trigger_webhook(self, node_id: str, webhook_data: Dict[str, Any]) -> None:
        """Trigger a webhook node.
        
        Args:
            node_id: ID of the webhook node
            webhook_data: Webhook trigger data
        """
        trigger = WebhookTrigger(
            node_id=node_id,
            data=webhook_data.get("data", {}),
            headers=webhook_data.get("headers", {})
        )
        
        await self.db.store_webhook_trigger(trigger)
        await self._log(node_id, LogLevel.INFO, "Webhook triggered")
    
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
        """Get input specifications for a node."""
        node = await self.db.get_node(node_id)
        if not node:
            raise ValueError(f"Node not found: {node_id}")
        
        inputs_info = {}
        for input_name, input_spec in node.config.inputs.items():
            inputs_info[input_name] = {
                "type": input_spec.type.value,
                "description": input_spec.description,
                "default": input_spec.default,
                "required": input_spec.required,
                "source": input_spec.source
            }
        
        return {
            "node_id": node_id,
            "inputs": inputs_info,
            "node_type": node.config.node_type.value
        }
    
    async def get_watched_files(self, node_id: Optional[str] = None) -> Dict[str, Any]:
        """Get information about watched files."""
        if node_id:
            # Get files watched by specific node
            watched_files = []
            for file_path, watching_nodes in self.file_watcher.watched_files.items():
                if node_id in watching_nodes:
                    watched_files.append({
                        "file_path": file_path,
                        "exists": Path(file_path).exists()
                    })
            
            return {
                "node_id": node_id,
                "watched_files": watched_files
            }
        else:
            # Get all watched files
            all_watched = {}
            for file_path, watching_nodes in self.file_watcher.watched_files.items():
                all_watched[file_path] = {
                    "watching_nodes": watching_nodes,
                    "exists": Path(file_path).exists()
                }
            
            # Add tail watched files
            tail_watched = {}
            for file_path in self.tail_watcher.get_watched_files():
                tail_watched[file_path] = {
                    "type": "tail",
                    "exists": Path(file_path).exists()
                }
            
            return {
                "file_watched": all_watched,
                "tail_watched": tail_watched
            }
    
    async def get_node_file_inputs(self, node_id: str) -> List[Dict[str, Any]]:
        """Get file inputs for a node."""
        node = await self.db.get_node(node_id)
        if not node:
            raise ValueError(f"Node not found: {node_id}")
        
        file_inputs = []
        for input_name, input_spec in node.config.inputs.items():
            if input_spec.type.value == "file":
                file_inputs.append({
                    "input_name": input_name,
                    "description": input_spec.description,
                    "required": input_spec.required,
                    "default": input_spec.default
                })
        
        return file_inputs
    
    async def get_dependency_graph(self, node_id: Optional[str] = None) -> Dict[str, Any]:
        """Get dependency graph information."""
        nodes = await self.db.list_nodes()
        
        graph_nodes = []
        graph_edges = []
        
        for node in nodes:
            if node_id and node.id != node_id:
                continue
            
            # Add node to graph
            graph_nodes.append({
                "id": node.id,
                "type": node.config.node_type.value,
                "outputs": node.config.outputs,
                "config_path": str(node.config_path) if node.config_path else None
            })
            
            # Find dependencies by scanning for node references
            dependencies = await self._extract_node_dependencies(node)
            for dep in dependencies:
                graph_edges.append({
                    "from": dep.dependency_node_id,
                    "to": dep.dependent_node_id,
                    "output": dep.dependency_output
                })
        
        return {
            "nodes": graph_nodes,
            "edges": graph_edges,
            "focus_node": node_id
        }
    
    async def get_node_logs(self, node_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Get execution logs for a node."""
        logs = await self.db.get_execution_logs(node_id, limit)
        return [
            {
                "id": log.id,
                "level": log.level.value,
                "message": log.message,
                "details": log.details,
                "timestamp": log.timestamp.isoformat(),
                "instance_id": log.instance_id
            }
            for log in logs
        ]
    
    def _generate_node_id(self, config_path: Path) -> str:
        """Generate a unique node ID from config path."""
        # Use relative path if possible, fall back to absolute
        try:
            rel_path = config_path.relative_to(Path.cwd())
            path_str = str(rel_path)
        except ValueError:
            path_str = str(config_path)
        
        return hashlib.md5(path_str.encode()).hexdigest()[:12]
    
    async def _load_existing_state(self) -> None:
        """Load existing nodes and instances from database."""
        nodes = await self.db.list_nodes()
        
        for node in nodes:
            instances = await self.db.get_node_instances(node.id)
            self.node_instances[node.id] = instances
            
            # Set up file watching for existing instances
            for instance in instances:
                await self._setup_file_watching(node, instance)
            
            # Set up node-specific watchers
            await self._setup_node_watchers(node)
    
    async def _setup_node_watchers(self, node: TemplateNode) -> None:
        """Set up watchers specific to node type."""
        # For program nodes, only watch the config file if it's different from the script file
        if node.config.node_type == NodeType.PROGRAM:
            if node.config_path and node.config_path.exists():
                # Check if config file is different from script file
                if node.config.script_path:
                    script_path = Path(node.config.script_path)
                    if not script_path.is_absolute():
                        script_path = Path.cwd() / script_path
                    
                    # Only watch config file if it's different from script file
                    if str(node.config_path.resolve()) != str(script_path.resolve()):
                        self.file_watcher.add_file_watch(str(node.config_path.resolve()), node.id)
                else:
                    # No script path, so watch config file
                    self.file_watcher.add_file_watch(str(node.config_path.resolve()), node.id)
        else:
            # For non-program nodes, watch the config file normally
            if node.config_path and node.config_path.exists():
                self.file_watcher.add_file_watch(str(node.config_path.resolve()), node.id)
        
        # Set up node-type specific watching
        if node.config.node_type == NodeType.TAIL and node.config.input_mode == InputMode.TAIL:
            # Set up tail watching for file inputs
            for input_name, input_spec in node.config.inputs.items():
                if input_spec.type.value == "file":
                    # This would be set up per instance with actual file paths
                    pass
    
    async def _setup_file_watching(self, node: TemplateNode, instance: NodeInstance) -> None:
        """Set up file watching for a node instance."""
        # Watch file inputs
        for input_name, input_value in instance.input_values.items():
            input_spec = node.config.inputs.get(input_name)
            if input_spec and input_spec.type.value == "file":
                if isinstance(input_value, str):
                    file_path = str(Path(input_value).resolve())
                    
                    if node.config.input_mode == InputMode.TAIL:
                        # Set up tail watching
                        self.tail_watcher.add_file_watch(
                            node.id,
                            file_path,
                            self.handle_tail_change,
                            node.config.tail_lines
                        )
                    else:
                        # Regular file watching
                        self.file_watcher.add_file_watch(file_path, node.id)
    
    async def _build_instance(self, node: TemplateNode, instance: NodeInstance) -> None:
        """Build/rebuild an instance.
        
        Args:
            node: The node configuration
            instance: The instance to build
        """
        try:
            await self._log(node.id, LogLevel.DEBUG, f"Building instance: {instance.id}")
            
            if node.config.node_type == NodeType.TEMPLATE:
                await self._build_template_instance(node, instance)
            elif node.config.node_type == NodeType.PROGRAM:
                await self._build_program_instance(node, instance)
            elif node.config.node_type == NodeType.WEBHOOK:
                await self._build_webhook_instance(node, instance)
            elif node.config.node_type == NodeType.TAIL:
                await self._build_tail_instance(node, instance)
            else:
                await self._log(node.id, LogLevel.WARNING, f"Unsupported node type: {node.config.node_type}")
            
            # Update instance metadata
            instance.last_built = datetime.now()
            instance.build_count += 1
            await self.db.store_node_instance(instance)
            
        except Exception as e:
            await self._log(node.id, LogLevel.ERROR, f"Build failed for instance {instance.id}: {e}")
            raise
    
    async def _build_template_instance(self, node: TemplateNode, instance: NodeInstance) -> None:
        """Build a template instance."""
        # Resolve input values
        context = await self._resolve_input_values(node, instance)
        
        # Render template
        rendered_content = self.template_engine.render(
            node.config.template_content or "",
            context
        )
        
        # Handle output mode
        target_path = Path(instance.output_path)
        
        if node.config.output_mode == OutputMode.REPLACE:
            # Store content and create symlink
            content_hash, content_path = self.content_store.store_content(rendered_content)
            self.symlink_manager.create_symlink(target_path, content_path)
            
            # Store symlink info
            await self.db.store_symlink(
                str(target_path),
                content_hash,
                instance.id
            )
        elif node.config.output_mode == OutputMode.APPEND:
            self.symlink_manager.append_to_file(target_path, rendered_content)
        elif node.config.output_mode == OutputMode.PREPEND:
            self.symlink_manager.prepend_to_file(target_path, rendered_content)
        elif node.config.output_mode == OutputMode.CONCATENATE:
            # For concatenate, we append but with some separator logic
            separator = "\n" if not rendered_content.endswith("\n") else ""
            self.symlink_manager.append_to_file(target_path, separator + rendered_content)
        
        # Store node values
        for output_name in node.config.outputs:
            value = NodeValue(
                node_id=node.id,
                output_name=output_name,
                value_hash=hashlib.md5(rendered_content.encode()).hexdigest(),
                value_data=rendered_content
            )
            await self.db.store_node_value(value)
    
    async def _build_program_instance(self, node: TemplateNode, instance: NodeInstance) -> None:
        """Build a program instance."""
        # Resolve input values
        input_values = await self._resolve_input_values(node, instance)
        
        # Execute program
        output_files, logs = await self.program_executor.execute_program(node, instance, input_values)
        
        # Store execution logs
        for log in logs:
            await self.db.store_execution_log(log)
        
        # Handle output files
        target_path = Path(instance.output_path)
        
        if len(output_files) == 1:
            # Single output file
            output_file = Path(output_files[0])
            if output_file.exists():
                content = output_file.read_text(encoding='utf-8')
                
                if node.config.output_mode == OutputMode.REPLACE:
                    content_hash, content_path = self.content_store.store_content(content)
                    self.symlink_manager.create_symlink(target_path, content_path)
                    await self.db.store_symlink(str(target_path), content_hash, instance.id)
                elif node.config.output_mode == OutputMode.APPEND:
                    self.symlink_manager.append_to_file(target_path, content)
                elif node.config.output_mode == OutputMode.PREPEND:
                    self.symlink_manager.prepend_to_file(target_path, content)
                elif node.config.output_mode == OutputMode.CONCATENATE:
                    separator = "\n" if not content.endswith("\n") else ""
                    self.symlink_manager.append_to_file(target_path, separator + content)
        elif len(output_files) > 1:
            # Multiple output files - copy to output directory
            target_path.mkdir(parents=True, exist_ok=True)
            for output_file in output_files:
                src_path = Path(output_file)
                if src_path.exists():
                    dst_path = target_path / src_path.name
                    # Handle different output modes for multiple files
                    content = src_path.read_text(encoding='utf-8')
                    if node.config.output_mode == OutputMode.REPLACE:
                        dst_path.write_text(content, encoding='utf-8')
                    elif node.config.output_mode == OutputMode.APPEND:
                        if dst_path.exists():
                            existing_content = dst_path.read_text(encoding='utf-8')
                            dst_path.write_text(existing_content + content, encoding='utf-8')
                        else:
                            dst_path.write_text(content, encoding='utf-8')
                    elif node.config.output_mode == OutputMode.PREPEND:
                        if dst_path.exists():
                            existing_content = dst_path.read_text(encoding='utf-8')
                            dst_path.write_text(content + existing_content, encoding='utf-8')
                        else:
                            dst_path.write_text(content, encoding='utf-8')
                    elif node.config.output_mode == OutputMode.CONCATENATE:
                        separator = "\n" if not content.endswith("\n") else ""
                        if dst_path.exists():
                            existing_content = dst_path.read_text(encoding='utf-8')
                            dst_path.write_text(existing_content + separator + content, encoding='utf-8')
                        else:
                            dst_path.write_text(content, encoding='utf-8')
        
        # Store node values for outputs that were generated
        stored_values = 0
        for i, output_name in enumerate(node.config.outputs):
            if i < len(output_files):
                output_file = Path(output_files[i])
                if output_file.exists():
                    content = output_file.read_text(encoding='utf-8')
                    value = NodeValue(
                        node_id=node.id,
                        output_name=output_name,
                        value_hash=hashlib.md5(content.encode()).hexdigest(),
                        value_data=content
                    )
                    await self.db.store_node_value(value)
                    stored_values += 1
                    
                    # Clean up the persistent temporary file
                    try:
                        output_file.unlink()
                    except Exception as e:
                        await self._log(node.id, LogLevel.DEBUG, f"Failed to cleanup temp file {output_file}: {e}")
        
        await self._log(node.id, LogLevel.INFO, f"Program instance completed: {stored_values} outputs stored")
    
    async def _build_webhook_instance(self, node: TemplateNode, instance: NodeInstance) -> None:
        """Build a webhook instance - essentially sets it up to receive triggers."""
        # Webhook instances don't produce immediate output
        # They wait for webhook triggers to be processed
        await self._log(node.id, LogLevel.INFO, f"Webhook instance ready: {instance.id}")
    
    async def _build_tail_instance(self, node: TemplateNode, instance: NodeInstance) -> None:
        """Build a tail instance - sets it up to monitor files."""
        # Set up tail watching for this instance
        await self._setup_file_watching(node, instance)
        await self._log(node.id, LogLevel.INFO, f"Tail instance ready: {instance.id}")
    
    async def _resolve_input_values(
        self, 
        node: TemplateNode, 
        instance: NodeInstance
    ) -> Dict[str, Any]:
        """Resolve input values for an instance, including node references."""
        context = {}
        
        for input_name, input_spec in node.config.inputs.items():
            if input_name in instance.input_values:
                # Use provided value
                value = instance.input_values[input_name]
            elif input_spec.source:
                # Resolve node reference
                try:
                    node_ref = NodeReference.parse_reference(input_spec.source)
                    value = await self._resolve_node_reference(node_ref)
                except Exception as e:
                    await self._log(node.id, LogLevel.WARNING, f"Failed to resolve reference {input_spec.source}: {e}")
                    value = input_spec.default
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
    
    async def _resolve_node_reference(self, node_ref: NodeReference) -> Any:
        """Resolve a reference to another node's output."""
        node_value = await self.db.get_node_value(node_ref.node_id, node_ref.output_name)
        if node_value:
            return node_value.value_data
        else:
            raise ValueError(f"Node reference not found: {node_ref.to_string()}")
    
    async def _extract_node_dependencies(self, node: TemplateNode) -> List[any]:
        """Extract node dependencies from configuration."""
        dependencies = []
        
        # Check input sources for node references
        for input_spec in node.config.inputs.values():
            if input_spec.source and input_spec.source.startswith('@'):
                try:
                    node_ref = NodeReference.parse_reference(input_spec.source)
                    from .models import DependencyEdge
                    dependencies.append(DependencyEdge(
                        dependent_node_id=node.id,
                        dependency_node_id=node_ref.node_id,
                        dependency_output=node_ref.output_name
                    ))
                except ValueError:
                    pass
        
        # Check template content for node references
        if node.config.template_content:
            # Simple regex to find @node-id.output patterns
            pattern = r'@([a-zA-Z0-9_-]+)\.([a-zA-Z0-9_-]+)'
            matches = re.findall(pattern, node.config.template_content)
            for node_id, output_name in matches:
                from .models import DependencyEdge
                dependencies.append(DependencyEdge(
                    dependent_node_id=node.id,
                    dependency_node_id=node_id,
                    dependency_output=output_name
                ))
        
        return dependencies
    
    async def _remove_instance(self, instance: NodeInstance) -> None:
        """Remove an instance and clean up."""
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
                        if node.config.input_mode == InputMode.TAIL:
                            self.tail_watcher.remove_file_watch(instance.node_id, input_value)
                        else:
                            self.file_watcher.remove_file_watch(input_value, node.id)
    
    async def _process_webhooks(self) -> None:
        """Background task to process webhook triggers."""
        while self.running:
            try:
                # Get pending webhook triggers
                triggers = await self.db.get_pending_webhook_triggers()
                
                for trigger in triggers:
                    await self._process_webhook_trigger(trigger)
                
                # Sleep between checks
                await asyncio.sleep(1)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                await self._log("webhook_processor", LogLevel.ERROR, f"Webhook processing error: {e}")
                await asyncio.sleep(5)
    
    async def _process_webhook_trigger(self, trigger: WebhookTrigger) -> None:
        """Process a single webhook trigger."""
        node = await self.db.get_node(trigger.node_id)
        if not node or node.config.node_type != NodeType.WEBHOOK:
            return
        
        # Process webhook instances
        if trigger.node_id in self.node_instances:
            for instance in self.node_instances[trigger.node_id]:
                # Create context with webhook data
                context = {
                    "webhook_data": trigger.data,
                    "webhook_headers": trigger.headers,
                    "webhook_timestamp": trigger.timestamp.isoformat()
                }
                
                # If it's a template webhook, render it
                if node.config.template_content:
                    rendered_content = self.template_engine.render(
                        node.config.template_content,
                        context
                    )
                    
                    # Handle output
                    target_path = Path(instance.output_path)
                    if node.config.output_mode == OutputMode.APPEND:
                        self.symlink_manager.append_to_file(target_path, rendered_content)
                    elif node.config.output_mode == OutputMode.PREPEND:
                        self.symlink_manager.prepend_to_file(target_path, rendered_content)
                    else:
                        content_hash, content_path = self.content_store.store_content(rendered_content)
                        self.symlink_manager.create_symlink(target_path, content_path)
                        await self.db.store_symlink(str(target_path), content_hash, instance.id)
                
                await self._log(trigger.node_id, LogLevel.INFO, f"Webhook processed for instance: {instance.id}")
        
        # Mark trigger as processed
        trigger_id = trigger.node_id + "_" + str(int(trigger.timestamp.timestamp() * 1000))
        await self.db.mark_webhook_processed(trigger_id)
    
    async def _log(self, node_id: str, level: LogLevel, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        """Log a message for a node."""
        log = ExecutionLog(
            id=str(uuid.uuid4()),
            node_id=node_id,
            level=level,
            message=message,
            details=details
        )
        await self.db.store_execution_log(log)
    
    async def _start_api_server(self) -> None:
        """Start the HTTP API server."""
        self.app = web.Application()
        
        # Add routes
        self.app.router.add_get('/api/status', self._api_get_status)
        self.app.router.add_get('/api/nodes', self._api_list_nodes)
        self.app.router.add_post('/api/nodes', self._api_register_node)
        self.app.router.add_delete('/api/nodes/{node_id}', self._api_unregister_node)
        self.app.router.add_get('/api/nodes/{node_id}', self._api_get_node)
        self.app.router.add_get('/api/nodes/{node_id}/inputs', self._api_get_node_inputs)
        self.app.router.add_get('/api/nodes/{node_id}/file-inputs', self._api_get_node_file_inputs)
        self.app.router.add_get('/api/nodes/{node_id}/logs', self._api_get_node_logs)
        self.app.router.add_post('/api/nodes/{node_id}/instances', self._api_create_instance)
        self.app.router.add_post('/api/nodes/{node_id}/rebuild', self._api_rebuild_node)
        self.app.router.add_get('/api/instances', self._api_list_instances)
        self.app.router.add_get('/api/watched-files', self._api_get_watched_files)
        self.app.router.add_get('/api/watched-files/{node_id}', self._api_get_watched_files_for_node)
        self.app.router.add_get('/api/graph', self._api_get_dependency_graph)
        self.app.router.add_post('/api/webhooks/{node_id}', self._api_trigger_webhook)
        
        # Start server
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, 'localhost', self.api_port)
        await site.start()
        self.api_server = runner
    
    # API endpoint implementations
    async def _api_get_status(self, request: web.Request) -> web.Response:
        """API endpoint: Get daemon status."""
        status = await self.get_status()
        return web.json_response(status)
    
    async def _api_list_nodes(self, request: web.Request) -> web.Response:
        """API endpoint: List all nodes."""
        nodes = await self.list_nodes()
        nodes_data = []
        for node in nodes:
            nodes_data.append({
                "id": node.id,
                "config_path": str(node.config_path) if node.config_path else None,
                "node_type": node.config.node_type.value,
                "outputs": node.config.outputs,
                "created_at": node.created_at.isoformat() if node.created_at else None
            })
        return web.json_response({"nodes": nodes_data})
    
    async def _api_register_node(self, request: web.Request) -> web.Response:
        """API endpoint: Register a new node."""
        data = await request.json()
        config_path = Path(data["config_path"])
        try:
            node_id = await self.register_node(config_path)
            return web.json_response({"node_id": node_id})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)
    
    async def _api_unregister_node(self, request: web.Request) -> web.Response:
        """API endpoint: Unregister a node."""
        node_id = request.match_info['node_id']
        try:
            await self.unregister_node(node_id)
            return web.json_response({"status": "success"})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)
    
    async def _api_get_node(self, request: web.Request) -> web.Response:
        """API endpoint: Get node details."""
        node_id = request.match_info['node_id']
        node = await self.db.get_node(node_id)
        if not node:
            return web.json_response({"error": "Node not found"}, status=404)
        
        return web.json_response({
            "id": node.id,
            "config_path": str(node.config_path) if node.config_path else None,
            "node_type": node.config.node_type.value,
            "outputs": node.config.outputs,
            "created_at": node.created_at.isoformat() if node.created_at else None,
            "config": node.config.dict()
        })
    
    async def _api_get_node_inputs(self, request: web.Request) -> web.Response:
        """API endpoint: Get node inputs."""
        node_id = request.match_info['node_id']
        try:
            inputs_data = await self.get_node_inputs(node_id)
            return web.json_response(inputs_data)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=404)
    
    async def _api_get_node_file_inputs(self, request: web.Request) -> web.Response:
        """API endpoint: Get node file inputs."""
        node_id = request.match_info['node_id']
        try:
            file_inputs = await self.get_node_file_inputs(node_id)
            return web.json_response({"file_inputs": file_inputs})
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=404)
    
    async def _api_get_node_logs(self, request: web.Request) -> web.Response:
        """API endpoint: Get node logs."""
        node_id = request.match_info['node_id']
        limit = int(request.query.get('limit', 100))
        try:
            logs = await self.get_node_logs(node_id, limit)
            return web.json_response({"logs": logs})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    
    async def _api_create_instance(self, request: web.Request) -> web.Response:
        """API endpoint: Create node instance."""
        node_id = request.match_info['node_id']
        data = await request.json()
        try:
            instance_id = await self.create_instance(
                node_id,
                data["output_path"],
                data["input_values"]
            )
            return web.json_response({"instance_id": instance_id})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)
    
    async def _api_rebuild_node(self, request: web.Request) -> web.Response:
        """API endpoint: Rebuild node instances."""
        node_id = request.match_info['node_id']
        try:
            await self.rebuild_node_instances(node_id)
            return web.json_response({"status": "success"})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    
    async def _api_list_instances(self, request: web.Request) -> web.Response:
        """API endpoint: List instances."""
        node_id = request.query.get('node_id')
        instances = await self.db.get_node_instances(node_id)
        instances_data = []
        for instance in instances:
            instances_data.append({
                "id": instance.id,
                "node_id": instance.node_id,
                "output_path": instance.output_path,
                "input_values": instance.input_values,
                "created_at": instance.created_at.isoformat(),
                "last_built": instance.last_built.isoformat() if instance.last_built else None,
                "build_count": instance.build_count
            })
        return web.json_response({"instances": instances_data})
    
    async def _api_get_watched_files(self, request: web.Request) -> web.Response:
        """API endpoint: Get all watched files."""
        watched_data = await self.get_watched_files()
        return web.json_response(watched_data)
    
    async def _api_get_watched_files_for_node(self, request: web.Request) -> web.Response:
        """API endpoint: Get watched files for a specific node."""
        node_id = request.match_info['node_id']
        watched_data = await self.get_watched_files(node_id)
        return web.json_response(watched_data)
    
    async def _api_get_dependency_graph(self, request: web.Request) -> web.Response:
        """API endpoint: Get dependency graph."""
        node_id = request.query.get('node_id')
        try:
            graph = await self.get_dependency_graph(node_id)
            return web.json_response(graph)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    
    async def _api_trigger_webhook(self, request: web.Request) -> web.Response:
        """API endpoint: Trigger webhook."""
        node_id = request.match_info['node_id']
        webhook_data = await request.json()
        try:
            await self.trigger_webhook(node_id, webhook_data)
            return web.json_response({"status": "success"})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)


# Keep legacy DaemonClient for backward compatibility
from ..client import LivingTemplatesClient as DaemonClient 