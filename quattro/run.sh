#!/bin/bash
# Run the RViz container on quattro.
# Requires: Docker, Tailscale active, X11 display available.
# Build first: docker build -t gopiros2-rviz .

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Allow X11 connections from Docker
xhost +local:docker 2>/dev/null || true

docker run --rm \
  --network host \
  -e DISPLAY="${DISPLAY:-:0}" \
  -v /tmp/.X11-unix:/tmp/.X11-unix \
  -v "${HOME}/.Xauthority:/root/.Xauthority:ro" \
  gopiros2-rviz
