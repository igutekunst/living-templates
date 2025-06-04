"""Program executor for Living Templates."""

import asyncio
import json
import os
import shlex
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import ExecutionLog, LogLevel, NodeConfig, NodeInstance, TemplateNode


class ProgramExecutor:
    """Executes program nodes."""
    
    def __init__(self):
        """Initialize program executor."""
        self.active_processes: Dict[str, asyncio.subprocess.Process] = {}
    
    async def execute_program(
        self, 
        node: TemplateNode, 
        instance: NodeInstance,
        input_values: Dict[str, Any]
    ) -> Tuple[List[str], List[ExecutionLog]]:
        """Execute a program node.
        
        Args:
            node: The program node
            instance: The instance to execute
            input_values: Resolved input values
            
        Returns:
            Tuple of (output_files, execution_logs)
        """
        logs = []
        execution_id = str(uuid.uuid4())
        
        logs.append(ExecutionLog(
            id=str(uuid.uuid4()),
            node_id=node.id,
            instance_id=instance.id,
            level=LogLevel.INFO,
            message=f"Starting program execution: {execution_id}",
            details={"input_values": input_values}
        ))
        
        try:
            # Prepare execution environment
            env = os.environ.copy()
            env.update(node.config.environment)
            
            # Add input values to environment with LT_ prefix
            for key, value in input_values.items():
                env_key = f"LT_{key.upper()}"
                if isinstance(value, (dict, list)):
                    env[env_key] = json.dumps(value)
                else:
                    env[env_key] = str(value)
            
            # Determine working directory
            work_dir = Path(node.config.working_directory) if node.config.working_directory else Path.cwd()
            work_dir = work_dir.resolve()
            
            # Create temporary output directory
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                env["LT_OUTPUT_DIR"] = str(temp_path)
                
                # Execute the program
                if node.config.script_path:
                    # Execute script file
                    script_path = Path(node.config.script_path)
                    if not script_path.is_absolute():
                        script_path = work_dir / script_path
                    
                    # Make script executable
                    script_path.chmod(script_path.stat().st_mode | 0o755)
                    
                    # Build command args with input values
                    cmd_args = [str(script_path)]
                    for key, value in input_values.items():
                        if isinstance(value, (dict, list)):
                            cmd_args.append(json.dumps(value))
                        else:
                            cmd_args.append(str(value))
                    
                    logs.append(ExecutionLog(
                        id=str(uuid.uuid4()),
                        node_id=node.id,
                        instance_id=instance.id,
                        level=LogLevel.DEBUG,
                        message=f"Executing script: {script_path}",
                        details={"args": cmd_args, "cwd": str(work_dir)}
                    ))
                    
                elif node.config.command:
                    # Execute command string
                    # Replace placeholders in command
                    command = node.config.command
                    for key, value in input_values.items():
                        placeholder = f"${{{key}}}"
                        if isinstance(value, (dict, list)):
                            replacement = json.dumps(value)
                        else:
                            replacement = str(value)
                        command = command.replace(placeholder, replacement)
                    
                    cmd_args = shlex.split(command)
                    
                    logs.append(ExecutionLog(
                        id=str(uuid.uuid4()),
                        node_id=node.id,
                        instance_id=instance.id,
                        level=LogLevel.DEBUG,
                        message=f"Executing command: {command}",
                        details={"args": cmd_args, "cwd": str(work_dir)}
                    ))
                else:
                    raise ValueError("Program node must have script_path or command")
                
                # Run the process
                process = await asyncio.create_subprocess_exec(
                    *cmd_args,
                    cwd=work_dir,
                    env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                
                self.active_processes[execution_id] = process
                
                try:
                    # Wait for completion with timeout
                    stdout, stderr = await asyncio.wait_for(
                        process.communicate(),
                        timeout=node.config.timeout
                    )
                    
                    return_code = process.returncode
                    
                    # Log output
                    if stdout:
                        logs.append(ExecutionLog(
                            id=str(uuid.uuid4()),
                            node_id=node.id,
                            instance_id=instance.id,
                            level=LogLevel.INFO,
                            message="Program stdout",
                            details={"output": stdout.decode('utf-8', errors='replace')}
                        ))
                    
                    if stderr:
                        level = LogLevel.ERROR if return_code != 0 else LogLevel.WARNING
                        logs.append(ExecutionLog(
                            id=str(uuid.uuid4()),
                            node_id=node.id,
                            instance_id=instance.id,
                            level=level,
                            message="Program stderr",
                            details={"output": stderr.decode('utf-8', errors='replace')}
                        ))
                    
                    if return_code != 0:
                        raise subprocess.CalledProcessError(return_code, cmd_args[0])
                    
                    # Collect output files
                    output_files = []
                    for output_name in node.config.outputs:
                        output_file = temp_path / output_name
                        if output_file.exists():
                            output_files.append(str(output_file))
                        else:
                            logs.append(ExecutionLog(
                                id=str(uuid.uuid4()),
                                node_id=node.id,
                                instance_id=instance.id,
                                level=LogLevel.WARNING,
                                message=f"Expected output file not found: {output_name}",
                                details={"expected_path": str(output_file)}
                            ))
                    
                    logs.append(ExecutionLog(
                        id=str(uuid.uuid4()),
                        node_id=node.id,
                        instance_id=instance.id,
                        level=LogLevel.INFO,
                        message=f"Program execution completed successfully",
                        details={
                            "return_code": return_code,
                            "output_files": output_files,
                            "execution_id": execution_id
                        }
                    ))
                    
                    return output_files, logs
                    
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
                    raise RuntimeError(f"Program execution timed out after {node.config.timeout} seconds")
                
                finally:
                    if execution_id in self.active_processes:
                        del self.active_processes[execution_id]
        
        except Exception as e:
            logs.append(ExecutionLog(
                id=str(uuid.uuid4()),
                node_id=node.id,
                instance_id=instance.id,
                level=LogLevel.ERROR,
                message=f"Program execution failed: {str(e)}",
                details={
                    "error_type": type(e).__name__,
                    "execution_id": execution_id
                }
            ))
            raise
    
    async def kill_process(self, execution_id: str) -> bool:
        """Kill a running process.
        
        Args:
            execution_id: ID of the execution to kill
            
        Returns:
            True if process was killed, False if not found
        """
        if execution_id in self.active_processes:
            process = self.active_processes[execution_id]
            process.kill()
            await process.wait()
            del self.active_processes[execution_id]
            return True
        return False
    
    def get_active_processes(self) -> List[str]:
        """Get list of active execution IDs."""
        return list(self.active_processes.keys()) 