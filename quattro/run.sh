#!/bin/bash
# Run the RViz container on quattro.
# Requires: Docker, Tailscale active, X11 display available.
# Build first: docker build -t gopiros2-rviz .

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RVIZ_CONF="$SCRIPT_DIR/rviz2_conf/default.rviz"

xhost +local:docker 2>/dev/null || true

docker run --rm -it \
  --network host \
  --env DISPLAY="${DISPLAY:-:0}" \
  --env XDG_RUNTIME_DIR=/tmp/runtime-root \
  --env LIBGL_ALWAYS_SOFTWARE=1 \
  --env MESA_GL_VERSION_OVERRIDE=3.3 \
  --volume /tmp/.X11-unix:/tmp/.X11-unix \
  --volume "$RVIZ_CONF":/config/default.rviz \
  gopiros2-rviz \
  bash -c "source /opt/ros/jazzy/setup.bash && rviz2 -d /config/default.rviz"
