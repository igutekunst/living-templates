"""Storage layer for Living Templates."""

import hashlib
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite

from .models import DependencyEdge, NodeConfig, NodeInstance, NodeValue, TemplateNode


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
    
    def store_content(self, content: str) -> Tuple[str, Path]:
        """Store content and return hash and path.
        
        Returns:
            Tuple of (content_hash, content_path)
        """
        content_hash = self._hash_content(content)
        content_path = self.store_path / content_hash
        
        # Only write if file doesn't exist (content-addressed)
        if not content_path.exists():
            content_path.write_text(content, encoding='utf-8')
        
        return content_hash, content_path
    
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
                
                CREATE INDEX IF NOT EXISTS idx_dependencies_dependent ON dependencies(dependent_node_id);
                CREATE INDEX IF NOT EXISTS idx_dependencies_dependency ON dependencies(dependency_node_id);
                CREATE INDEX IF NOT EXISTS idx_node_values_updated ON node_values(updated_at);
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
                INSERT OR REPLACE INTO node_instances (id, node_id, input_config, output_path)
                VALUES (?, ?, ?, ?)
            """, (
                instance.id,
                instance.node_id,
                json.dumps(instance.input_values),
                instance.output_path
            ))
            await db.commit()
    
    async def store_node_value(self, value: NodeValue) -> None:
        """Store a node output value."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO node_values 
                (node_id, output_name, value_hash, value_data, content_path, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                value.node_id,
                value.output_name,
                value.value_hash,
                json.dumps(value.value_data),
                value.content_path,
                value.updated_at.isoformat()
            ))
            await db.commit()
    
    async def get_node_value(self, node_id: str, output_name: str) -> Optional[NodeValue]:
        """Get a node's output value."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT node_id, output_name, value_hash, value_data, content_path, updated_at
                FROM node_values WHERE node_id = ? AND output_name = ?
            """, (node_id, output_name)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return NodeValue(
                        node_id=row[0],
                        output_name=row[1],
                        value_hash=row[2],
                        value_data=json.loads(row[3]),
                        content_path=row[4],
                        updated_at=datetime.fromisoformat(row[5])
                    )
        return None
    
    async def store_dependency(self, dependency: DependencyEdge) -> None:
        """Store a dependency relationship."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO dependencies 
                (dependent_node_id, dependency_node_id, dependency_output)
                VALUES (?, ?, ?)
            """, (
                dependency.dependent_node_id,
                dependency.dependency_node_id,
                dependency.dependency_output
            ))
            await db.commit()
    
    async def get_dependents(self, node_id: str, output_name: str) -> List[str]:
        """Get nodes that depend on this node's output."""
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
        """Store symlink information."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO symlinks (target_path, content_hash, node_instance_id)
                VALUES (?, ?, ?)
            """, (target_path, content_hash, instance_id))
            await db.commit()
    
    async def remove_node(self, node_id: str) -> None:
        """Remove a node and all its data."""
        async with aiosqlite.connect(self.db_path) as db:
            # Remove in order due to foreign key constraints
            await db.execute("DELETE FROM dependencies WHERE dependent_node_id = ? OR dependency_node_id = ?", (node_id, node_id))
            await db.execute("DELETE FROM node_values WHERE node_id = ?", (node_id,))
            await db.execute("DELETE FROM node_instances WHERE node_id = ?", (node_id,))
            await db.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
            await db.commit() 