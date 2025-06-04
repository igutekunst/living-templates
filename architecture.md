# Living Templates Architecture

## Overview

Living Templates is a reactive file system that maintains a dependency graph of nodes (templates, programs, data sources) and automatically updates outputs when inputs change. The system is built around a core daemon with a plugin architecture that supports various node types, including long-running processes like webhook listeners.

## Core Architecture

### System Components

```
┌─────────────────────────────────────────────────────────────┐
│                    Living Templates System                   │
├─────────────────────────────────────────────────────────────┤
│  CLI Tools          │  Web API          │  Plugin Manager   │
│  - lt               │  - REST API       │  - Node Plugins   │
│  - living-templates │  - Webhooks       │  - Extensions     │
├─────────────────────────────────────────────────────────────┤
│                      Core Daemon                            │
│  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐│
│  │ Dependency      │ │ Value Store     │ │ Event System    ││
│  │ Graph Manager   │ │ Manager         │ │ & Scheduler     ││
│  └─────────────────┘ └─────────────────┘ └─────────────────┘│
├─────────────────────────────────────────────────────────────┤
│                    Storage Layer                            │
│  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐│
│  │ SQLite Database │ │ Content Store   │ │ Symlink Manager ││
│  │ - Metadata      │ │ - Hash-based    │ │ - Target Links  ││
│  │ - Dependencies  │ │ - Generated     │ │ - Cleanup       ││
│  │ - Values        │ │   Content       │ │                 ││
│  └─────────────────┘ └─────────────────┘ └─────────────────┘│
└─────────────────────────────────────────────────────────────┘
```

### Core Daemon Responsibilities

1. **Dependency Graph Management**: Track nodes and their relationships
2. **Value Storage**: Store and retrieve node output values
3. **Event Processing**: Handle change notifications and trigger rebuilds
4. **Plugin Lifecycle**: Manage long-running node processes
5. **API Server**: Provide REST API for external integrations
6. **File System Operations**: Manage content store and symlinks

## Node Architecture

### Node Types and Lifecycle

```python
# Base node interface
class NodePlugin:
    def __init__(self, node_config: dict, daemon_api: DaemonAPI):
        self.config = node_config
        self.daemon = daemon_api
    
    async def initialize(self) -> None:
        """Called when node is registered"""
        pass
    
    async def start(self) -> None:
        """Called when daemon starts (for long-running nodes)"""
        pass
    
    async def process(self, inputs: dict) -> dict:
        """Process inputs and return outputs"""
        raise NotImplementedError
    
    async def stop(self) -> None:
        """Called when daemon stops"""
        pass
    
    async def cleanup(self) -> None:
        """Called when node is unregistered"""
        pass
```

### Node Type Categories

#### 1. Reactive Nodes (Event-Driven)
- **Template Nodes**: Render when inputs change
- **Program Nodes**: Execute scripts when triggered
- **File Nodes**: Watch filesystem changes

#### 2. Active Nodes (Long-Running)
- **Webhook Nodes**: HTTP servers listening for external events
- **API Polling Nodes**: Periodically fetch external data
- **Database Watch Nodes**: Monitor database changes
- **Message Queue Nodes**: Listen to message brokers

#### 3. Manual Nodes (On-Demand)
- **Manual Input Nodes**: Updated via CLI/API calls
- **Scheduled Nodes**: Triggered by cron-like schedules

## Plugin System Design

### Plugin Registration

```python
# Plugin registry
class PluginManager:
    def __init__(self):
        self.plugins = {}
        self.active_nodes = {}  # node_id -> plugin_instance
    
    def register_plugin(self, node_type: str, plugin_class: Type[NodePlugin]):
        self.plugins[node_type] = plugin_class
    
    async def create_node(self, node_id: str, node_config: dict) -> NodePlugin:
        node_type = node_config['node_type']
        plugin_class = self.plugins[node_type]
        instance = plugin_class(node_config, self.daemon_api)
        await instance.initialize()
        self.active_nodes[node_id] = instance
        return instance
```

