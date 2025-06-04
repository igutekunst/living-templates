#!/usr/bin/env python3
"""
---
schema_version: "1.0"
node_type: program
inputs:
  input_file:
    type: file
    description: "CSV file to process"
    required: true
  threshold:
    type: number
    description: "Minimum score threshold"
    default: 0.5
  output_format:
    type: string
    description: "Output format"
    default: "json"
outputs:
  - processed_data.json
  - summary.txt
script_path: "examples/program-example.py"
environment:
  PYTHONPATH: "."
timeout: 120
---
"""

import json
import sys
import os
from pathlib import Path

def main():
    """Process CSV data based on threshold."""
    if len(sys.argv) < 4:
        print("Usage: program-example.py <input_file> <threshold> <output_format>")
        sys.exit(1)
    
    input_file = sys.argv[1]
    threshold = float(sys.argv[2])
    output_format = sys.argv[3]
    
    # Get output directory from environment
    output_dir = Path(os.environ.get('LT_OUTPUT_DIR', '.'))
    
    # Simple CSV processing (for demo purposes)
    try:
        # Read input file
        with open(input_file, 'r') as f:
            lines = f.readlines()
        
        # Parse CSV (simple implementation)
        header = lines[0].strip().split(',')
        data = []
        for line in lines[1:]:
            values = line.strip().split(',')
            if len(values) == len(header):
                row = dict(zip(header, values))
                # Assume there's a 'score' column
                if 'score' in row:
                    try:
                        row['score'] = float(row['score'])
                        if row['score'] >= threshold:
                            data.append(row)
                    except ValueError:
                        continue
        
        # Write processed data
        if output_format.lower() == 'json':
            output_file = output_dir / 'processed_data.json'
            with open(output_file, 'w') as f:
                json.dump(data, f, indent=2)
        
        # Write summary
        summary_file = output_dir / 'summary.txt'
        with open(summary_file, 'w') as f:
            f.write(f"Processing Summary\n")
            f.write(f"==================\n")
            f.write(f"Input file: {input_file}\n")
            f.write(f"Threshold: {threshold}\n")
            f.write(f"Total rows processed: {len(lines) - 1}\n")
            f.write(f"Rows above threshold: {len(data)}\n")
            f.write(f"Filter rate: {len(data)/(len(lines)-1)*100:.1f}%\n")
        
        print(f"Processed {len(lines)-1} rows, {len(data)} passed threshold {threshold}")
        
    except Exception as e:
        print(f"Error processing file: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main() 