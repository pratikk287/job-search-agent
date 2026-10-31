"""Start the Job Search Agent.

    python run.py            start the app and open it in your browser (the page opens only once the app is ready)
    python run.py scan       run one discovery scan (for Windows Task Scheduler)
    python run.py report     write this week's HTML report to data/reports/
"""
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

HOST, PORT = "127.0.0.1", 8765
URL = f"http://{HOST}:{PORT}"


def fail(msg):
    """Show the problem on screen (the launcher window is minimized) and in the log."""
    print(msg)
    try:
        import ctypes, os
        os.makedirs("data", exist_ok=True)
        open("data/launcher.log", "a", encoding="utf-8").write(time.strftime("%Y-%m-%d %H:%M:%S") + " ERROR " + msg + chr(10))
        ctypes.windll.user32.MessageBoxW(0, msg, "Job Search Agent", 0x10)
    except Exception:
        pass
    sys.exit(1)


def free_port():
    """Close whatever Python process holds our port if it is this app and not answering. True if the port is now free."""
    import subprocess
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
            f"Get-NetTCPConnection -LocalPort {PORT} -State Listen | ForEach-Object {{ $p = Get-CimInstance Win32_Process -Filter \"ProcessId=$($_.OwningProcess)\"; \"$($p.ProcessId)|$($p.CommandLine)\" }}"],
            capture_output=True, text=True, timeout=20).stdout
        for line in out.splitlines():
            pid, _, cmd = line.partition("|")
            if pid.strip().isdigit() and ("run.py" in cmd or "jobagent" in cmd or "uvicorn" in cmd):
                subprocess.run(["taskkill", "/F", "/PID", pid.strip()], capture_output=True, timeout=20)
        time.sleep(1.5)
    except Exception:
        pass
    return not port_in_use()


def open_page():
    """Open the dashboard in the default browser; Windows' own opener first, then the webbrowser module."""
    try:
        import os
        os.startfile(URL)
    except Exception:
        webbrowser.open(URL)
    try:
        import os
        os.makedirs("data", exist_ok=True)
        with open("data/launcher.log", "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " opened " + URL + chr(10))
    except Exception:
        pass


def port_in_use():
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex((HOST, PORT)) == 0


def app_ready():
    """True when the Job Search Agent itself (not some other program) answers on the port."""
    try:
        with urllib.request.urlopen(f"{URL}/api/health", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def open_when_ready(timeout=90):
    """Open the browser only after the app answers, so the page never shows 'site can't be reached'."""
    start = time.time()
    while time.time() - start < timeout:
        if app_ready():
            print(f"Ready after {time.time() - start:.1f}s. Opening {URL}")
            open_page()
            return
        time.sleep(0.4)
    print("The app did not become ready in time. Check the messages above for an error.")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if cmd == "scan":
        from jobagent import scan
        s = scan.run_scan()
        print(f"{s['new']} new, {s['updated']} refreshed, rejected: {s['rejected']}, errors: {len(s['errors'])}")
        for e in s["errors"]:
            print("  ", e)
    elif cmd == "report":
        from jobagent import report
        print(report.export_html())
    else:
        if port_in_use():
            if app_ready():                     # already running: just show it
                print("The app is already running. Opening it.")
                if "--no-browser" not in sys.argv:
                    open_page()
                return
            if free_port():                      # a stuck copy of this app held the port: it was closed, carry on
                time.sleep(1)
            else:
                fail(f"Port {PORT} is being used by another program, so the app cannot start.\n"
                     "Restart the computer, then open the shortcut again.")
        if "--no-browser" not in sys.argv:
            threading.Thread(target=open_when_ready, daemon=True).start()
        try:
            import uvicorn
            uvicorn.run("jobagent.server:app", host=HOST, port=PORT, log_level="warning")
        except Exception as e:
            fail(f"The app could not start: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