### Built-in Node Plugins

#### Template Node Plugin
```python
class TemplateNodePlugin(NodePlugin):
    async def process(self, inputs: dict) -> dict:
        template_engine = self.config.get('template_engine', 'jinja2')
        template_content = self.config['template_content']
        
        if template_engine == 'jinja2':
            template = jinja2.Template(template_content)
            rendered = template.render(**inputs)
        
        outputs = {}
        for output_file in self.config['outputs']:
            outputs[output_file] = rendered
        
        return outputs
```

#### Webhook Node Plugin
```python
class WebhookNodePlugin(NodePlugin):
    def __init__(self, node_config: dict, daemon_api: DaemonAPI):
        super().__init__(node_config, daemon_api)
        self.app = None
        self.server = None
    
    async def start(self):
        """Start HTTP server for webhook"""
        from fastapi import FastAPI
        import uvicorn
        
        self.app = FastAPI()
        webhook_path = self.config.get('webhook_path', '/webhook')
        port = self.config.get('port', 8080)
        
        @self.app.post(webhook_path)
        async def handle_webhook(payload: dict):
            # Process webhook payload
            processed_data = await self.process_webhook_data(payload)
            
            # Update node value in daemon
            await self.daemon.update_node_value(
                self.config['node_id'], 
                processed_data
            )
            
            return {"status": "received"}
        
        # Start server in background
        config = uvicorn.Config(self.app, host="0.0.0.0", port=port)
        self.server = uvicorn.Server(config)
        await self.server.serve()
    
    async def process_webhook_data(self, payload: dict) -> dict:
        # Apply transform if specified
        if 'transform' in self.config:
            # Execute transform code
            transform_code = self.config['transform']
            # ... execute transform
        return payload
    
    async def stop(self):
        if self.server:
            self.server.should_exit = True
```

#### Program Node Plugin
```python
class ProgramNodePlugin(NodePlugin):
    async def process(self, inputs: dict) -> dict:
        import subprocess
        import tempfile
        import json
        
        # Prepare input files/environment
        env = os.environ.copy()
        args = []
        
        for input_name, input_value in inputs.items():
            if isinstance(input_value, str) and os.path.isfile(input_value):
                args.append(input_value)  # File path
            else:
                env[f"LT_INPUT_{input_name.upper()}"] = str(input_value)
        
        # Execute program
        script_path = self.config.get('script_path')
        result = subprocess.run(
            [script_path] + args,
            env=env,
            capture_output=True,
            text=True,
            cwd=tempfile.mkdtemp()
        )
        
        if result.returncode != 0:
            raise Exception(f"Program failed: {result.stderr}")
        
        # Collect outputs
        outputs = {}
        for output_file in self.config['outputs']:
            if os.path.exists(output_file):
                with open(output_file, 'r') as f:
                    outputs[output_file] = f.read()
        
        return outputs
```

## Database Schema

```sql
-- Core tables
CREATE TABLE nodes (
    id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL,
    config_path TEXT,
    config_data JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE node_instances (
    id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL,
    input_config JSON,
    output_path TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (node_id) REFERENCES nodes(id)
);

CREATE TABLE node_values (
    node_id TEXT,
    output_name TEXT,
    value_hash TEXT,
    value_data JSON,
    content_path TEXT,  -- Path in content store
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (node_id, output_name),
    FOREIGN KEY (node_id) REFERENCES nodes(id)
);

CREATE TABLE dependencies (
    dependent_node_id TEXT,
    dependency_node_id TEXT,
    dependency_output TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (dependent_node_id, dependency_node_id, dependency_output),
    FOREIGN KEY (dependent_node_id) REFERENCES nodes(id),
    FOREIGN KEY (dependency_node_id) REFERENCES nodes(id)
);

CREATE TABLE symlinks (
    target_path TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    node_instance_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (node_instance_id) REFERENCES node_instances(id)
);

-- Plugin-specific tables
CREATE TABLE webhook_endpoints (
    node_id TEXT PRIMARY KEY,
    port INTEGER,
    path TEXT,
    auth_token TEXT,
    FOREIGN KEY (node_id) REFERENCES nodes(id)
);

CREATE TABLE scheduled_nodes (
    node_id TEXT PRIMARY KEY,
    cron_expression TEXT,
    next_run TIMESTAMP,
    FOREIGN KEY (node_id) REFERENCES nodes(id)
);
```

