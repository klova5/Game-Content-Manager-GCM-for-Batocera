#!/bin/bash
# GCM 4.0 developer preview uninstaller. See README.md.
cd "$(dirname "$0")" && exec python3 -B payload/gcm_install.py uninstall "$@"
