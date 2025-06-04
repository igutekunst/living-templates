"""Command-line interface for Living Templates."""

import asyncio
import json
import os
import signal
import sys
from functools import wraps
from pathlib import Path
from typing import Any, Dict, Optional

import click
import yaml
from rich.console import Console
from rich.table import Table
from rich.text import Text

from .core.daemon import LivingTemplatesDaemon
from .core.config import ConfigManager
from .core.models import NodeType
from .core.storage import ContentStore
from .core.template_engine import TemplateEngine


console = Console()


def handle_async(func):
    """Decorator to handle async functions in Click commands."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))
    return wrapper


@click.group()
@click.version_option()
@click.option(
    '--config-dir',
    type=click.Path(exists=False, file_okay=False, dir_okay=True, path_type=Path),
    help='Configuration directory (default: ~/.living-templates)'
)
@click.pass_context
def main(ctx: click.Context, config_dir: Optional[Path]) -> None:
    """Living Templates - Reactive file system for automatic template updates."""
    ctx.ensure_object(dict)
    ctx.obj['config_dir'] = config_dir


@main.group()
@click.pass_context
def daemon(ctx: click.Context) -> None:
    """Daemon management commands."""
    pass


@daemon.command()
@click.option('--port', default=8080, help='API server port')
@click.option('--host', default='127.0.0.1', help='API server host')
@click.pass_context
@handle_async
async def start(ctx: click.Context, port: int, host: str) -> None:
    """Start the Living Templates daemon."""
    config_dir = ctx.obj.get('config_dir')
    daemon_instance = LivingTemplatesDaemon(config_dir)
    
    # Check if already running
    pid_file = daemon_instance.config_manager.daemon_pid_path
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text())
            os.kill(pid, 0)  # Check if process exists
            console.print("[red]Daemon is already running[/red]")
            return
        except (OSError, ValueError):
            # PID file exists but process doesn't, remove stale file
            pid_file.unlink()
    
    console.print("[green]Starting Living Templates daemon...[/green]")
    
    try:
        await daemon_instance.start()
        console.print(f"[green]Daemon started successfully[/green]")
        console.print(f"PID: {os.getpid()}")
        console.print(f"Config directory: {daemon_instance.config_manager.config_dir}")
        
        # Set up signal handlers
        def signal_handler(signum, frame):
            console.print("\n[yellow]Shutting down daemon...[/yellow]")
            asyncio.create_task(daemon_instance.stop())
            sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # Keep daemon running
        while daemon_instance.running:
            await asyncio.sleep(1)
            
    except Exception as e:
        console.print(f"[red]Failed to start daemon: {e}[/red]")
        sys.exit(1)


@daemon.command()
@click.pass_context
def stop(ctx: click.Context) -> None:
    """Stop the Living Templates daemon."""
    config_dir = ctx.obj.get('config_dir')
    config_manager = ConfigManager(config_dir)
    pid_file = config_manager.daemon_pid_path
    
    if not pid_file.exists():
        console.print("[yellow]Daemon is not running[/yellow]")
        return
    
    try:
        pid = int(pid_file.read_text())
        os.kill(pid, signal.SIGTERM)
        console.print("[green]Daemon stopped successfully[/green]")
    except (OSError, ValueError) as e:
        console.print(f"[red]Failed to stop daemon: {e}[/red]")
        # Remove stale PID file
        if pid_file.exists():
            pid_file.unlink()


@daemon.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show daemon status."""
    config_dir = ctx.obj.get('config_dir')
    config_manager = ConfigManager(config_dir)
    pid_file = config_manager.daemon_pid_path
    
    if not pid_file.exists():
        console.print("[red]Daemon is not running[/red]")
        return
    
    try:
        pid = int(pid_file.read_text())
        os.kill(pid, 0)  # Check if process exists
        console.print(f"[green]Daemon is running[/green] (PID: {pid})")
        console.print(f"Config directory: {config_manager.config_dir}")
        console.print(f"Database: {config_manager.db_path}")
        console.print(f"Content store: {config_manager.store_path}")
    except (OSError, ValueError):
        console.print("[red]Daemon is not running[/red] (stale PID file)")
        pid_file.unlink()


