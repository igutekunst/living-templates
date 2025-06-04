"""Client SDK for Living Templates daemon."""

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import aiohttp


class LivingTemplatesClient:
    """Client SDK for interacting with Living Templates daemon."""
    
    def __init__(self, host: str = "localhost", port: int = 8765):
        """Initialize client.
        
        Args:
            host: Daemon host
            port: Daemon port
        """
        self.base_url = f"http://{host}:{port}/api"
        self.session = None
    
    async def __aenter__(self):
        """Async context manager entry."""
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self.session:
            await self.session.close()
    
    async def is_daemon_running(self) -> bool:
        """Check if daemon is running and accessible."""
        try:
            async with self.session.get(
                f"{self.base_url}/status", 
                timeout=aiohttp.ClientTimeout(total=2)
            ) as resp:
                return resp.status == 200
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return False
    
    async def get_status(self) -> Dict[str, Any]:
        """Get daemon status."""
        async with self.session.get(f"{self.base_url}/status") as resp:
            resp.raise_for_status()
            return await resp.json()
    
    async def list_nodes(self) -> List[Dict[str, Any]]:
        """List all registered nodes."""
        async with self.session.get(f"{self.base_url}/nodes") as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data["nodes"]
    
    async def get_node(self, node_id: str) -> Dict[str, Any]:
        """Get specific node details."""
        async with self.session.get(f"{self.base_url}/nodes/{node_id}") as resp:
            resp.raise_for_status()
            return await resp.json()
    
    async def get_node_inputs(self, node_id: str) -> Dict[str, Any]:
        """Get node input specifications."""
        async with self.session.get(f"{self.base_url}/nodes/{node_id}/inputs") as resp:
            resp.raise_for_status()
            return await resp.json()
    
    async def get_node_file_inputs(self, node_id: str) -> List[Dict[str, Any]]:
        """Get node file inputs."""
        async with self.session.get(f"{self.base_url}/nodes/{node_id}/file-inputs") as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data["file_inputs"]
    
    async def get_watched_files(self, node_id: Optional[str] = None) -> Dict[str, Any]:
        """Get watched files."""
        if node_id:
            url = f"{self.base_url}/watched-files/{node_id}"
        else:
            url = f"{self.base_url}/watched-files"
        
        async with self.session.get(url) as resp:
            resp.raise_for_status()
            return await resp.json()
    
    async def register_node(self, config_path: Union[str, Path]) -> str:
        """Register a new node."""
        data = {"config_path": str(config_path)}
        async with self.session.post(f"{self.base_url}/nodes", json=data) as resp:
            resp.raise_for_status()
            result = await resp.json()
            return result["node_id"]
    
    async def unregister_node(self, node_id: str) -> None:
        """Unregister a node."""
        async with self.session.delete(f"{self.base_url}/nodes/{node_id}") as resp:
            resp.raise_for_status()
    
    async def create_instance(
        self, 
        node_id: str, 
        output_path: Union[str, Path], 
        input_values: Dict[str, Any]
    ) -> str:
        """Create a new node instance."""
        data = {
            "output_path": str(output_path),
            "input_values": input_values
        }
        async with self.session.post(f"{self.base_url}/nodes/{node_id}/instances", json=data) as resp:
            resp.raise_for_status()
            result = await resp.json()
            return result["instance_id"]
    
    async def list_instances(self, node_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List node instances."""
        url = f"{self.base_url}/instances"
        if node_id:
            url += f"?node_id={node_id}"
        
        async with self.session.get(url) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data["instances"]
    
    async def rebuild_node(self, node_id: str) -> None:
        """Force rebuild a node's instances."""
        async with self.session.post(f"{self.base_url}/nodes/{node_id}/rebuild") as resp:
            resp.raise_for_status()
    
    async def get_dependency_graph(self, node_id: Optional[str] = None) -> Dict[str, Any]:
        """Get dependency graph."""
        url = f"{self.base_url}/graph"
        if node_id:
            url += f"?node_id={node_id}"
        
        async with self.session.get(url) as resp:
            resp.raise_for_status()
            return await resp.json()
    
    async def trigger_webhook(self, node_id: str, data: Dict[str, Any]) -> None:
        """Trigger a webhook node."""
        async with self.session.post(f"{self.base_url}/webhooks/{node_id}", json=data) as resp:
            resp.raise_for_status()
    
    async def get_node_logs(self, node_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Get node execution logs."""
        url = f"{self.base_url}/nodes/{node_id}/logs?limit={limit}"
        async with self.session.get(url) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data["logs"]


# Legacy compatibility with DaemonClient
DaemonClient = LivingTemplatesClient 