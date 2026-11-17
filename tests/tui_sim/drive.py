"""Drive a real OpenCode TUI with a fake dictation helper and read the composer.

Manual regression for the live draft: no microphone or models are used. A fake
`voice_helper.py dictate` streams a known transcript (words.txt) at speaking pace,
this script presses F9, types or edits at chosen moments and prints what the
message box shows. Needs a running OpenCode service (~/.local/state/opencode/
service.json), the plugin loaded globally, and `pyte` in the interpreter:

    uv venv /tmp/sim && uv pip install --python /tmp/sim/bin/python pyte
    /tmp/sim/bin/python tests/tui_sim/drive.py plain|type|mid|inside

plain   no edits (a long turn must reach the last word)
type    type after the dictated text, then keep talking
mid     type at the start of the text, then keep talking
inside  type in the middle of a dictated word sequence, then keep talking
Expected: every scenario ends with "...microphone is still on." plus your typing.
"""
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import sys
import tempfile
import termios
import time

import pyte

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
scenario = sys.argv[1] if len(sys.argv) > 1 else "plain"
service = json.loads((Path.home() / ".local/state/opencode/service.json").read_text())
work = Path(tempfile.mkdtemp(prefix="tui-voice-sim-"))
wrapper = work / "python-wrapper"
wrapper.write_text(f"""#!/bin/sh
if [ "$2" = "dictate" ]; then exec {sys.executable} {HERE / 'fake_dictate.py'} {HERE / 'words.txt'} 0.3; fi
exec {sys.executable} "$@"
""")
wrapper.chmod(0o755)
env = {**os.environ, "OPENCODE_PASSWORD": service["password"], "TERM": "xterm-256color",
       "TUI_VOICE_PYTHON": str(wrapper), "TUI_VOICE_LOG_DIR": str(work / "log"),
       "TUI_VOICE_AUTO_INSTALL": "0"}
cols, rows = 110, 32
master, slave = pty.openpty()
fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
opencode = os.environ.get("OPENCODE_BIN", str(Path.home() / ".opencode/bin/opencode"))
proc = subprocess.Popen([opencode, "--server", service["url"], "--session",
                         "ses_depcheck0000000000000001", str(REPO)],
                        stdin=slave, stdout=slave, stderr=slave, env=env, start_new_session=True)
os.close(slave)
screen = pyte.Screen(cols, rows)
stream = pyte.ByteStream(screen)
start = time.monotonic()


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        ready, _, _ = select.select([master], [], [], 0.05)
        if ready:
            try:
                stream.feed(os.read(master, 65536))
            except OSError:
                return


def snap(label):
    lines = [line.rstrip() for line in screen.display if line.strip()]
    print(f"--- {label} t={time.monotonic() - start:.0f}s")
    print("\n".join(line for line in lines[-9:] if "▀▀▀" not in line and "shift+tab" not in line))


F9 = b"\x1b[20~"
pump(7)
os.write(master, F9)
pump(3)
snap("listening")
if scenario == "plain":
    pump(45)
    snap("long turn")
elif scenario == "type":
    pump(6)
    os.write(master, b"TYPED-A ")
    pump(1)
    snap("after typing at the end")
    pump(32)
    snap("end of speech")
elif scenario == "mid":
    pump(6)
    os.write(master, b"\x01FIX ")
    pump(1)
    snap("after typing at the start")
    pump(33)
    snap("end of speech")
elif scenario == "inside":
    pump(6)
    os.write(master, b"\x01" + b"\x1b[C" * 7 + b"XX")
    pump(1)
    snap("after typing inside the text")
    pump(33)
    snap("end of speech")
os.write(master, F9)
pump(3)
snap("after stop")
proc.terminate()
