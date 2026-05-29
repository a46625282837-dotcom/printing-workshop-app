#!/usr/bin/env python
"""Run the backend server."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.app import run_server

if __name__ == "__main__":
    run_server(debug=True)
