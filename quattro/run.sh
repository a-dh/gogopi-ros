#!/bin/bash
# Run the RViz container on quattro.
# Requires: Docker, X11 display available, gopiros2 running gprctl (its
# robot_state_publisher provides /robot_description + TF).
# Discovery: Tailscale running → unicast peer profile (works off-LAN);
#            otherwise → plain multicast discovery (same LAN only).
# Build first: docker build -t gopiros2-rviz .

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RVIZ_CONF="$SCRIPT_DIR/rviz2_conf/default.rviz"
FASTDDS_CONF="$SCRIPT_DIR/fastdds_tailscale.xml"

if tailscale status >/dev/null 2>&1; then
    echo "Tailscale up — discovery via unicast peer (fastdds_tailscale.xml)"
    DDS_ARGS=(--volume "$FASTDDS_CONF":/config/fastdds_tailscale.xml:ro
              --env RMW_FASTRTPS_DEFAULT_PROFILES_FILE=/config/fastdds_tailscale.xml)
else
    echo "Tailscale down — LAN multicast discovery"
    DDS_ARGS=(--env RMW_FASTRTPS_DEFAULT_PROFILES_FILE=)
fi

xhost +local:docker 2>/dev/null || true

docker run --rm -it \
  --network host \
  --env DISPLAY="${DISPLAY:-:0}" \
  --env XDG_RUNTIME_DIR=/tmp/runtime-root \
  --env LIBGL_ALWAYS_SOFTWARE=1 \
  --env MESA_GL_VERSION_OVERRIDE=3.3 \
  --volume /tmp/.X11-unix:/tmp/.X11-unix \
  --volume "$RVIZ_CONF":/config/default.rviz \
  "${DDS_ARGS[@]}" \
  gopiros2-rviz