## API Design

### Core Daemon API

```python
class DaemonAPI:
    """API interface for plugins to interact with core daemon"""
    
    async def update_node_value(self, node_id: str, output_name: str, value: Any):
        """Update a node's output value and trigger dependents"""
        
    async def get_node_value(self, node_id: str, output_name: str) -> Any:
        """Retrieve a node's current output value"""
        
    async def get_node_inputs(self, node_id: str) -> dict:
        """Get resolved input values for a node"""
        
    async def trigger_rebuild(self, node_id: str):
        """Force rebuild of a specific node"""
        
    async def get_dependents(self, node_id: str) -> List[str]:
        """Get list of nodes that depend on this node"""
        
    async def log_event(self, node_id: str, event_type: str, data: dict):
        """Log events for debugging/monitoring"""
```

### REST API Endpoints

```python
# External API for integrations
@app.post("/api/v1/nodes/{node_id}/notify")
async def notify_node_change(node_id: str, payload: dict):
    """External systems can notify of changes"""
    
@app.put("/api/v1/nodes/{node_id}/values/{output_name}")
async def set_node_value(node_id: str, output_name: str, value: Any):
    """Manually set a node's output value"""
    
@app.get("/api/v1/nodes/{node_id}/values/{output_name}")
async def get_node_value(node_id: str, output_name: str):
    """Get a node's current output value"""
    
@app.post("/api/v1/nodes/{node_id}/rebuild")
async def rebuild_node(node_id: str):
    """Force rebuild of a node"""
    
@app.get("/api/v1/graph")
async def get_dependency_graph():
    """Get the current dependency graph"""
    
@app.get("/api/v1/status")
async def get_system_status():
    """Get system status and active nodes"""
```

## Event System

### Event Flow

```python
class EventSystem:
    async def handle_change_event(self, source_node_id: str, output_name: str, new_value: Any):
        # 1. Update value in database
        await self.db.update_node_value(source_node_id, output_name, new_value)
        
        # 2. Find dependent nodes
        dependents = await self.db.get_dependents(source_node_id, output_name)
        
        # 3. Schedule rebuilds (with topological ordering)
        rebuild_order = self.dependency_graph.topological_sort(dependents)
        
        # 4. Execute rebuilds
        for node_id in rebuild_order:
            await self.rebuild_node(node_id)
    
    async def rebuild_node(self, node_id: str):
        # 1. Get node plugin instance
        plugin = self.plugin_manager.get_node(node_id)
        
        # 2. Resolve input values
        inputs = await self.resolve_node_inputs(node_id)
        
        # 3. Process node
        outputs = await plugin.process(inputs)
        
        # 4. Store outputs and update symlinks
        for output_name, output_value in outputs.items():
            await self.store_output(node_id, output_name, output_value)
```

## Configuration Examples

### Webhook Node Configuration
```yaml
---
schema_version: "1.0"
node_type: webhook
webhook_config:
  port: 8080
  path: "/github-webhook"
  auth_token: "secret-token"
inputs:
  repository:
    type: string
    default: "unknown"
outputs:
  - commit_info
transform: |
  # Python code to process webhook payload
  def process_payload(payload):
      return {
          "sha": payload["head_commit"]["id"],
          "message": payload["head_commit"]["message"],
          "author": payload["head_commit"]["author"]["name"],
          "timestamp": payload["head_commit"]["timestamp"]
      }
---
```

