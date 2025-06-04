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
from .client import LivingTemplatesClient


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
@click.option('--port', default=8765, help='API server port')
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
        # Update daemon port
        daemon_instance.api_port = port
        
        await daemon_instance.start()
        console.print(f"[green]Daemon started successfully[/green]")
        console.print(f"PID: {os.getpid()}")
        console.print(f"API server: http://{host}:{port}")
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
    # Try to connect to running daemon first
    async with LivingTemplatesClient() as client:
        if await client.is_daemon_running():
            try:
                node_id = await client.register_node(config_file)
                console.print(f"[green]Registered node:[/green] {node_id}")
                console.print(f"[blue]Config file:[/blue] {config_file}")
                return
            except Exception as e:
                console.print(f"[red]Failed to register via daemon: {e}[/red]")
                console.print("[yellow]Falling back to direct registration...[/yellow]")
    
    # Fall back to direct registration
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
    # Try to connect to running daemon first
    async with LivingTemplatesClient() as client:
        if await client.is_daemon_running():
            try:
                await client.unregister_node(node_id)
                console.print(f"[green]Unregistered node:[/green] {node_id}")
                return
            except Exception as e:
                console.print(f"[red]Failed to unregister via daemon: {e}[/red]")
                console.print("[yellow]Falling back to direct operation...[/yellow]")
    
    # Fall back to direct operation
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
    # Try to connect to running daemon first
    async with LivingTemplatesClient() as client:
        if await client.is_daemon_running():
            try:
                nodes_data = await client.list_nodes()
                
                if not nodes_data:
                    console.print("[yellow]No nodes registered[/yellow]")
                    return
                
                # Create table
                table = Table(title="Registered Nodes")
                table.add_column("Node ID", style="cyan")
                table.add_column("Type", style="magenta")
                table.add_column("Config Path", style="green")
                table.add_column("Outputs", style="yellow")
                table.add_column("Created", style="blue")
                
                for node in nodes_data:
                    outputs = ", ".join(node.get("outputs", []))
                    created = node.get("created_at", "Unknown")
                    if created and created != "Unknown":
                        # Format datetime
                        from datetime import datetime
                        try:
                            dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
                            created = dt.strftime("%Y-%m-%d %H:%M")
                        except:
                            pass
                    
                    table.add_row(
                        node["id"],
                        node["node_type"],
                        node.get("config_path", "N/A"),
                        outputs,
                        created
                    )
                
                console.print(table)
                return
                
            except Exception as e:
                console.print(f"[red]Failed to get nodes from daemon: {e}[/red]")
                console.print("[yellow]Falling back to direct access...[/yellow]")
    
    # Fall back to direct access
    config_dir = ctx.obj.get('config_dir')
    daemon_instance = LivingTemplatesDaemon(config_dir)
    
    try:
        await daemon_instance.initialize()
        nodes = await daemon_instance.list_nodes()
        
        if not nodes:
            console.print("[yellow]No nodes registered[/yellow]")
            return
        
        # Create table
        table = Table(title="Registered Nodes")
        table.add_column("Node ID", style="cyan")
        table.add_column("Type", style="magenta")
        table.add_column("Config Path", style="green")
        table.add_column("Outputs", style="yellow")
        table.add_column("Created", style="blue")
        
        for node in nodes:
            outputs = ", ".join(node.config.outputs)
            created = node.created_at.strftime("%Y-%m-%d %H:%M") if node.created_at else "Unknown"
            
            table.add_row(
                node.id,
                node.config.node_type.value,
                str(node.config_path) if node.config_path else "N/A",
                outputs,
                created
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
    """Show input specifications for a node."""
    async with LivingTemplatesClient() as client:
        if await client.is_daemon_running():
            try:
                inputs_data = await client.get_node_inputs(node_id)
                
                console.print(f"[bold cyan]Node:[/bold cyan] {node_id}")
                console.print(f"[bold cyan]Type:[/bold cyan] {inputs_data.get('node_type', 'Unknown')}")
                console.print()
                
                if not inputs_data.get('inputs'):
                    console.print("[yellow]No inputs defined[/yellow]")
                    return
                
                # Create table
                table = Table(title="Node Inputs")
                table.add_column("Input Name", style="cyan")
                table.add_column("Type", style="magenta")
                table.add_column("Required", style="red")
                table.add_column("Default", style="green")
                table.add_column("Source", style="blue")
                table.add_column("Description", style="yellow")
                
                for input_name, input_spec in inputs_data['inputs'].items():
                    required = "Yes" if input_spec.get('required', True) else "No"
                    default = str(input_spec.get('default', '')) if input_spec.get('default') is not None else ""
                    source = input_spec.get('source', '')
                    description = input_spec.get('description', '')
                    
                    table.add_row(
                        input_name,
                        input_spec['type'],
                        required,
                        default,
                        source,
                        description
                    )
                
                console.print(table)
                return
                
            except Exception as e:
                console.print(f"[red]Failed to get node inputs: {e}[/red]")
                return
    
    console.print("[red]Daemon is not running[/red]")


@main.command('show-watched-files')
@click.option('--node-id', help='Show watched files for specific node only')
@click.pass_context
@handle_async
async def show_watched_files(ctx: click.Context, node_id: Optional[str]) -> None:
    """Show currently watched files."""
    async with LivingTemplatesClient() as client:
        if await client.is_daemon_running():
            try:
                watched_data = await client.get_watched_files(node_id)
                
                if node_id:
                    console.print(f"[bold cyan]Watched files for node:[/bold cyan] {node_id}")
                    console.print()
                    
                    watched_files = watched_data.get('watched_files', [])
                    if not watched_files:
                        console.print("[yellow]No files watched by this node[/yellow]")
                        return
                    
                    table = Table(title=f"Files watched by {node_id}")
                    table.add_column("File Path", style="cyan")
                    table.add_column("Exists", style="green")
                    
                    for file_info in watched_files:
                        exists = "Yes" if file_info['exists'] else "No"
                        table.add_row(file_info['file_path'], exists)
                    
                    console.print(table)
                else:
                    console.print("[bold cyan]All watched files:[/bold cyan]")
                    console.print()
                    
                    file_watched = watched_data.get('file_watched', {})
                    tail_watched = watched_data.get('tail_watched', {})
                    
                    if not file_watched and not tail_watched:
                        console.print("[yellow]No files are being watched[/yellow]")
                        return
                    
                    if file_watched:
                        table = Table(title="File System Watches")
                        table.add_column("File Path", style="cyan")
                        table.add_column("Watching Nodes", style="magenta")
                        table.add_column("Exists", style="green")
                        
                        for file_path, info in file_watched.items():
                            nodes = ", ".join(info['watching_nodes'])
                            exists = "Yes" if info['exists'] else "No"
                            table.add_row(file_path, nodes, exists)
                        
                        console.print(table)
                        console.print()
                    
                    if tail_watched:
                        table = Table(title="Tail Watches")
                        table.add_column("File Path", style="cyan")
                        table.add_column("Type", style="magenta")
                        table.add_column("Exists", style="green")
                        
                        for file_path, info in tail_watched.items():
                            exists = "Yes" if info['exists'] else "No"
                            table.add_row(file_path, info['type'], exists)
                        
                        console.print(table)
                
                return
                
            except Exception as e:
                console.print(f"[red]Failed to get watched files: {e}[/red]")
                return
    
    console.print("[red]Daemon is not running[/red]")


@main.command('show-file-inputs')
@click.argument('node_id')
@click.pass_context
@handle_async
async def show_file_inputs(ctx: click.Context, node_id: str) -> None:
    """Show file inputs for a node."""
    async with LivingTemplatesClient() as client:
        if await client.is_daemon_running():
            try:
                file_inputs = await client.get_node_file_inputs(node_id)
                
                console.print(f"[bold cyan]File inputs for node:[/bold cyan] {node_id}")
                console.print()
                
                if not file_inputs:
                    console.print("[yellow]No file inputs defined[/yellow]")
                    return
                
                table = Table(title="File Inputs")
                table.add_column("Input Name", style="cyan")
                table.add_column("Required", style="red")
                table.add_column("Default", style="green")
                table.add_column("Description", style="yellow")
                
                for file_input in file_inputs:
                    required = "Yes" if file_input.get('required', True) else "No"
                    default = str(file_input.get('default', '')) if file_input.get('default') is not None else ""
                    description = file_input.get('description', '')
                    
                    table.add_row(
                        file_input['input_name'],
                        required,
                        default,
                        description
                    )
                
                console.print(table)
                return
                
            except Exception as e:
                console.print(f"[red]Failed to get file inputs: {e}[/red]")
                return
    
    console.print("[red]Daemon is not running[/red]")


@main.command()
@click.argument('config_file', type=click.Path(exists=True, path_type=Path))
@click.pass_context
def validate(ctx: click.Context, config_file: Path) -> None:
    """Validate a node configuration file."""
    try:
        config_manager = ConfigManager()
        config, content = config_manager.load_node_config(config_file)
        
        console.print(f"[green]✓ Configuration is valid[/green]")
        console.print(f"[blue]Node type:[/blue] {config.node_type.value}")
        console.print(f"[blue]Inputs:[/blue] {len(config.inputs)}")
        console.print(f"[blue]Outputs:[/blue] {len(config.outputs)}")
        
        if config.inputs:
            console.print("\n[bold]Inputs:[/bold]")
            for input_name, input_spec in config.inputs.items():
                required = " (required)" if input_spec.required else " (optional)"
                console.print(f"  • {input_name}: {input_spec.type.value}{required}")
        
        if config.outputs:
            console.print("\n[bold]Outputs:[/bold]")
            for output in config.outputs:
                console.print(f"  • {output}")
        
    except Exception as e:
        console.print(f"[red]✗ Configuration is invalid: {e}[/red]")
        sys.exit(1)


@main.command()
@click.argument('node_id')
@click.pass_context
@handle_async
async def rebuild(ctx: click.Context, node_id: str) -> None:
    """Force rebuild a node's instances."""
    async with LivingTemplatesClient() as client:
        if await client.is_daemon_running():
            try:
                await client.rebuild_node(node_id)
                console.print(f"[green]Rebuilt node instances:[/green] {node_id}")
                return
            except Exception as e:
                console.print(f"[red]Failed to rebuild node: {e}[/red]")
                return
    
    console.print("[red]Daemon is not running[/red]")


@main.command()
@click.option('--node-id', help='Show graph for specific node only')
@click.option('--format', 'output_format', default='text', 
              type=click.Choice(['text', 'json']), help='Output format')
@click.pass_context
@handle_async
async def graph(ctx: click.Context, node_id: Optional[str], output_format: str) -> None:
    """Show dependency graph."""
    async with LivingTemplatesClient() as client:
        if await client.is_daemon_running():
            try:
                graph_data = await client.get_dependency_graph(node_id)
                
                if output_format == 'json':
                    console.print(json.dumps(graph_data, indent=2))
                    return
                
                nodes = graph_data.get('nodes', [])
                edges = graph_data.get('edges', [])
                
                if not nodes:
                    console.print("[yellow]No nodes in graph[/yellow]")
                    return
                
                # Show nodes
                console.print("[bold cyan]Nodes:[/bold cyan]")
                table = Table()
                table.add_column("Node ID", style="cyan")
                table.add_column("Type", style="magenta")
                table.add_column("Outputs", style="yellow")
                table.add_column("Config Path", style="green")
                
                for node in nodes:
                    outputs = ", ".join(node.get('outputs', []))
                    config_path = node.get('config_path', 'N/A')
                    
                    table.add_row(
                        node['id'],
                        node['type'],
                        outputs,
                        config_path
                    )
                
                console.print(table)
                
                # Show dependencies
                if edges:
                    console.print("\n[bold cyan]Dependencies:[/bold cyan]")
                    dep_table = Table()
                    dep_table.add_column("From Node", style="green")
                    dep_table.add_column("Output", style="yellow")
                    dep_table.add_column("To Node", style="cyan")
                    
                    for edge in edges:
                        dep_table.add_row(
                            edge['from'],
                            edge['output'],
                            edge['to']
                        )
                    
                    console.print(dep_table)
                else:
                    console.print("\n[yellow]No dependencies found[/yellow]")
                
                return
                
            except Exception as e:
                console.print(f"[red]Failed to get dependency graph: {e}[/red]")
                return
    
    console.print("[red]Daemon is not running[/red]")


@main.command('logs')
@click.argument('node_id')
@click.option('--limit', default=50, help='Number of log entries to show')
@click.option('--level', type=click.Choice(['debug', 'info', 'warning', 'error']),
              help='Filter by log level')
@click.pass_context
@handle_async
async def logs(ctx: click.Context, node_id: str, limit: int, level: Optional[str]) -> None:
    """Show execution logs for a node."""
    async with LivingTemplatesClient() as client:
        if await client.is_daemon_running():
            try:
                logs_data = await client.get_node_logs(node_id, limit)
                
                if not logs_data:
                    console.print(f"[yellow]No logs found for node:[/yellow] {node_id}")
                    return
                
                # Filter by level if specified
                if level:
                    logs_data = [log for log in logs_data if log['level'].lower() == level.lower()]
                
                console.print(f"[bold cyan]Logs for node:[/bold cyan] {node_id}")
                console.print()
                
                for log in logs_data:
                    # Color code by level
                    level_colors = {
                        'debug': 'blue',
                        'info': 'green',
                        'warning': 'yellow',
                        'error': 'red'
                    }
                    level_color = level_colors.get(log['level'].lower(), 'white')
                    
                    timestamp = log['timestamp']
                    try:
                        from datetime import datetime
                        dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                        timestamp = dt.strftime("%Y-%m-%d %H:%M:%S")
                    except:
                        pass
                    
                    console.print(f"[{level_color}]{timestamp} [{log['level'].upper()}][/{level_color}] {log['message']}")
                    
                    if log.get('details') and isinstance(log['details'], dict):
                        for key, value in log['details'].items():
                            console.print(f"  {key}: {value}")
                    
                    console.print()
                
                return
                
            except Exception as e:
                console.print(f"[red]Failed to get logs: {e}[/red]")
                return
    
    console.print("[red]Daemon is not running[/red]")


@main.command('list-instances')
@click.option('--node-id', help='Filter by node ID')
@click.pass_context
@handle_async
async def list_instances(ctx: click.Context, node_id: Optional[str]) -> None:
    """List node instances."""
    async with LivingTemplatesClient() as client:
        if await client.is_daemon_running():
            try:
                instances_data = await client.list_instances(node_id)
                
                if not instances_data:
                    filter_msg = f" for node {node_id}" if node_id else ""
                    console.print(f"[yellow]No instances found{filter_msg}[/yellow]")
                    return
                
                title = f"Instances for node {node_id}" if node_id else "All Instances"
                table = Table(title=title)
                table.add_column("Instance ID", style="cyan")
                table.add_column("Node ID", style="magenta")
                table.add_column("Output Path", style="green")
                table.add_column("Build Count", style="yellow")
                table.add_column("Last Built", style="blue")
                
                for instance in instances_data:
                    last_built = instance.get('last_built', 'Never')
                    if last_built and last_built != 'Never':
                        try:
                            from datetime import datetime
                            dt = datetime.fromisoformat(last_built.replace('Z', '+00:00'))
                            last_built = dt.strftime("%Y-%m-%d %H:%M:%S")
                        except:
                            pass
                    
                    table.add_row(
                        instance['id'][:12] + "...",  # Truncate ID for display
                        instance['node_id'],
                        instance['output_path'],
                        str(instance.get('build_count', 0)),
                        last_built
                    )
                
                console.print(table)
                return
                
            except Exception as e:
                console.print(f"[red]Failed to get instances: {e}[/red]")
                return
    
    console.print("[red]Daemon is not running[/red]")


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
    """Create an instance of a template with specific inputs (lt command)."""
    
    # Parse input values
    input_values = {}
    
    # Load from config file if provided
    if config_file:
        try:
            with open(config_file, 'r') as f:
                if config_file.suffix.lower() in ['.yaml', '.yml']:
                    config_data = yaml.safe_load(f)
                else:
                    config_data = json.load(f)
            
            if 'inputs' in config_data:
                input_values.update(config_data['inputs'])
            else:
                input_values.update(config_data)
                
        except Exception as e:
            console.print(f"[red]Failed to load config file: {e}[/red]")
            sys.exit(1)
    
    # Parse command line inputs (these override config file)
    for input_str in inputs:
        if '=' not in input_str:
            console.print(f"[red]Invalid input format: {input_str}[/red]")
            console.print("Use format: key=value")
            sys.exit(1)
        
        key, value = input_str.split('=', 1)
        
        # Try to parse as JSON for complex types
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            # Keep as string
            pass
        
        input_values[key] = value
    
    try:
        # Try daemon first
        async with LivingTemplatesClient() as client:
            if await client.is_daemon_running():
                try:
                    # Register node if not already registered
                    node_id = await client.register_node(template_file)
                    
                    if dry_run:
                        console.print(f"[yellow]Would create instance:[/yellow]")
                        console.print(f"  Template: {template_file}")
                        console.print(f"  Output: {output_path}")
                        console.print(f"  Inputs: {input_values}")
                        return
                    
                    # Create instance
                    instance_id = await client.create_instance(node_id, str(output_path), input_values)
                    
                    console.print(f"[green]✓ Instance created successfully[/green]")
                    console.print(f"Node ID: {node_id}")
                    console.print(f"Instance ID: {instance_id}")
                    console.print(f"Output: {output_path}")
                    return
                    
                except Exception as e:
                    console.print(f"[red]Failed via daemon: {e}[/red]")
                    console.print("[yellow]Falling back to direct processing...[/yellow]")
        
        # Fall back to direct processing
        if dry_run:
            console.print(f"[yellow]Would create instance:[/yellow]")
            console.print(f"  Template: {template_file}")
            console.print(f"  Output: {output_path}")
            console.print(f"  Inputs: {input_values}")
            return
        
        # Direct template processing
        config_manager = ConfigManager(config_dir)
        config, content = config_manager.load_node_config(template_file)
        
        if config.node_type != NodeType.TEMPLATE:
            console.print(f"[red]Node type {config.node_type.value} not supported in direct mode[/red]")
            console.print("Start the daemon to use all node types.")
            sys.exit(1)
        
        # Validate required inputs
        for input_name, input_spec in config.inputs.items():
            if input_spec.required and input_name not in input_values:
                if input_spec.default is not None:
                    input_values[input_name] = input_spec.default
                else:
                    console.print(f"[red]Required input missing: {input_name}[/red]")
                    sys.exit(1)
        
        # Render template
        template_engine = TemplateEngine()
        rendered_content = template_engine.render(content, input_values)
        
        # Write output
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered_content, encoding='utf-8')
        
        console.print(f"[green]✓ Template rendered successfully[/green]")
        console.print(f"Output written to: {output_path}")
        
    except Exception as e:
        console.print(f"[red]Failed to process template: {e}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    # Register the lt command
    import sys
    if len(sys.argv) > 0 and 'lt' in sys.argv[0]:
        lt_main()
    else:
        main() 