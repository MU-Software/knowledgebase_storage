#!/bin/sh
exec /usr/local/lib/duckdb/duckdb -no-init \
    -cmd ".highlight_results off" -cmd ".highlight_errors off" \
    -cmd "SET memory_limit='1GB'; SET threads=2; SET autoinstall_known_extensions=false; SET extension_directory='/opt/duckdb/extensions';" \
    "$@"
