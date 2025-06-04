"""Core data models for Living Templates."""

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field, validator


class NodeType(str, Enum):
    """Supported node types."""
    TEMPLATE = "template"
    PROGRAM = "program"
    FILE = "file"
    WEBHOOK = "webhook"
    MANUAL = "manual"
    TAIL = "tail"


class InputType(str, Enum):
    """Supported input types."""
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ARRAY = "array"
    OBJECT = "object"
    FILE = "file"


class OutputMode(str, Enum):
    """Output modes for nodes."""
    REPLACE = "replace"
    APPEND = "append"
    PREPEND = "prepend"
    CONCATENATE = "concatenate"


class InputMode(str, Enum):
    """Input modes for nodes."""
    NORMAL = "normal"
    TAIL = "tail"


class LogLevel(str, Enum):
    """Log levels."""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class InputSpec(BaseModel):
    """Specification for a node input."""
    type: InputType
    description: Optional[str] = None
    default: Optional[Any] = None
    required: bool = True
    source: Optional[str] = None  # Reference to another node: "@node-id.output"

    @validator('required', pre=True, always=True)
    def set_required_if_no_default(cls, v: bool, values: Dict[str, Any]) -> bool:
        """Set required=False if default is provided."""
        if 'default' in values and values['default'] is not None:
            return False
        return v


class NodeConfig(BaseModel):
    """Configuration for a node."""
    schema_version: str = "1.0"
    node_type: NodeType
    inputs: Dict[str, InputSpec] = Field(default_factory=dict)
    outputs: List[str]
    dependencies: List[str] = Field(default_factory=list)
    template_engine: Optional[str] = "jinja2"
    output_mode: OutputMode = OutputMode.REPLACE
    input_mode: InputMode = InputMode.NORMAL
    transform: Optional[str] = None
    
    # Template-specific
    template_content: Optional[str] = None
    
    # Program-specific
    script_path: Optional[str] = None
    command: Optional[str] = None
    working_directory: Optional[str] = None
    environment: Dict[str, str] = Field(default_factory=dict)
    timeout: Optional[int] = 300  # 5 minutes default
    
    # Webhook-specific
    webhook_config: Optional[Dict[str, Any]] = None
    
    # Tail-specific
    tail_lines: int = 10  # Number of lines to keep in memory for tail
    tail_separator: str = "\n"


class NodeInstance(BaseModel):
    """An instance of a node with specific inputs."""
    id: str
    node_id: str
    input_values: Dict[str, Any]
    output_path: str
    created_at: datetime = Field(default_factory=datetime.now)
    last_built: Optional[datetime] = None
    build_count: int = 0


class NodeValue(BaseModel):
    """A stored value for a node output."""
    node_id: str
    output_name: str
    value_hash: str
    value_data: Any
    content_path: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.now)


class NodeReference(BaseModel):
    """A reference to another node's output."""
    node_id: str
    output_name: str
    
    @classmethod
    def parse_reference(cls, ref_string: str) -> 'NodeReference':
        """Parse a reference string like '@node-id.output'."""
        if not ref_string.startswith('@'):
            raise ValueError(f"Node reference must start with '@': {ref_string}")
        
        ref_part = ref_string[1:]  # Remove @
        if '.' not in ref_part:
            raise ValueError(f"Node reference must contain '.': {ref_string}")
        
        node_id, output_name = ref_part.split('.', 1)
        return cls(node_id=node_id, output_name=output_name)
    
    def to_string(self) -> str:
        """Convert to string representation."""
        return f"@{self.node_id}.{self.output_name}"


class TemplateNode(BaseModel):
    """A template node with its configuration and content."""
    id: str
    config: NodeConfig
    config_path: Optional[Path] = None
    created_at: datetime = Field(default_factory=datetime.now)
    
    @validator('config')
    def validate_node_config(cls, v: NodeConfig) -> NodeConfig:
        """Validate node-specific configuration."""
        if v.node_type == NodeType.TEMPLATE and not v.template_content:
            raise ValueError("Template nodes must have template_content")
        elif v.node_type == NodeType.PROGRAM and not (v.script_path or v.command):
            raise ValueError("Program nodes must have script_path or command")
        elif v.node_type == NodeType.WEBHOOK and not v.webhook_config:
            raise ValueError("Webhook nodes must have webhook_config")
        return v


class DependencyEdge(BaseModel):
    """An edge in the dependency graph."""
    dependent_node_id: str
    dependency_node_id: str
    dependency_output: str


class ExecutionLog(BaseModel):
    """Log entry for node execution."""
    id: str
    node_id: str
    instance_id: Optional[str] = None
    level: LogLevel
    message: str
    details: Optional[Dict[str, Any]] = None
    timestamp: datetime = Field(default_factory=datetime.now)


class SystemStatus(BaseModel):
    """System status information."""
    daemon_running: bool
    active_nodes: int
    total_instances: int
    last_update: datetime
    version: str


class TailState(BaseModel):
    """State for tail nodes."""
    node_id: str
    file_path: str
    last_position: int = 0
    last_inode: Optional[int] = None
    buffer: List[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=datetime.now)


class WebhookTrigger(BaseModel):
    """Webhook trigger data."""
    node_id: str
    data: Dict[str, Any]
    headers: Dict[str, str] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.now) 