@main.command()
@click.argument('config_file', type=click.Path(exists=True, path_type=Path))
@click.pass_context
@handle_async
async def register(ctx: click.Context, config_file: Path) -> None:
    """Register a new template or node."""
    config_dir = ctx.obj.get('config_dir')
    daemon_instance = LivingTemplatesDaemon(config_dir)
    
    try:
        await daemon_instance.initialize()
        node_id = await daemon_instance.register_node(config_file)
        console.print(f"[green]Registered node:[/green] {node_id}")
        console.print(f"[blue]Config file:[/blue] {config_file}")
    except Exception as e:
        console.print(f"[red]Failed to register node: {e}[/red]")
        sys.exit(1)


@main.command()
@click.argument('node_id')
@click.pass_context
@handle_async
async def unregister(ctx: click.Context, node_id: str) -> None:
    """Unregister a node."""
    config_dir = ctx.obj.get('config_dir')
    daemon_instance = LivingTemplatesDaemon(config_dir)
    
    try:
        await daemon_instance.initialize()
        await daemon_instance.unregister_node(node_id)
        console.print(f"[green]Unregistered node:[/green] {node_id}")
    except Exception as e:
        console.print(f"[red]Failed to unregister node: {e}[/red]")
        sys.exit(1)


@main.command('list-nodes')
@click.pass_context
@handle_async
async def list_nodes(ctx: click.Context) -> None:
    """List all registered nodes."""
    config_dir = ctx.obj.get('config_dir')
    daemon_instance = LivingTemplatesDaemon(config_dir)
    
    try:
        await daemon_instance.initialize()
        nodes = await daemon_instance.list_nodes()
        
        if not nodes:
            console.print("[yellow]No nodes registered[/yellow]")
            return
        
        table = Table(title="Registered Nodes")
        table.add_column("Node ID", style="cyan")
        table.add_column("Type", style="magenta")
        table.add_column("Config File", style="blue")
        table.add_column("Outputs", style="green")
        table.add_column("Created", style="dim")
        
        for node in nodes:
            outputs = ", ".join(node.config.outputs)
            config_path = str(node.config_path) if node.config_path else "N/A"
            table.add_row(
                node.id,
                node.config.node_type.value,
                config_path,
                outputs,
                node.created_at.strftime("%Y-%m-%d %H:%M")
            )
        
        console.print(table)
        
    except Exception as e:
        console.print(f"[red]Failed to list nodes: {e}[/red]")
        sys.exit(1)


@main.command('show-inputs')
@click.argument('node_id')
@click.pass_context
@handle_async
async def show_inputs(ctx: click.Context, node_id: str) -> None:
    """Show input specifications and current values for a node."""
    config_dir = ctx.obj.get('config_dir')
    daemon_instance = LivingTemplatesDaemon(config_dir)
    
    try:
        await daemon_instance.initialize()
        input_info = await daemon_instance.get_node_inputs(node_id)
        
        console.print(f"[bold blue]Node:[/bold blue] {node_id}")
        if input_info['config_path']:
            console.print(f"[blue]Config:[/blue] {input_info['config_path']}")
        
        # Show input specifications
        if input_info['input_specifications']:
            console.print("\n[bold]Input Specifications:[/bold]")
            spec_table = Table()
            spec_table.add_column("Input Name", style="cyan")
            spec_table.add_column("Type", style="magenta")
            spec_table.add_column("Required", style="yellow")
            spec_table.add_column("Default", style="green")
            spec_table.add_column("Description", style="dim")
            
            for input_name, spec in input_info['input_specifications'].items():
                required = "Yes" if spec['required'] else "No"
                default = str(spec['default']) if spec['default'] is not None else "None"
                description = spec['description'] or ""
                
                spec_table.add_row(
                    input_name,
                    spec['type'],
                    required,
                    default,
                    description
                )
            
            console.print(spec_table)
        else:
            console.print("[yellow]No input specifications defined[/yellow]")
        
        # Show active instances
        if input_info['active_instances']:
            console.print(f"\n[bold]Active Instances ({len(input_info['active_instances'])}):[/bold]")
            for instance in input_info['active_instances']:
                console.print(f"\n[cyan]Instance:[/cyan] {instance['instance_id']}")
                console.print(f"[blue]Output:[/blue] {instance['output_path']}")
                
                if instance['input_values']:
                    console.print("[green]Input Values:[/green]")
                    for input_name, value in instance['input_values'].items():
                        console.print(f"  {input_name}: {value}")
                else:
                    console.print("[yellow]No input values set[/yellow]")
        else:
            console.print("\n[yellow]No active instances[/yellow]")
        
    except Exception as e:
        console.print(f"[red]Failed to show inputs: {e}[/red]")
        sys.exit(1)


