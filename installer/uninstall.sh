#!/bin/bash
# GCM 4.1 uninstaller. See README.md.
cd "$(dirname "$0")" && exec python3 -B payload/gcm_install.py uninstall "$@"
