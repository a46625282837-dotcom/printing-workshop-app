import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from backend.app import run_server

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true")
    run_server(host="0.0.0.0", port=port, debug=debug)
