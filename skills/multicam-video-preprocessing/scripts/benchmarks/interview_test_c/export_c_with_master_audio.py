#!/usr/bin/env python3
"""Compatibility entrypoint for Test C; use measured Stage 1 metadata.

The original one-interview exporter is preserved in Git at 39bc03b. This entry
now delegates to the portable exporter, without fixed files, people, fps or offset.
Run with --dir OUTPUT_DIR --strict-edl --lang zh-TW after Stage 1 and Stage 2.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from export_fcp7_xml import main

if __name__ == "__main__":
    main()