### API Polling Node Configuration
```yaml
---
schema_version: "1.0"
node_type: api_poll
poll_config:
  interval: 300  # 5 minutes
  url: "https://api.github.com/repos/user/repo/commits"
  headers:
    Authorization: "token ${GITHUB_TOKEN}"
inputs:
  repository:
    type: string
outputs:
  - latest_commits
transform: |
  # Process API response
  def process_response(response_data):
      return {
          "latest_commit": response_data[0]["sha"],
          "commit_count": len(response_data),
          "last_updated": datetime.now().isoformat()
      }
---
```

## Deployment Architecture

### Single Machine Deployment
```
┌─────────────────────────────────────────┐
│              Host Machine                │
│  ┌─────────────────────────────────────┐ │
│  │        Living Templates Daemon      │ │
│  │  ┌─────────────┐ ┌─────────────────┐│ │
│  │  │ Core Engine │ │ Plugin Manager  ││ │
│  │  └─────────────┘ └─────────────────┘│ │
│  │  ┌─────────────┐ ┌─────────────────┐│ │
│  │  │ Webhook     │ │ File Watcher    ││ │
│  │  │ Listeners   │ │ Processes       ││ │
│  │  └─────────────┘ └─────────────────┘│ │
│  └─────────────────────────────────────┘ │
│  ┌─────────────────────────────────────┐ │
│  │         Storage                     │ │
│  │  ~/.living-templates/               │ │
│  │  ├── db.sqlite                     │ │
│  │  ├── store/                        │ │
│  │  └── plugins/                      │ │
│  └─────────────────────────────────────┘ │
└─────────────────────────────────────────┘
```

### Distributed Deployment (Future)
```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Core Daemon   │    │  Plugin Nodes   │    │  Storage Layer  │
│                 │    │                 │    │                 │
│ ┌─────────────┐ │    │ ┌─────────────┐ │    │ ┌─────────────┐ │
│ │Dependency   │ │    │ │ Webhook     │ │    │ │ Distributed │ │
│ │Graph        │◄────►│ │ Listeners   │ │    │ │ Database    │ │
│ └─────────────┘ │    │ └─────────────┘ │    │ └─────────────┘ │
│ ┌─────────────┐ │    │ ┌─────────────┐ │    │ ┌─────────────┐ │
│ │Event        │ │    │ │ File        │ │    │ │ Content     │ │
│ │Coordinator  │◄────►│ │ Watchers    │ │    │ │ Store       │ │
│ └─────────────┘ │    │ └─────────────┘ │    │ └─────────────┘ │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

## Security Considerations

### Plugin Sandboxing
- **Process Isolation**: Each plugin runs in its own process/container
- **Resource Limits**: CPU, memory, and network restrictions
- **File System Access**: Restricted to specific directories
- **Network Access**: Configurable allow/deny lists

### Authentication & Authorization
- **API Authentication**: Token-based auth for REST API
- **Webhook Security**: HMAC signature verification
- **Plugin Permissions**: Role-based access to daemon APIs
- **Audit Logging**: All operations logged for security review

## Performance Considerations

### Scalability
- **Async Processing**: All I/O operations are asynchronous
- **Parallel Rebuilds**: Independent nodes rebuild in parallel
- **Content Deduplication**: Hash-based storage prevents duplicate work
- **Incremental Updates**: Only rebuild what actually changed

### Resource Management
- **Connection Pooling**: Database and HTTP connections pooled
- **Memory Management**: Large values stored on disk, not in memory
- **Process Limits**: Configurable limits on concurrent operations
- **Cleanup Processes**: Automatic cleanup of unused content

## Extension Points

### Custom Node Types
Developers can create custom node types by implementing the `NodePlugin` interface and registering them with the plugin manager.

### Custom Template Engines
Support for additional template engines beyond Jinja2 through plugin system.

### Custom Storage Backends
Alternative storage backends (Redis, S3, etc.) can be implemented through storage plugins.

### Monitoring & Observability
Integration points for metrics collection, logging, and distributed tracing.

This architecture provides a solid foundation for the Living Templates system while maintaining flexibility for future enhancements and custom extensions. 