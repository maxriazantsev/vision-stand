#!/usr/bin/env bash
# Machine-independent environment for this workspace. Source this instead of
# (or in addition to) sourcing ROS directly, on every machine: VM, Pi, any
# future reimage. Keeps config in the repo instead of hand-edited dotfiles.
#
#   source scripts/env.sh

source /opt/ros/jazzy/setup.bash

# Isolates this project's ROS graph from anything else on the LAN. Must match
# across every machine that needs to see these nodes/topics.
export ROS_DOMAIN_ID=42
