# plugins/pip.py
"""引擎包管理
POST /api/pip
action: install | list | uninstall"""

import subprocess
import sys

async def execute(envelop, agent):
    action = envelop.payload.get("action", "list")
    
    if action == "install":
        packages = envelop.payload.get("packages", [])
        if isinstance(packages, str):
            packages = [packages]
        
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install"] + packages,
                capture_output=True, text=True, timeout=120
            )
            envelop.payload = {
                "ok": result.returncode == 0,
                "stdout": result.stdout[-500:],
                "stderr": result.stderr[-200:],
                "packages": packages
            }
        except Exception as e:
            envelop.payload = {"ok": False, "error": str(e)}
    
    elif action == "list":
        result = subprocess.run(
            [sys.executable, "-m", "pip", "list", "--format=json"],
            capture_output=True, text=True, timeout=10
        )
        import json
        envelop.payload = {"ok": True, "packages": json.loads(result.stdout)}
    
    elif action == "uninstall":
        packages = envelop.payload.get("packages", [])
        if isinstance(packages, str):
            packages = [packages]
        result = subprocess.run(
            [sys.executable, "-m", "pip", "uninstall", "-y"] + packages,
            capture_output=True, text=True, timeout=30
        )
        envelop.payload = {"ok": result.returncode == 0, "packages": packages}
    
    return envelop