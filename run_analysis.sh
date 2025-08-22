#!/bin/bash

# GitHub Skills Analyzer - Setup and Run Script

echo "🚀 GitHub Skills Analyzer"
echo "=========================="

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is required but not installed."
    exit 1
fi

# Install dependencies if needed
echo "📦 Installing dependencies..."
pip3 install -r requirements.txt

# Check for OpenAI API key
if [ -z "$OPENAI_API_KEY" ]; then
    echo "🔑 OpenAI API key required."
    read -p "Please enter your OpenAI API key: " api_key
    export OPENAI_API_KEY="$api_key"
fi

# Run the analysis
echo "🔍 Starting GitHub analysis for user: dang-w"
cd src
python3 github_skills_analyzer.py

echo "✅ Analysis complete! Check the generated reports."