"""Tail watcher for monitoring file changes."""

import asyncio
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .models import TailState


class TailWatcher:
    """Watches files for new content and processes it."""
    
    def __init__(self):
        """Initialize tail watcher."""
        self.watched_files: Dict[str, TailState] = {}
        self.callbacks: Dict[str, List[Callable]] = {}
        self.running = False
        self.watch_task: Optional[asyncio.Task] = None
    
    def add_file_watch(
        self, 
        node_id: str, 
        file_path: str, 
        callback: Callable[[str, List[str]], None],
        tail_lines: int = 10,
        separator: str = "\n"
    ) -> None:
        """Add a file to watch for tail updates.
        
        Args:
            node_id: ID of the node watching this file
            file_path: Path to the file to watch
            callback: Function to call with new lines
            tail_lines: Number of lines to keep in buffer
            separator: Line separator
        """
        path = str(Path(file_path).resolve())
        
        if path not in self.watched_files:
            # Initialize tail state
            state = TailState(
                node_id=node_id,
                file_path=path,
                buffer=[]
            )
            
            # Read initial content if file exists
            if Path(path).exists():
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        f.seek(0, 2)  # Seek to end
                        state.last_position = f.tell()
                        
                        # Get file inode for rotation detection
                        stat = os.stat(path)
                        state.last_inode = stat.st_ino
                        
                        # Read last N lines for buffer
                        f.seek(0)
                        lines = f.readlines()
                        state.buffer = [line.rstrip(separator) for line in lines[-tail_lines:]]
                        
                except (IOError, OSError) as e:
                    # File might be locked or permission denied
                    state.last_position = 0
                    state.buffer = []
            
            self.watched_files[path] = state
            self.callbacks[path] = []
        
        # Add callback for this node
        if callback not in self.callbacks[path]:
            self.callbacks[path].append(callback)
    
    def remove_file_watch(self, node_id: str, file_path: str) -> None:
        """Remove file watch for a specific node.
        
        Args:
            node_id: ID of the node
            file_path: Path to the file
        """
        path = str(Path(file_path).resolve())
        
        if path in self.watched_files:
            # Remove callbacks for this node
            # Note: We don't have a direct node->callback mapping, 
            # so we remove the entire file watch. In practice, each
            # file should typically be watched by one node.
            if path in self.callbacks:
                del self.callbacks[path]
            del self.watched_files[path]
    
    async def start_watching(self) -> None:
        """Start the tail watching process."""
        if self.running:
            return
        
        self.running = True
        self.watch_task = asyncio.create_task(self._watch_loop())
    
    async def stop_watching(self) -> None:
        """Stop the tail watching process."""
        self.running = False
        if self.watch_task:
            self.watch_task.cancel()
            try:
                await self.watch_task
            except asyncio.CancelledError:
                pass
            self.watch_task = None
    
    async def _watch_loop(self) -> None:
        """Main watch loop."""
        while self.running:
            try:
                for file_path in list(self.watched_files.keys()):
                    await self._check_file_changes(file_path)
                
                # Sleep between checks
                await asyncio.sleep(0.5)  # Check every 500ms
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                # Log error but continue watching
                print(f"Error in tail watcher: {e}")
                await asyncio.sleep(1)
    
    async def _check_file_changes(self, file_path: str) -> None:
        """Check a specific file for changes.
        
        Args:
            file_path: Path to the file to check
        """
        state = self.watched_files[file_path]
        path = Path(file_path)
        
        try:
            if not path.exists():
                # File doesn't exist, reset state
                state.last_position = 0
                state.last_inode = None
                return
            
            stat = os.stat(file_path)
            current_inode = stat.st_ino
            current_size = stat.st_size
            
            # Check for file rotation (inode changed)
            if state.last_inode is not None and current_inode != state.last_inode:
                # File was rotated, start from beginning
                state.last_position = 0
                state.last_inode = current_inode
                state.buffer = []
            
            # Check if file grew
            if current_size > state.last_position:
                with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                    f.seek(state.last_position)
                    new_content = f.read()
                    state.last_position = f.tell()
                    state.last_inode = current_inode
                
                if new_content:
                    # Split into lines
                    lines = new_content.split('\n')
                    
                    # First line might be continuation of previous line
                    if state.buffer and lines:
                        state.buffer[-1] += lines[0]
                        lines = lines[1:]
                    
                    # Add new complete lines to buffer
                    new_lines = []
                    for line in lines[:-1]:  # All but last (might be incomplete)
                        state.buffer.append(line)
                        new_lines.append(line)
                    
                    # Keep buffer size limited
                    max_lines = 1000  # Reasonable limit
                    if len(state.buffer) > max_lines:
                        state.buffer = state.buffer[-max_lines:]
                    
                    # Handle last line (might be incomplete)
                    if lines and lines[-1]:
                        state.buffer.append(lines[-1])
                    
                    # Notify callbacks of new lines
                    if new_lines and file_path in self.callbacks:
                        for callback in self.callbacks[file_path]:
                            try:
                                if asyncio.iscoroutinefunction(callback):
                                    await callback(state.node_id, new_lines)
                                else:
                                    callback(state.node_id, new_lines)
                            except Exception as e:
                                print(f"Error in tail callback: {e}")
            
            elif current_size < state.last_position:
                # File was truncated, start from beginning
                state.last_position = 0
                state.buffer = []
        
        except (IOError, OSError) as e:
            # File might be locked, moved, or permission denied
            # Just skip this check
            pass
    
    def get_buffer(self, file_path: str) -> List[str]:
        """Get current buffer for a file.
        
        Args:
            file_path: Path to the file
            
        Returns:
            List of lines in buffer
        """
        path = str(Path(file_path).resolve())
        if path in self.watched_files:
            return self.watched_files[path].buffer.copy()
        return []
    
    def get_watched_files(self) -> List[str]:
        """Get list of watched file paths."""
        return list(self.watched_files.keys()) 