"""Server-owned trace program executed only inside the isolated Python Runner."""

DEBUG_SCRIPT = r'''
import contextlib
import io
import json
import runpy
import sys
import traceback

ROOT = "/workspace/student/"
MAX_EVENTS = 72
MAX_STREAM = 1200
events = []
truncated = False
trace_bytes = 0

class BoundedText(io.StringIO):
    def __init__(self):
        super().__init__()
        self.truncated = False

    def write(self, value):
        available = MAX_STREAM - len(self.getvalue())
        if available > 0:
            super().write(value[:available])
        if len(value) > available:
            self.truncated = True
        return len(value)

def display(value, depth=0):
    try:
        if value is None or type(value) in (bool, int, float):
            return str(value)[:64]
        if type(value) is str:
            return repr(value[:48])[:64]
        if depth < 1 and type(value) in (list, tuple):
            parts = [display(item, 1) for item in value[:6]]
            suffix = ", …" if len(value) > 6 else ""
            return ("[" if type(value) is list else "(") + ", ".join(parts) + suffix + ("]" if type(value) is list else ")")
        if depth < 1 and type(value) is dict:
            parts = [display(key, 1) + ": " + display(item, 1) for key, item in list(value.items())[:6]]
            return "{" + ", ".join(parts) + (", …" if len(value) > 6 else "") + "}"
        return "<" + type(value).__name__ + ">"
    except Exception:
        return "<无法显示>"

def trace(frame, event, arg):
    global truncated, trace_bytes
    if event != "line" or not frame.f_code.co_filename.startswith(ROOT):
        return trace
    if len(events) >= MAX_EVENTS:
        truncated = True
        return trace
    local_values = {}
    for name, value in frame.f_locals.items():
        if name.startswith("__"):
            continue
        local_values[str(name)[:40]] = display(value)[:160]
        if len(local_values) >= 8:
            break
    item = {"file": frame.f_code.co_filename[len(ROOT):], "line": frame.f_lineno, "locals": local_values}
    item_bytes = len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
    if trace_bytes + item_bytes > 6500:
        truncated = True
        return trace
    events.append(item)
    trace_bytes += item_bytes
    return trace

stdout = BoundedText()
stderr = BoundedText()
exit_code = 0
sys.path.insert(0, ROOT.rstrip("/"))
with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
    try:
        sys.settrace(trace)
        runpy.run_path(ROOT + "main.py", run_name="__main__")
    except SystemExit as exc:
        exit_code = exc.code if type(exc.code) is int else (0 if exc.code is None else 1)
    except BaseException:
        exit_code = 1
        traceback.print_exc(limit=8)
    finally:
        sys.settrace(None)

payload = {
    "passed": exit_code == 0,
    "code": "passed" if exit_code == 0 else "runtime_error",
    "checks": [],
    "exit_code": exit_code,
    "stdout": stdout.getvalue(),
    "stderr": stderr.getvalue(),
    "stdout_truncated": stdout.truncated,
    "stderr_truncated": stderr.truncated,
    "trace": events,
    "trace_truncated": truncated,
}
print("__TEACHING_EVIDENCE__=" + json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
'''
