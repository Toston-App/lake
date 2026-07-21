#!/usr/bin/env python3
"""
Simple HTTP server for the Investment Dashboard.

Usage:
    python serve.py
    
Then open http://localhost:3000 in your browser.
"""
import http.server
import socketserver
import os

PORT = 3000
DIRECTORY = os.path.dirname(os.path.abspath(__file__))


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)
    
    def end_headers(self):
        # Add CORS headers for development
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
        super().end_headers()


if __name__ == "__main__":
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"""
╔═══════════════════════════════════════════════════════════╗
║           Investment Dashboard                            ║
╠═══════════════════════════════════════════════════════════╣
║  Server running at: http://localhost:{PORT}                 ║
║                                                           ║
║  Make sure your backend API is running at:                ║
║  http://localhost:8000                                    ║
║                                                           ║
║  Press Ctrl+C to stop                                     ║
╚═══════════════════════════════════════════════════════════╝
        """)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped.")

