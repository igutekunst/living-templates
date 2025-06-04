# Living Templates

A reactive file system that automatically updates generated files when their dependencies change. Think of it as "functional reactive programming for files" or a "living Makefile" that watches your filesystem and keeps everything in sync.

## Core Concept

Living Templates creates a dependency graph where files, templates, and computational processes automatically regenerate their outputs when inputs change. It's like having a build system that runs continuously in the background, ensuring your generated content is always up-to-date.

## Architecture Overview

### Node-Based System
- **Template Nodes**: Jinja2 templates that render to files
- **Program Nodes**: Scripts/commands that process inputs and generate outputs  
- **File Nodes**: Raw file dependencies that trigger updates
- **API Nodes**: External data sources (REST APIs, databases)
- **Environment Nodes**: System environment variables

### Centralized Store with Symlinks
Generated content is stored in a content-addressed store (similar to Nix), with symlinks pointing to the actual locations where files are needed:

```
~/.living-templates/
├── store/
│   ├── abc123def456.../     # Content hash directories
│   │   └── config.json      # Generated content
│   └── def456ghi789.../
│       └── processed.csv
├── db.sqlite               # Dependency graph and metadata
└── daemon.pid             # Daemon process info
```

### YAML Frontmatter Configuration
Any file can become a living template by adding YAML frontmatter that declares its inputs, outputs, and processing logic:

```yaml
---
schema_version: "1.0"
node_type: template
template_engine: jinja2
inputs:
  database_url:
    type: string
    description: "Database connection string"
  port:
    type: integer
    default: 8080
  features:
    type: array
    items:
      type: string
outputs:
  - config.json
dependencies:
  - ./base-config.yaml
  - @api-node.database_info
---
{
  "database": "{{ database_url }}",
  "server": {
    "port": {{ port }},
    "features": {{ features | tojson }}
  },
  "timestamp": "{{ now() }}"
}
```

## Usage Examples

### Basic Template Registration and Linking

1. **Create a template file** (`app-config.yaml`):
```yaml
---
schema_version: "1.0"
node_type: template
template_engine: jinja2
inputs:
  app_name:
    type: string
  debug_mode:
    type: boolean
    default: false
outputs:
  - config.json
---
{
  "name": "{{ app_name }}",
  "debug": {{ debug_mode | lower }},
  "generated_at": "{{ now() }}"
}
```

2. **Register the template**:
```bash
living-templates register app-config.yaml
```

3. **Create an instance with specific inputs**:
```bash
# Using CLI arguments
lt -s app-config.yaml /path/to/my-app/config.json \
  --input app_name="MyAwesomeApp" \
  --input debug_mode=true

# Or using a config file for complex inputs
lt -s app-config.yaml /path/to/my-app/config.json \
  --config my-app-inputs.yaml
```

### Program Node Example

```python
#!/usr/bin/env python3
"""
---
schema_version: "1.0"
node_type: program
inputs:
  data_file:
    type: file
    description: "CSV file to process"
  threshold:
    type: number
    default: 0.5
outputs:
  - processed_data.json
  - summary.txt
---
"""

import json
import pandas as pd
import sys

def main():
    # Read inputs from environment or args
    data_file = sys.argv[1]
    threshold = float(sys.argv[2])
    
    # Process data
    df = pd.read_csv(data_file)
    filtered = df[df['score'] > threshold]
    
    # Generate outputs
    with open('processed_data.json', 'w') as f:
        json.dump(filtered.to_dict('records'), f)
    
    with open('summary.txt', 'w') as f:
        f.write(f"Processed {len(df)} rows, {len(filtered)} passed threshold")

if __name__ == "__main__":
    main()
```

### Complex Dependency Chain

```yaml
# docs-generator.yaml
---
schema_version: "1.0"
node_type: template
template_engine: jinja2
inputs:
  api_spec:
    type: object
    source: "@swagger-parser.api_definition"
  version:
    type: string
    source: "@git-info.current_tag"
  contributors:
    type: array
    source: "@github-api.contributors"
outputs:
  - README.md
---
# {{ api_spec.info.title }} v{{ version }}

## API Documentation
{% for endpoint in api_spec.paths %}
### {{ endpoint.method.upper() }} {{ endpoint.path }}
{{ endpoint.description }}
{% endfor %}

## Contributors
{% for contributor in contributors %}
- {{ contributor.name }} ({{ contributor.commits }} commits)
{% endfor %}

*Generated on {{ now() }}*
```

## Advanced Features

### Concatenation Mode
Files can be configured to concatenate new content rather than replace:

```yaml
---
schema_version: "1.0"
node_type: template
template_engine: jinja2
output_mode: concatenate  # append, prepend, or replace (default)
inputs:
  log_entry:
    type: object
outputs:
  - application.log
---
{{ now() }} [{{ log_entry.level }}] {{ log_entry.message }}
```

This enables:
- **Log Aggregation**: Multiple sources appending to a single log file
- **Incremental Documentation**: Adding new sections as code changes
- **Data Collection**: Accumulating results from periodic processes

