#!/usr/bin/env python3
"""Adicot Load Calc — the easy way in.

    python3 app.py

Opens a browser page with two drop zones: your plans PDF, and a JSON file.
  - First time on a project: drop the JSON from the Adicot DM Setup site.
  - Picking back up: drop your saved rooms_edited.json instead — the app
    tells the two apart automatically, so it's the same drop zone either way.

Ctrl-C the terminal when you're done (stops the local server).
"""
import loadcalc

if __name__ == "__main__":
    loadcalc.app_cmd()
