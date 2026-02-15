#!/bin/bash
# Script to configure and run the benchmark_agent

# Exit on error
set -e

echo "Changing to project root directory..."
cd "$(dirname "$0")"

# Activate virtual environment if it exists, else create one and install requirements
if [ ! -d ".venv" ]; then
    echo "Creating new virtual environment..."
    python -m venv .venv
    echo "Activating virtual environment and installing requirements..."
    source .venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
else
    echo "Activating existing virtual environment..."
    source .venv/bin/activate
fi

echo "Running benchmark_agent..."
python -m agent.benchmark_agent