@main.command('show-watched-files')
@click.option('--node-id', help='Show watched files for specific node only')
@click.pass_context
@handle_async
async def show_watched_files(ctx: click.Context, node_id: Optional[str]) -> None:
    """Show files being watched by the daemon."""
    config_dir = ctx.obj.get('config_dir')
    daemon_instance = LivingTemplatesDaemon(config_dir)
    
    try:
        await daemon_instance.initialize()
        watch_info = await daemon_instance.get_watched_files(node_id)
        
        if node_id:
            console.print(f"[bold blue]Watched Files for Node:[/bold blue] {node_id}")
        else:
            console.print("[bold blue]All Watched Files[/bold blue]")
        
        if not watch_info['watched_files']:
            if node_id:
                console.print(f"[yellow]No files being watched for node {node_id}[/yellow]")
            else:
                console.print("[yellow]No files being watched[/yellow]")
            return
        
        table = Table()
        table.add_column("File Path", style="cyan")
        table.add_column("Watching Nodes", style="magenta")
        table.add_column("Exists", style="green")
        
        for file_path, watching_nodes in watch_info['watched_files'].items():
            exists = "Yes" if Path(file_path).exists() else "No"
            nodes_str = ", ".join(watching_nodes)
            
            table.add_row(file_path, nodes_str, exists)
        
        console.print(table)
        
        if not node_id:
            console.print(f"\n[blue]Total files watched:[/blue] {watch_info['total_files']}")
            console.print(f"[blue]Total watchers:[/blue] {watch_info['total_watchers']}")
        
    except Exception as e:
        console.print(f"[red]Failed to show watched files: {e}[/red]")
        sys.exit(1)


@main.command('show-file-inputs')
@click.argument('node_id')
@click.pass_context
@handle_async
async def show_file_inputs(ctx: click.Context, node_id: str) -> None:
    """Show file inputs for a specific node."""
    config_dir = ctx.obj.get('config_dir')
    daemon_instance = LivingTemplatesDaemon(config_dir)
    
    try:
        await daemon_instance.initialize()
        file_inputs = await daemon_instance.get_node_file_inputs(node_id)
        
        console.print(f"[bold blue]File Inputs for Node:[/bold blue] {node_id}")
        
        if not file_inputs:
            console.print("[yellow]No file inputs found for this node[/yellow]")
            return
        
        table = Table()
        table.add_column("Instance ID", style="cyan")
        table.add_column("Input Name", style="magenta")
        table.add_column("File Path", style="blue")
        table.add_column("Exists", style="green")
        table.add_column("Watched", style="yellow")
        table.add_column("Output Path", style="dim")
        
        for file_input in file_inputs:
            exists = "Yes" if file_input['exists'] else "No"
            watched = "Yes" if file_input['is_watched'] else "No"
            
            table.add_row(
                file_input['instance_id'][:8] + "...",  # Truncate instance ID
                file_input['input_name'],
                file_input['file_path'],
                exists,
                watched,
                file_input['output_path']
            )
        
        console.print(table)
        
    except Exception as e:
        console.print(f"[red]Failed to show file inputs: {e}[/red]")
        sys.exit(1)


