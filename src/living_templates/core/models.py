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
    transform: Optional[str] = None
    
    # Template-specific
    template_content: Optional[str] = None
    
    # Program-specific
    script_path: Optional[str] = None
    
    # Webhook-specific
    webhook_config: Optional[Dict[str, Any]] = None


class NodeInstance(BaseModel):
    """An instance of a node with specific inputs."""
    id: str
    node_id: str
    input_values: Dict[str, Any]
    output_path: str
    created_at: datetime = Field(default_factory=datetime.now)


class NodeValue(BaseModel):
    """A stored value for a node output."""
    node_id: str
    output_name: str
    value_hash: str
    value_data: Any
    content_path: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.now)


class TemplateNode(BaseModel):
    """A template node with its configuration and content."""
    id: str
    config: NodeConfig
    config_path: Optional[Path] = None
    created_at: datetime = Field(default_factory=datetime.now)
    
    @validator('config')
    def validate_template_config(cls, v: NodeConfig) -> NodeConfig:
        """Validate template-specific configuration."""
        if v.node_type == NodeType.TEMPLATE and not v.template_content:
            raise ValueError("Template nodes must have template_content")
        return v


class DependencyEdge(BaseModel):
    """An edge in the dependency graph."""
    dependent_node_id: str
    dependency_node_id: str
    dependency_output: str


class SystemStatus(BaseModel):
    """System status information."""
    daemon_running: bool
    active_nodes: int
    total_instances: int
    last_update: datetime
    version: str 