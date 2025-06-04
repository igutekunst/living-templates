"""Configuration parsing and frontmatter handling."""

import re
from pathlib import Path
from typing import Dict, Optional, Tuple

import yaml
from pydantic import ValidationError

from .models import NodeConfig


class FrontmatterParser:
    """Parser for YAML frontmatter in files."""
    
    FRONTMATTER_PATTERN = re.compile(
        r'^---\s*\n(.*?)\n---\s*\n(.*)',
        re.DOTALL | re.MULTILINE
    )
    
    @classmethod
    def parse_file(cls, file_path: Path) -> Tuple[NodeConfig, str]:
        """Parse a file with YAML frontmatter.
        
        Returns:
            Tuple of (NodeConfig, content)
        """
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        content = file_path.read_text(encoding='utf-8')
        return cls.parse_content(content)
    
    @classmethod
    def parse_content(cls, content: str) -> Tuple[NodeConfig, str]:
        """Parse content with YAML frontmatter.
        
        Returns:
            Tuple of (NodeConfig, content)
        """
        match = cls.FRONTMATTER_PATTERN.match(content)
        if not match:
            raise ValueError("No valid YAML frontmatter found")
        
        frontmatter_yaml = match.group(1)
        template_content = match.group(2)
        
        try:
            frontmatter_data = yaml.safe_load(frontmatter_yaml)
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in frontmatter: {e}")
        
        # Add template content to the config
        if frontmatter_data.get('node_type') == 'template':
            frontmatter_data['template_content'] = template_content
        
        try:
            config = NodeConfig(**frontmatter_data)
        except ValidationError as e:
            raise ValueError(f"Invalid node configuration: {e}")
        
        return config, template_content


class ConfigManager:
    """Manages configuration loading and validation."""
    
    def __init__(self, config_dir: Optional[Path] = None):
        """Initialize config manager.
        
        Args:
            config_dir: Directory for configuration files. Defaults to ~/.living-templates
        """
        if config_dir is None:
            config_dir = Path.home() / ".living-templates"
        
        self.config_dir = config_dir
        self.config_dir.mkdir(parents=True, exist_ok=True)
        
        # Create subdirectories
        (self.config_dir / "store").mkdir(exist_ok=True)
        (self.config_dir / "plugins").mkdir(exist_ok=True)
    
    @property
    def db_path(self) -> Path:
        """Path to the SQLite database."""
        return self.config_dir / "db.sqlite"
    
    @property
    def store_path(self) -> Path:
        """Path to the content store."""
        return self.config_dir / "store"
    
    @property
    def daemon_pid_path(self) -> Path:
        """Path to the daemon PID file."""
        return self.config_dir / "daemon.pid"
    
    def load_node_config(self, config_path: Path) -> Tuple[NodeConfig, str]:
        """Load node configuration from file."""
        return FrontmatterParser.parse_file(config_path)
    
    def validate_config(self, config_path: Path) -> bool:
        """Validate a configuration file."""
        try:
            self.load_node_config(config_path)
            return True
        except (FileNotFoundError, ValueError, ValidationError):
            return False 