@main.command()
@click.argument('config_file', type=click.Path(exists=True, path_type=Path))
@click.pass_context
def validate(ctx: click.Context, config_file: Path) -> None:
    """Validate a configuration file."""
    config_dir = ctx.obj.get('config_dir')
    config_manager = ConfigManager(config_dir)
    
    try:
        config, content = config_manager.load_node_config(config_file)
        console.print(f"[green]✓ Configuration is valid[/green]")
        console.print(f"[blue]Node type:[/blue] {config.node_type.value}")
        console.print(f"[blue]Outputs:[/blue] {', '.join(config.outputs)}")
        console.print(f"[blue]Inputs:[/blue] {len(config.inputs)}")
    except Exception as e:
        console.print(f"[red]✗ Configuration is invalid: {e}[/red]")
        sys.exit(1)


# Short form CLI (lt command)
@click.command()
@click.option('-s', '--source', 'template_file', required=True,
              type=click.Path(exists=True, path_type=Path),
              help='Template file to use')
@click.argument('output_path', type=click.Path(path_type=Path))
@click.option('--input', 'inputs', multiple=True,
              help='Input values in key=value format')
@click.option('--config', 'config_file',
              type=click.Path(exists=True, path_type=Path),
              help='YAML/JSON file with input values')
@click.option('--force', is_flag=True,
              help='Force regeneration even if up to date')
@click.option('--dry-run', is_flag=True,
              help='Show what would be generated without creating files')
@click.option('--config-dir',
              type=click.Path(exists=False, file_okay=False, dir_okay=True, path_type=Path),
              help='Configuration directory (default: ~/.living-templates)')
@handle_async
async def lt_main(
    template_file: Path,
    output_path: Path,
    inputs: tuple,
    config_file: Optional[Path],
    force: bool,
    dry_run: bool,
    config_dir: Optional[Path]
) -> None:
    """Create a living template instance.
    
    TEMPLATE_FILE: Path to the template configuration file
    OUTPUT_PATH: Where to create the generated file
    """
    daemon_instance = LivingTemplatesDaemon(config_dir)
    
    try:
        await daemon_instance.initialize()
        
        # Parse input values
        input_values = {}
        
        # Load from config file if provided
        if config_file:
            if config_file.suffix.lower() in ['.yaml', '.yml']:
                with open(config_file) as f:
                    file_inputs = yaml.safe_load(f)
                    if 'inputs' in file_inputs:
                        input_values.update(file_inputs['inputs'])
                    else:
                        input_values.update(file_inputs)
            elif config_file.suffix.lower() == '.json':
                with open(config_file) as f:
                    file_inputs = json.load(f)
                    if 'inputs' in file_inputs:
                        input_values.update(file_inputs['inputs'])
                    else:
                        input_values.update(file_inputs)
        
        # Parse CLI input arguments
        for input_arg in inputs:
            if '=' not in input_arg:
                console.print(f"[red]Invalid input format: {input_arg}[/red]")
                console.print("Use format: --input key=value")
                sys.exit(1)
            
            key, value = input_arg.split('=', 1)
            # Try to parse as JSON for complex types
            try:
                input_values[key] = json.loads(value)
            except json.JSONDecodeError:
                input_values[key] = value
        
        if dry_run:
            console.print("[yellow]Dry run mode - no files will be created[/yellow]")
            console.print(f"[blue]Template:[/blue] {template_file}")
            console.print(f"[blue]Output:[/blue] {output_path}")
            console.print(f"[blue]Inputs:[/blue] {json.dumps(input_values, indent=2)}")
            return
        
        # Register node if not already registered
        node_id = await daemon_instance.register_node(template_file)
        
        # Create instance
        instance_id = await daemon_instance.create_instance(
            node_id,
            str(output_path),
            input_values
        )
        
        console.print(f"[green]✓ Created template instance[/green]")
        console.print(f"[blue]Node ID:[/blue] {node_id}")
        console.print(f"[blue]Instance ID:[/blue] {instance_id}")
        console.print(f"[blue]Output:[/blue] {output_path}")
        
        if output_path.exists():
            console.print(f"[green]✓ Generated file created successfully[/green]")
        
    except Exception as e:
        console.print(f"[red]Failed to create template instance: {e}[/red]")
        sys.exit(1)


if __name__ == '__main__':
    main() 