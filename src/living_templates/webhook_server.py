"""Standalone webhook server for Living Templates."""

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import click
from aiohttp import web

from .client import LivingTemplatesClient


class WebhookServer:
    """Standalone webhook server that communicates with Living Templates daemon."""
    
    def __init__(self, port: int = 9000, daemon_host: str = "localhost", daemon_port: int = 8765):
        """Initialize webhook server.
        
        Args:
            port: Port to run webhook server on
            daemon_host: Host of Living Templates daemon
            daemon_port: Port of Living Templates daemon
        """
        self.port = port
        self.daemon_host = daemon_host
        self.daemon_port = daemon_port
        self.app = web.Application()
        self.runner = None
        self.site = None
        
        # Setup routes
        self.app.router.add_post('/webhook/{node_id}', self._handle_webhook)
        self.app.router.add_get('/health', self._health_check)
        self.app.router.add_get('/webhooks', self._list_webhooks)
    
    async def start(self) -> None:
        """Start the webhook server."""
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, 'localhost', self.port)
        await self.site.start()
        print(f"Webhook server started on http://localhost:{self.port}")
    
    async def stop(self) -> None:
        """Stop the webhook server."""
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
    
    async def _handle_webhook(self, request: web.Request) -> web.Response:
        """Handle incoming webhook requests."""
        node_id = request.match_info['node_id']
        
        try:
            # Parse request data
            content_type = request.headers.get('Content-Type', '')
            if content_type.startswith('application/json'):
                data = await request.json()
            else:
                # For non-JSON, store as raw text
                text = await request.text()
                data = {"raw_body": text}
            
            # Add metadata
            webhook_data = {
                "data": data,
                "headers": dict(request.headers),
                "method": request.method,
                "path": request.path,
                "query": dict(request.query),
                "remote": request.remote,
                "timestamp": str(asyncio.get_event_loop().time())
            }
            
            # Forward to daemon
            async with LivingTemplatesClient(self.daemon_host, self.daemon_port) as client:
                if not await client.is_daemon_running():
                    return web.json_response(
                        {"error": "Living Templates daemon is not running"}, 
                        status=503
                    )
                
                await client.trigger_webhook(node_id, webhook_data)
            
            return web.json_response({"status": "success", "node_id": node_id})
            
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    
    async def _health_check(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        # Check if daemon is reachable
        try:
            async with LivingTemplatesClient(self.daemon_host, self.daemon_port) as client:
                daemon_running = await client.is_daemon_running()
            
            return web.json_response({
                "status": "healthy",
                "daemon_connected": daemon_running,
                "webhook_port": self.port
            })
        except Exception as e:
            return web.json_response({
                "status": "unhealthy",
                "daemon_connected": False,
                "error": str(e),
                "webhook_port": self.port
            }, status=503)
    
    async def _list_webhooks(self, request: web.Request) -> web.Response:
        """List available webhook nodes."""
        try:
            async with LivingTemplatesClient(self.daemon_host, self.daemon_port) as client:
                if not await client.is_daemon_running():
                    return web.json_response(
                        {"error": "Living Templates daemon is not running"}, 
                        status=503
                    )
                
                nodes = await client.list_nodes()
                webhook_nodes = [
                    node for node in nodes 
                    if node.get("node_type") == "webhook"
                ]
                
                return web.json_response({
                    "webhook_nodes": webhook_nodes,
                    "base_url": f"http://localhost:{self.port}/webhook"
                })
                
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)


@click.command()
@click.option('--port', default=9000, help='Port to run webhook server on')
@click.option('--daemon-host', default='localhost', help='Living Templates daemon host')
@click.option('--daemon-port', default=8765, help='Living Templates daemon port')
def main(port: int, daemon_host: str, daemon_port: int):
    """Run the Living Templates webhook server."""
    
    async def run_server():
        server = WebhookServer(port, daemon_host, daemon_port)
        
        try:
            await server.start()
            print(f"Webhook server running on port {port}")
            print(f"Daemon connection: {daemon_host}:{daemon_port}")
            print("Press Ctrl+C to stop")
            
            # Keep server running
            while True:
                await asyncio.sleep(1)
                
        except KeyboardInterrupt:
            print("\nShutting down webhook server...")
        finally:
            await server.stop()
    
    asyncio.run(run_server())


if __name__ == "__main__":
    main() 