#!/bin/zsh
# Compatibility shortcut; uses the same incremental update as UPDATE_MATERIALS.
exec /bin/zsh "${0:A:h}/SYNC_AND_EXPORT.command" "$@"
