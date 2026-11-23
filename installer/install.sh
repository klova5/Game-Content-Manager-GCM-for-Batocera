#!/bin/bash
# GCM 4.0 developer preview installer for Batocera 44 (x86_64). See README.md.
cd "$(dirname "$0")" && exec python3 -B payload/gcm_install.py install "$@"
