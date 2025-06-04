"""Template engine for Living Templates."""

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import jinja2


class TemplateEngine:
    """Template engine with custom filters and functions."""
    
    def __init__(self):
        """Initialize template engine."""
        self.env = jinja2.Environment(
            loader=jinja2.BaseLoader(),
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True
        )
        
        # Add custom filters and functions
        self.env.filters['read_file'] = self._read_file_filter
        self.env.globals['now'] = self._now_function
        self.env.globals['env'] = self._env_function
    
    def render(self, template_content: str, context: Dict[str, Any]) -> str:
        """Render template with given context.
        
        Args:
            template_content: The template content to render
            context: Variables to pass to the template
            
        Returns:
            Rendered template content
        """
        template = self.env.from_string(template_content)
        return template.render(**context)
    
    def _read_file_filter(self, file_path: str) -> str:
        """Jinja2 filter to read file contents.
        
        Usage: {{ "path/to/file.txt" | read_file }}
        """
        try:
            path = Path(file_path)
            if path.exists():
                return path.read_text(encoding='utf-8')
            else:
                return f"<!-- File not found: {file_path} -->"
        except Exception as e:
            return f"<!-- Error reading file {file_path}: {e} -->"
    
    def _now_function(self, format_str: str = "%Y-%m-%d %H:%M:%S") -> str:
        """Jinja2 function to get current timestamp.
        
        Usage: {{ now() }} or {{ now("%Y-%m-%d") }}
        """
        return datetime.now().strftime(format_str)
    
    def _env_function(self, var_name: str, default: str = "") -> str:
        """Jinja2 function to get environment variable.
        
        Usage: {{ env("HOME") }} or {{ env("MISSING_VAR", "default") }}
        """
        return os.environ.get(var_name, default) 