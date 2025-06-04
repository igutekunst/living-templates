"""Basic tests for Living Templates."""

import asyncio
import tempfile
from pathlib import Path

import pytest

from living_templates.core.config import FrontmatterParser
from living_templates.core.daemon import LivingTemplatesDaemon
from living_templates.core.models import NodeType
from living_templates.core.template_engine import TemplateEngine


def test_frontmatter_parser():
    """Test YAML frontmatter parsing."""
    content = """---
schema_version: "1.0"
node_type: template
template_engine: jinja2
inputs:
  name:
    type: string
    default: "World"
outputs:
  - greeting.txt
---
Hello, {{ name }}!
"""
    
    config, template_content = FrontmatterParser.parse_content(content)
    
    assert config.schema_version == "1.0"
    assert config.node_type == NodeType.TEMPLATE
    assert config.template_engine == "jinja2"
    assert "name" in config.inputs
    assert config.inputs["name"].default == "World"
    assert config.outputs == ["greeting.txt"]
    assert "Hello, {{ name }}!" in template_content


def test_template_engine():
    """Test template rendering."""
    engine = TemplateEngine()
    
    template = "Hello, {{ name }}! Today is {{ now('%Y-%m-%d') }}."
    context = {"name": "Isaac"}
    
    result = engine.render(template, context)
    
    assert "Hello, Isaac!" in result
    assert "Today is" in result


def test_template_engine_read_file_filter():
    """Test the read_file filter."""
    engine = TemplateEngine()
    
    # Create a temporary file
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write("Test content")
        temp_path = f.name
    
    try:
        template = f"Content: {{{{ '{temp_path}' | read_file }}}}"
        result = engine.render(template, {})
        
        assert "Content: Test content" in result
    finally:
        Path(temp_path).unlink()


@pytest.mark.asyncio
async def test_daemon_initialization():
    """Test daemon initialization."""
    with tempfile.TemporaryDirectory() as temp_dir:
        config_dir = Path(temp_dir)
        daemon = LivingTemplatesDaemon(config_dir)
        
        await daemon.initialize()
        
        # Check that directories were created
        assert (config_dir / "store").exists()
        assert daemon.config_manager.db_path.exists()


@pytest.mark.asyncio
async def test_node_registration():
    """Test node registration and template creation."""
    with tempfile.TemporaryDirectory() as temp_dir:
        config_dir = Path(temp_dir)
        daemon = LivingTemplatesDaemon(config_dir)
        await daemon.initialize()
        
        # Create a test template file
        template_content = """---
schema_version: "1.0"
node_type: template
template_engine: jinja2
inputs:
  name:
    type: string
    default: "Test"
outputs:
  - output.txt
---
Hello, {{ name }}!
"""
        
        template_file = Path(temp_dir) / "test-template.yaml"
        template_file.write_text(template_content)
        
        # Register the node
        node_id = await daemon.register_node(template_file)
        assert node_id is not None
        
        # Create an instance
        output_path = Path(temp_dir) / "output.txt"
        instance_id = await daemon.create_instance(
            node_id,
            str(output_path),
            {"name": "Isaac"}
        )
        
        assert instance_id is not None
        assert output_path.exists()
        
        # Check the content
        content = output_path.read_text()
        assert "Hello, Isaac!" in content


if __name__ == "__main__":
    pytest.main([__file__]) 