"""Storage layer for Living Templates."""

import hashlib
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite

from .models import (
    DependencyEdge, ExecutionLog, NodeConfig, NodeInstance, NodeValue, 
    OutputMode, TemplateNode, TailState, WebhookTrigger
)


class ContentStore:
    """Content-addressed storage for generated files."""
    
    def __init__(self, store_path: Path):
        """Initialize content store.
        
        Args:
            store_path: Path to the content store directory
        """
        self.store_path = store_path
        self.store_path.mkdir(parents=True, exist_ok=True)
    
    def _hash_content(self, content: str) -> str:
        """Generate hash for content."""
        return hashlib.sha256(content.encode('utf-8')).hexdigest()
    
    def store_content(self, content: str, output_mode: OutputMode = OutputMode.REPLACE) -> Tuple[str, Path]:
        """Store content and return hash and path.
        
        Args:
            content: Content to store
            output_mode: How to handle the content
            
        Returns:
            Tuple of (content_hash, content_path)
        """
        content_hash = self._hash_content(content)
        content_path = self.store_path / content_hash
        
        # Only write if file doesn't exist (content-addressed)
        if not content_path.exists():
            content_path.write_text(content, encoding='utf-8')
        
        return content_hash, content_path
    
    def append_content(self, existing_hash: Optional[str], new_content: str) -> Tuple[str, Path]:
        """Append content to existing content.
        
        Args:
            existing_hash: Hash of existing content (if any)
            new_content: New content to append
            
        Returns:
            Tuple of (new_content_hash, content_path)
        """
        if existing_hash:
            existing_content = self.get_content(existing_hash) or ""
            combined_content = existing_content + new_content
        else:
            combined_content = new_content
        
        return self.store_content(combined_content)
    
    def prepend_content(self, existing_hash: Optional[str], new_content: str) -> Tuple[str, Path]:
        """Prepend content to existing content.
        
        Args:
            existing_hash: Hash of existing content (if any)
            new_content: New content to prepend
            
        Returns:
            Tuple of (new_content_hash, content_path)
        """
        if existing_hash:
            existing_content = self.get_content(existing_hash) or ""
            combined_content = new_content + existing_content
        else:
            combined_content = new_content
        
        return self.store_content(combined_content)
    
    def get_content(self, content_hash: str) -> Optional[str]:
        """Retrieve content by hash."""
        content_path = self.store_path / content_hash
        if content_path.exists():
            return content_path.read_text(encoding='utf-8')
        return None
    
    def cleanup_unused(self, used_hashes: List[str]) -> int:
        """Remove unused content files.
        
        Args:
            used_hashes: List of hashes that are still in use
            
        Returns:
            Number of files removed
        """
        removed = 0
        for content_file in self.store_path.iterdir():
            if content_file.is_file() and content_file.name not in used_hashes:
                content_file.unlink()
                removed += 1
        return removed


class SymlinkManager:
    """Manages symlinks to content store."""
    
    def create_symlink(self, target_path: Path, content_path: Path) -> None:
        """Create symlink from target to content.
        
        Args:
            target_path: Where the symlink should be created
            content_path: What the symlink should point to
        """
        # Remove existing file/symlink
        if target_path.exists() or target_path.is_symlink():
            target_path.unlink()
        
        # Create parent directories
        target_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Create symlink
        target_path.symlink_to(content_path.resolve())
    
    def append_to_file(self, target_path: Path, new_content: str) -> None:
        """Append content to a file.
        
        Args:
            target_path: Path to the file
            new_content: Content to append
        """
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with open(target_path, 'a', encoding='utf-8') as f:
            f.write(new_content)
    
    def prepend_to_file(self, target_path: Path, new_content: str) -> None:
        """Prepend content to a file.
        
        Args:
            target_path: Path to the file
            new_content: Content to prepend
        """
        target_path.parent.mkdir(parents=True, exist_ok=True)
        
        existing_content = ""
        if target_path.exists():
            existing_content = target_path.read_text(encoding='utf-8')
        
        with open(target_path, 'w', encoding='utf-8') as f:
            f.write(new_content + existing_content)
    
    def remove_symlink(self, target_path: Path) -> None:
        """Remove symlink if it exists."""
        if target_path.is_symlink():
            target_path.unlink()


