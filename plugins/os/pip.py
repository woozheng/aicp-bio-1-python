# plugins/pip.py
"""引擎包管理
POST /api/pip
action: install | list | uninstall
"""

import asyncio
import json
import sys


async def execute(envelop, agent):
    action = envelop.payload.get("action", "list")

    # ============================================================
    # install
    # ============================================================
    if action == "install":
        packages = envelop.payload.get("packages", [])
        if isinstance(packages, str):
            packages = [packages]

        if not packages:
            envelop.payload = {"ok": False, "error": "缺少 packages 参数"}
            return envelop

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pip", "install", *packages,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=120
            )

            envelop.payload = {
                "ok": proc.returncode == 0,
                "stdout": stdout.decode("utf-8", errors="replace")[-500:],
                "stderr": stderr.decode("utf-8", errors="replace")[-200:],
                "packages": packages,
            }
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            envelop.payload = {
                "ok": False,
                "error": "pip install 超时（120秒）",
                "packages": packages,
            }
        except Exception as e:
            envelop.payload = {"ok": False, "error": str(e), "packages": packages}

    # ============================================================
    # list
    # ============================================================
    elif action == "list":
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pip", "list", "--format=json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=10
            )

            if proc.returncode != 0:
                envelop.payload = {
                    "ok": False,
                    "error": stderr.decode("utf-8", errors="replace")[-200:],
                }
                return envelop

            envelop.payload = {
                "ok": True,
                "packages": json.loads(stdout.decode("utf-8")),
            }
        except asyncio.TimeoutError:
            envelop.payload = {"ok": False, "error": "pip list 超时"}
        except Exception as e:
            envelop.payload = {"ok": False, "error": str(e)}

    # ============================================================
    # uninstall
    # ============================================================
    elif action == "uninstall":
        packages = envelop.payload.get("packages", [])
        if isinstance(packages, str):
            packages = [packages]

        if not packages:
            envelop.payload = {"ok": False, "error": "缺少 packages 参数"}
            return envelop

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pip", "uninstall", "-y", *packages,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=30
            )

            envelop.payload = {
                "ok": proc.returncode == 0,
                "packages": packages,
                "stdout": stdout.decode("utf-8", errors="replace")[-500:],
                "stderr": stderr.decode("utf-8", errors="replace")[-200:],
            }
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            envelop.payload = {"ok": False, "error": "pip uninstall 超时"}
        except Exception as e:
            envelop.payload = {"ok": False, "error": str(e)}

    # ============================================================
    # 未知 action
    # ============================================================
    else:
        envelop.payload = {"ok": False, "error": f"未知 action: {action}"}

    return envelop


def help():
    return {
        "route": "os/pip",
        "description": "Python 包管理（pip install / list / uninstall）",
        "input": {
            "action": "install | list | uninstall",
            "packages": "list[str] 或 str（install / uninstall 必填）",
        },
        "output": {
            "ok": "boolean",
            "packages": "list",
            "stdout": "string（截断到 500 字）",
            "stderr": "string（截断到 200 字）",
            "error": "string（失败时）",
        },
    }
