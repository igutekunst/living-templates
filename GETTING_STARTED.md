# Getting Started with Living Templates

This guide will help you get up and running with Living Templates quickly.

## Installation

### From Source (Development)

1. **Clone the repository:**
```bash
git clone https://github.com/igutekunst/living-templates.git
cd living-templates
```

2. **Install in development mode:**
```bash
pip install -e ".[dev]"
```

### From PyPI (When Published)

```bash
pip install living-templates
```

## Quick Start

### 1. Validate the Installation

Check that the CLI is working:

```bash
living-templates --help
lt --help
```

### 2. Try the Hello World Example

First, validate the example template:

```bash
living-templates validate examples/hello-world.yaml
```

Create your first living template:

```bash
lt -s examples/hello-world.yaml ./hello.txt --input name="Isaac"
```

This will:
- Register the hello-world template
- Create an instance with your name
- Generate `hello.txt` with the greeting
- Set up file watching so changes to the template automatically update the output

### 3. Check the Generated File

```bash
cat hello.txt
```

You should see:
```
Hello, Isaac!

Generated at: 2024-01-15 10:30:45
Environment: isaac
```

### 4. Test Live Updates

Try editing the `examples/hello-world.yaml` template (change the greeting or add some text), and watch `hello.txt` automatically update!

### 5. More Complex Example

Try the configuration template example:

```bash
lt -s examples/config-template.yaml ./app-config.json \
  --input app_name="MyApp" \
  --input port=3000 \
  --input debug=true \
  --input features='["auth", "logging", "metrics"]' \
  --input database_config="examples/database.json"
```

This demonstrates:
- Multiple input types (string, integer, boolean, array)
- File inputs (reads database.json)
- JSON output generation
- Complex template logic

## Core Concepts

### Templates with Frontmatter

Living Templates uses YAML frontmatter to define template configuration:

```yaml
---
schema_version: "1.0"
node_type: template
template_engine: jinja2
inputs:
  name:
    type: string
    description: "Your name"
    default: "World"
outputs:
  - greeting.txt
---
Hello, {{ name }}!
Generated at {{ now() }}
```

### Input Types

- **string**: Text values
- **integer**: Whole numbers
- **number**: Decimal numbers
- **boolean**: true/false values
- **array**: Lists (use JSON format in CLI)
- **object**: Objects (use JSON format in CLI)
- **file**: File paths (content is read and made available)

### Built-in Template Functions

- `{{ now() }}` - Current timestamp
- `{{ now("%Y-%m-%d") }}` - Formatted timestamp
- `{{ env("VAR_NAME") }}` - Environment variable
- `{{ "file.txt" | read_file }}` - Read file contents

## CLI Commands

### Template Management

```bash
# Register a template
living-templates register my-template.yaml

# List registered templates
living-templates list-nodes

# Validate a template
living-templates validate my-template.yaml

# Unregister a template
living-templates unregister <node-id>
```

### Creating Template Instances

```bash
# Basic usage
lt -s template.yaml output.txt --input key=value

# With config file
lt -s template.yaml output.txt --config inputs.yaml

# Dry run (see what would be generated)
lt -s template.yaml output.txt --input name="test" --dry-run
```

### Daemon Management

```bash
# Start daemon (for file watching)
living-templates daemon start

# Check daemon status
living-templates daemon status

# Stop daemon
living-templates daemon stop
```

## File Structure

Living Templates creates a directory structure in `~/.living-templates/`:

```
~/.living-templates/
├── db.sqlite              # Metadata database
├── store/                 # Content-addressed storage
│   ├── abc123def...       # Generated content files
│   └── def456ghi...
└── daemon.pid            # Daemon process ID
```

Generated files are symlinks pointing to the content store, enabling:
- Deduplication (identical content shares storage)
- Atomic updates (symlinks are updated atomically)
- Easy cleanup and management

## Next Steps

1. **Create your own templates** - Start with simple examples and gradually add complexity
2. **Explore file watching** - Start the daemon and see live updates in action
3. **Try complex inputs** - Use JSON/YAML config files for complex data structures
4. **Build workflows** - Chain templates together for complex build processes

## Troubleshooting

### Common Issues

**Template validation fails:**
- Check YAML frontmatter syntax
- Ensure all required fields are present
- Validate input type specifications

**File watching not working:**
- Make sure the daemon is running: `living-templates daemon status`
- Check that input files exist and are readable
- Verify file paths are absolute or relative to current directory

**Permission errors:**
- Ensure you have write permissions to the output directory
- Check that `~/.living-templates/` is writable

### Getting Help

- Check the full documentation in `README.md` and `architecture.md`
- Use `--help` with any command for detailed usage
- File issues on GitHub for bugs or feature requests

## Examples Directory

The `examples/` directory contains:

- `hello-world.yaml` - Simple greeting template
- `config-template.yaml` - Complex configuration generator
- `database.json` - Sample data file for templates

Feel free to modify these examples or create your own! 