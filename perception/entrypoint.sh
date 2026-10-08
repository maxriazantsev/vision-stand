#!/bin/bash
# Sources the ROS + workspace overlay, then execs whatever CMD was passed.
set -e
source /opt/ros/jazzy/setup.bash
source /workspace/install/setup.bash
exec "$@"
