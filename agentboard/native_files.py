"""Windows file dialog fallback, isolated from the server's worker threads."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from threading import Lock

from .schema import BusinessError

_dialog = Lock()


def suggested_name(name):
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]',"_",name).strip(" .")[:80] or "AgentBoard"
    if stem.split(".")[0].upper() in {"CON","PRN","AUX","NUL",*("COM"+str(n) for n in range(1,10)),*("LPT"+str(n) for n in range(1,10))}:
        stem = "AgentBoard_"+stem
    return stem+".agentboard"


def choose_file(mode, name="AgentBoard.agentboard"):
    if os.name != "nt":
        raise BusinessError("FILE_DIALOG_UNAVAILABLE","请使用Chrome／Edge打开黑板，以选择文件与保存位置。")
    if not _dialog.acquire(blocking=False):
        raise BusinessError("FILE_DIALOG_BUSY","已有文件对话框打开，请先完成或取消。")
    try:
        process = subprocess.run([sys.executable,"-X","utf8","-m","agentboard.native_files",mode,name],
            capture_output=True,encoding="utf-8",timeout=300,cwd=str(Path(__file__).resolve().parent.parent),
            creationflags=subprocess.CREATE_NO_WINDOW)
        if process.returncode != 0:
            raise BusinessError("FILE_DIALOG_UNAVAILABLE","无法打开Windows文件对话框，请检查Python的tkinter组件，或使用Chrome／Edge。")
        result = json.loads(process.stdout)
        return Path(result["path"]) if result.get("path") else None
    except (OSError,ValueError,subprocess.TimeoutExpired):
        raise BusinessError("FILE_DIALOG_UNAVAILABLE","文件选择未完成，请重试或使用Chrome／Edge。")
    finally: _dialog.release()


def atomic_save(path, raw):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent,prefix=".agentboard-",suffix=".tmp",delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        if temporary is not None and temporary.exists(): temporary.unlink()


def main():
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk(); root.withdraw(); root.attributes("-topmost",True)
    try:
        options = {"parent":root,"filetypes":[("AgentBoard数据文件","*.agentboard"),("所有文件","*.*")]}
        if sys.argv[1] == "save":
            path = filedialog.asksaveasfilename(title="导出黑板",initialfile=sys.argv[2],defaultextension=".agentboard",**options)
        else:
            path = filedialog.askopenfilename(title="导入黑板",**options)
        print(json.dumps({"path":path},ensure_ascii=False))
    finally: root.destroy()


if __name__ == "__main__": main()
