"""Quick test: simulate serving login.html the way main.py does."""
import sys
sys.path.insert(0, 'backend')
import os
os.environ['SHEET_ID'] = '16nhZJyAiCX7xzBujieAF1AOas6bgh2-4X6ePQixWHJE'
os.environ['SHEET_GID'] = '0'

from pathlib import Path

# Simulate what main.py does
MAIN_PY = Path('backend/main.py').resolve()
print(f"main.py resolved: {MAIN_PY}")
print(f"parent: {MAIN_PY.parent}")
print(f"parent.parent: {MAIN_PY.parent.parent}")

FRONTEND_DIR = MAIN_PY.parent.parent
print(f"\nFRONTEND_DIR: {FRONTEND_DIR}")

login_path = FRONTEND_DIR / "login.html"
index_path = FRONTEND_DIR / "index.html"
styles_path = FRONTEND_DIR / "styles.css"

print(f"\nlogin.html exists: {login_path.exists()} -> {login_path}")
print(f"index.html exists: {index_path.exists()} -> {index_path}")
print(f"styles.css exists: {styles_path.exists()} -> {styles_path}")

# List all html files in FRONTEND_DIR
print(f"\nHTML files in {FRONTEND_DIR}:")
for f in FRONTEND_DIR.iterdir():
    if f.suffix in ('.html', '.css', '.js'):
        print(f"  {f.name} ({f.stat().st_size} bytes)")