class Database:
    """SQLite database for Living Templates metadata."""
    
    def __init__(self, db_path: Path):
        """Initialize database.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
    
    async def initialize(self) -> None:
        """Initialize database schema."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS nodes (
                    id TEXT PRIMARY KEY,
                    node_type TEXT NOT NULL,
                    config_path TEXT,
                    config_data TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE TABLE IF NOT EXISTS node_instances (
                    id TEXT PRIMARY KEY,
                    node_id TEXT NOT NULL,
                    input_config TEXT,
                    output_path TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_built TIMESTAMP,
                    build_count INTEGER DEFAULT 0,
                    FOREIGN KEY (node_id) REFERENCES nodes(id)
                );
                
                CREATE TABLE IF NOT EXISTS node_values (
                    node_id TEXT,
                    output_name TEXT,
                    value_hash TEXT,
                    value_data TEXT,
                    content_path TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (node_id, output_name),
                    FOREIGN KEY (node_id) REFERENCES nodes(id)
                );
                
                CREATE TABLE IF NOT EXISTS dependencies (
                    dependent_node_id TEXT,
                    dependency_node_id TEXT,
                    dependency_output TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (dependent_node_id, dependency_node_id, dependency_output),
                    FOREIGN KEY (dependent_node_id) REFERENCES nodes(id),
                    FOREIGN KEY (dependency_node_id) REFERENCES nodes(id)
                );
                
                CREATE TABLE IF NOT EXISTS symlinks (
                    target_path TEXT PRIMARY KEY,
                    content_hash TEXT NOT NULL,
                    node_instance_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (node_instance_id) REFERENCES node_instances(id)
                );
                
                CREATE TABLE IF NOT EXISTS execution_logs (
                    id TEXT PRIMARY KEY,
                    node_id TEXT NOT NULL,
                    instance_id TEXT,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (node_id) REFERENCES nodes(id),
                    FOREIGN KEY (instance_id) REFERENCES node_instances(id)
                );
                
                CREATE TABLE IF NOT EXISTS tail_states (
                    node_id TEXT PRIMARY KEY,
                    file_path TEXT NOT NULL,
                    last_position INTEGER DEFAULT 0,
                    last_inode INTEGER,
                    buffer TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (node_id) REFERENCES nodes(id)
                );
                
                CREATE TABLE IF NOT EXISTS webhook_triggers (
                    id TEXT PRIMARY KEY,
                    node_id TEXT NOT NULL,
                    data TEXT NOT NULL,
                    headers TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    processed BOOLEAN DEFAULT FALSE,
                    FOREIGN KEY (node_id) REFERENCES nodes(id)
                );
                
                CREATE INDEX IF NOT EXISTS idx_dependencies_dependent ON dependencies(dependent_node_id);
                CREATE INDEX IF NOT EXISTS idx_dependencies_dependency ON dependencies(dependency_node_id);
                CREATE INDEX IF NOT EXISTS idx_node_values_updated ON node_values(updated_at);
                CREATE INDEX IF NOT EXISTS idx_execution_logs_node ON execution_logs(node_id);
                CREATE INDEX IF NOT EXISTS idx_execution_logs_timestamp ON execution_logs(timestamp);
                CREATE INDEX IF NOT EXISTS idx_webhook_triggers_node ON webhook_triggers(node_id);
                CREATE INDEX IF NOT EXISTS idx_webhook_triggers_processed ON webhook_triggers(processed);
            """)
            await db.commit()
    
    async def store_node(self, node: TemplateNode) -> None:
        """Store a node in the database."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO nodes (id, node_type, config_path, config_data, updated_at)
                VALUES (?, ?, ?, ?, ?)
            """, (
                node.id,
                node.config.node_type.value,
                str(node.config_path) if node.config_path else None,
                node.config.json(),
                datetime.now().isoformat()
            ))
            await db.commit()
    
    async def get_node(self, node_id: str) -> Optional[TemplateNode]:
        """Retrieve a node by ID."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT id, node_type, config_path, config_data, created_at
                FROM nodes WHERE id = ?
            """, (node_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    config_data = json.loads(row[3])
                    config = NodeConfig(**config_data)
                    return TemplateNode(
                        id=row[0],
                        config=config,
                        config_path=Path(row[2]) if row[2] else None,
                        created_at=datetime.fromisoformat(row[4])
                    )
        return None
    
    async def list_nodes(self) -> List[TemplateNode]:
        """List all nodes."""
        nodes = []
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT id, node_type, config_path, config_data, created_at
                FROM nodes ORDER BY created_at
            """) as cursor:
                async for row in cursor:
                    config_data = json.loads(row[3])
                    config = NodeConfig(**config_data)
                    nodes.append(TemplateNode(
                        id=row[0],
                        config=config,
                        config_path=Path(row[2]) if row[2] else None,
                        created_at=datetime.fromisoformat(row[4])
                    ))
        return nodes
    
    async def store_node_instance(self, instance: NodeInstance) -> None:
        """Store a node instance."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO node_instances 
                (id, node_id, input_config, output_path, created_at, last_built, build_count)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                instance.id,
                instance.node_id,
                json.dumps(instance.input_values),
                instance.output_path,
                instance.created_at.isoformat(),
                instance.last_built.isoformat() if instance.last_built else None,
                instance.build_count
            ))
            await db.commit()
    
    async def get_node_instances(self, node_id: Optional[str] = None) -> List[NodeInstance]:
        """Get node instances."""
        instances = []
        async with aiosqlite.connect(self.db_path) as db:
            if node_id:
                query = "SELECT * FROM node_instances WHERE node_id = ?"
                params = (node_id,)
            else:
                query = "SELECT * FROM node_instances"
                params = ()
            
            async with db.execute(query, params) as cursor:
                async for row in cursor:
                    instances.append(NodeInstance(
                        id=row[0],
                        node_id=row[1],
                        input_values=json.loads(row[2]),
                        output_path=row[3],
                        created_at=datetime.fromisoformat(row[4]),
                        last_built=datetime.fromisoformat(row[5]) if row[5] else None,
                        build_count=row[6]
                    ))
        return instances
    
    async def store_node_value(self, value: NodeValue) -> None:
        """Store a node value."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO node_values 
                (node_id, output_name, value_hash, value_data, content_path, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                value.node_id,
                value.output_name,
                value.value_hash,
                json.dumps(value.value_data) if not isinstance(value.value_data, str) else value.value_data,
                value.content_path,
                value.updated_at.isoformat()
            ))
            await db.commit()
    
    async def get_node_value(self, node_id: str, output_name: str) -> Optional[NodeValue]:
        """Get a node value."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT * FROM node_values WHERE node_id = ? AND output_name = ?
            """, (node_id, output_name)) as cursor:
                row = await cursor.fetchone()
                if row:
                    # Try to parse as JSON, fall back to string
                    try:
                        value_data = json.loads(row[3])
                    except (json.JSONDecodeError, TypeError):
                        value_data = row[3]
                    
                    return NodeValue(
                        node_id=row[0],
                        output_name=row[1],
                        value_hash=row[2],
                        value_data=value_data,
                        content_path=row[4],
                        updated_at=datetime.fromisoformat(row[5])
                    )
        return None
    
    async def store_dependency(self, dependency: DependencyEdge) -> None:
        """Store a dependency relationship."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO dependencies 
                (dependent_node_id, dependency_node_id, dependency_output, created_at)
                VALUES (?, ?, ?, ?)
            """, (
                dependency.dependent_node_id,
                dependency.dependency_node_id,
                dependency.dependency_output,
                datetime.now().isoformat()
            ))
            await db.commit()
    
    async def get_dependents(self, node_id: str, output_name: str) -> List[str]:
        """Get nodes that depend on a specific node output."""
        dependents = []
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT dependent_node_id FROM dependencies 
                WHERE dependency_node_id = ? AND dependency_output = ?
            """, (node_id, output_name)) as cursor:
                async for row in cursor:
                    dependents.append(row[0])
        return dependents
    
    async def store_symlink(self, target_path: str, content_hash: str, instance_id: str) -> None:
        """Store symlink metadata."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO symlinks (target_path, content_hash, node_instance_id, created_at)
                VALUES (?, ?, ?, ?)
            """, (target_path, content_hash, instance_id, datetime.now().isoformat()))
            await db.commit()
    
    async def store_execution_log(self, log: ExecutionLog) -> None:
        """Store an execution log entry."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO execution_logs (id, node_id, instance_id, level, message, details, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                log.id,
                log.node_id,
                log.instance_id,
                log.level.value,
                log.message,
                json.dumps(log.details) if log.details else None,
                log.timestamp.isoformat()
            ))
            await db.commit()
    
    async def get_execution_logs(self, node_id: str, limit: int = 100) -> List[ExecutionLog]:
        """Get execution logs for a node."""
        logs = []
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT * FROM execution_logs 
                WHERE node_id = ? 
                ORDER BY timestamp DESC 
                LIMIT ?
            """, (node_id, limit)) as cursor:
                async for row in cursor:
                    details = json.loads(row[5]) if row[5] else None
                    logs.append(ExecutionLog(
                        id=row[0],
                        node_id=row[1],
                        instance_id=row[2],
                        level=row[3],
                        message=row[4],
                        details=details,
                        timestamp=datetime.fromisoformat(row[6])
                    ))
        return logs
    
    async def store_tail_state(self, state: TailState) -> None:
        """Store tail state."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO tail_states 
                (node_id, file_path, last_position, last_inode, buffer, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                state.node_id,
                state.file_path,
                state.last_position,
                state.last_inode,
                json.dumps(state.buffer),
                state.updated_at.isoformat()
            ))
            await db.commit()
    
    async def get_tail_state(self, node_id: str) -> Optional[TailState]:
        """Get tail state for a node."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT * FROM tail_states WHERE node_id = ?
            """, (node_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    buffer = json.loads(row[4]) if row[4] else []
                    return TailState(
                        node_id=row[0],
                        file_path=row[1],
                        last_position=row[2],
                        last_inode=row[3],
                        buffer=buffer,
                        updated_at=datetime.fromisoformat(row[5])
                    )
        return None
    
    async def store_webhook_trigger(self, trigger: WebhookTrigger) -> str:
        """Store a webhook trigger."""
        trigger_id = trigger.node_id + "_" + str(int(trigger.timestamp.timestamp() * 1000))
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO webhook_triggers (id, node_id, data, headers, timestamp, processed)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                trigger_id,
                trigger.node_id,
                json.dumps(trigger.data),
                json.dumps(trigger.headers),
                trigger.timestamp.isoformat(),
                False
            ))
            await db.commit()
        return trigger_id
    
    async def get_pending_webhook_triggers(self, node_id: Optional[str] = None) -> List[WebhookTrigger]:
        """Get pending webhook triggers."""
        triggers = []
        async with aiosqlite.connect(self.db_path) as db:
            if node_id:
                query = "SELECT * FROM webhook_triggers WHERE node_id = ? AND processed = FALSE ORDER BY timestamp"
                params = (node_id,)
            else:
                query = "SELECT * FROM webhook_triggers WHERE processed = FALSE ORDER BY timestamp"
                params = ()
            
            async with db.execute(query, params) as cursor:
                async for row in cursor:
                    triggers.append(WebhookTrigger(
                        node_id=row[1],
                        data=json.loads(row[2]),
                        headers=json.loads(row[3]) if row[3] else {},
                        timestamp=datetime.fromisoformat(row[4])
                    ))
        return triggers
    
    async def mark_webhook_processed(self, trigger_id: str) -> None:
        """Mark a webhook trigger as processed."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                UPDATE webhook_triggers SET processed = TRUE WHERE id = ?
            """, (trigger_id,))
            await db.commit()
    
    async def remove_node(self, node_id: str) -> None:
        """Remove a node and all its related data."""
        async with aiosqlite.connect(self.db_path) as db:
            # Remove in order to respect foreign key constraints
            await db.execute("DELETE FROM symlinks WHERE node_instance_id IN (SELECT id FROM node_instances WHERE node_id = ?)", (node_id,))
            await db.execute("DELETE FROM execution_logs WHERE node_id = ?", (node_id,))
            await db.execute("DELETE FROM tail_states WHERE node_id = ?", (node_id,))
            await db.execute("DELETE FROM webhook_triggers WHERE node_id = ?", (node_id,))
            await db.execute("DELETE FROM dependencies WHERE dependent_node_id = ? OR dependency_node_id = ?", (node_id, node_id))
            await db.execute("DELETE FROM node_values WHERE node_id = ?", (node_id,))
            await db.execute("DELETE FROM node_instances WHERE node_id = ?", (node_id,))
            await db.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
            await db.commit() 