### Tail Mode
Monitor file changes and stream new content to dependent nodes:

```yaml
---
schema_version: "1.0"
node_type: tail
input_mode: tail  # Watch for new lines appended to file
inputs:
  source_file:
    type: file
    path: /var/log/application.log
outputs:
  - parsed_logs.json
transform: |
  # Python code to process each new line
  import json
  import re
  
  def process_line(line):
      match = re.match(r'(\d{4}-\d{2}-\d{2}) \[(\w+)\] (.+)', line)
      if match:
          return {
              "timestamp": match.group(1),
              "level": match.group(2),
              "message": match.group(3)
          }
      return None
---
```

## CLI Reference

### Core Commands

```bash
# Daemon Management
living-templates daemon start
living-templates daemon stop
living-templates daemon status
living-templates daemon restart

# Node Registration
living-templates register <config-file>
living-templates unregister <config-file>
living-templates list-nodes

# Instance Management (short form: lt)
lt -s <template> <output-path> [options]
lt --source <template> <output-path> [options]

# Options for lt command:
--input key=value          # Set input values
--config <config-file>     # Load inputs from YAML/JSON file
--force                    # Force regeneration
--dry-run                  # Show what would be generated

# Inspection and Debugging
living-templates status                    # Show all active instances
living-templates graph                     # Visualize dependency graph
living-templates graph --node <node-id>   # Show specific node dependencies
living-templates rebuild [node-id]        # Force rebuild specific node
living-templates logs [node-id]           # Show processing logs
living-templates validate <config-file>   # Validate configuration
```

### Input Configuration Files

For complex inputs, use YAML or JSON configuration files:

```yaml
# my-app-config.yaml
inputs:
  database:
    host: "localhost"
    port: 5432
    name: "myapp_prod"
  features:
    - "authentication"
    - "analytics"
    - "caching"
  scaling:
    min_instances: 2
    max_instances: 10
    target_cpu: 70
```

```bash
lt -s app-template.yaml /deploy/config.json --config my-app-config.yaml
```

## Schema Definition

### Meta-Schema
All frontmatter must conform to the living-templates meta-schema:

```yaml
schema_version: "1.0"  # Required
node_type: string      # Required: template, program, tail, etc.
inputs:               # Optional: input definitions
  <input_name>:
    type: string       # string, integer, number, boolean, array, object, file
    description: string
    default: any
    required: boolean
    source: string     # Reference to another node: "@node-id.output"
outputs:              # Required: list of output files
  - string
dependencies:         # Optional: explicit dependencies
  - string            # File paths or node references
template_engine: string  # For template nodes: jinja2, etc.
output_mode: string   # replace (default), append, prepend, concatenate
input_mode: string    # normal (default), tail
transform: string     # For program nodes: inline code or script path
```

### Input Types
- **string**: Text values
- **integer/number**: Numeric values  
- **boolean**: true/false values
- **array**: Lists of values
- **object**: Nested key-value structures
- **file**: File path references (triggers file watching)

### Node References
Reference outputs from other nodes using `@node-id.output_name` syntax:
- `@api-fetcher.user_data`
- `@config-parser.database_settings`
- `@git-info.current_commit`

## Implementation Notes

### Technology Stack
- **Python 3.8+** with asyncio for the daemon
- **SQLite** for dependency graph and metadata storage
- **Watchdog** for file system monitoring
- **Jinja2** for template rendering
- **Pydantic** for schema validation
- **Click** for CLI interface

### Performance Considerations
- Content-addressed storage prevents duplicate work
- Incremental updates only rebuild changed dependencies
- Configurable debouncing for rapid file changes
- Parallel processing for independent nodes

### Security
- Sandboxed execution for program nodes
- Input validation against declared schemas
- Configurable file system access restrictions
- Audit logging for all operations

## Use Cases

### Development Workflows
- **Configuration Management**: Generate environment-specific configs
- **Documentation**: Auto-update docs from code comments and API specs
- **Code Generation**: Create boilerplate from templates and schemas

### Data Processing
- **ETL Pipelines**: Transform data as sources update
- **Report Generation**: Create reports from live data sources
- **Log Processing**: Parse and aggregate log files in real-time

### System Administration
- **Config Deployment**: Push configuration changes across environments
- **Monitoring**: Generate dashboards from system metrics
- **Backup Orchestration**: Coordinate backup processes across services

## Getting Started

1. **Install living-templates**:
```bash
pip install living-templates
```

2. **Start the daemon**:
```bash
living-templates daemon start
```

3. **Create your first template** (see examples above)

4. **Register and link it**:
```bash
living-templates register my-template.yaml
lt -s my-template.yaml ./output.txt --input name="World"
```

5. **Watch it update automatically** when dependencies change!

## Contributing

This project is in active development. Key areas for contribution:
- Additional template engines
- New node types (database, API, etc.)
- Performance optimizations
- Security enhancements
- Documentation and examples

## License

MIT License - see LICENSE file for details. 