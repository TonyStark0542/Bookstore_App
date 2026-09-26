#!/bin/bash
# ==============================================================================
# Project 2 Pre-Flight Environment Validation Utility
# ==============================================================================
set -e # Trap errors instantly. If any command fails, abort execution.

echo "🚀 [PRE-FLIGHT] Initializing environment validation sequence..."

# 1. Validate Docker daemon availability
if ! command -v docker &> /dev/null; then
    echo "❌ CRITICAL: Docker CLI binary is missing from the system path map." >&2
    exit 1
fi

# 2. Check engine socket communication health
if ! docker info &> /dev/null; then
    echo "❌ CRITICAL: Docker daemon engine is not active or socket is unresponsive." >&2
    exit 1
fi
echo "✅ Docker Engine status verified: Active"

# 3. Verify Docker Compose utility integration
if ! docker compose version &> /dev/null; then
    echo "❌ CRITICAL: Docker Compose plugin is missing or unlinked." >&2
    exit 1
fi
echo "✅ Docker Compose plugin verified: Present"

# 4. Enforce mandatory host environment token variable parameters
if [ -z "$GEMINI_API_KEY" ]; then
    echo "❌ CRITICAL: GEMINI_API_KEY is not set in the host environment memory." >&2
    exit 1
fi
echo "✅ Mandatory credential vectors verified: GEMINI_API_KEY is present."

# 5. Verify local dependency files exist before building image layers
echo "📁 Checking microservice file boundaries..."
if [ ! -f "./frontend/requirements.txt" ] || [ ! -f "./backend/requirements.txt" ]; then
    echo "❌ CRITICAL: One or more microservice requirements.txt files are missing!" >&2
    exit 1
fi
echo "✅ Microservice dependency blueprints verified: Present"

echo "🎉 [PRE-FLIGHT] Environment validation successful. Deployment runway clear."
