"""Stand-in for `voice_helper.py warm ...`: records the call, touches no worker."""
import json
import os
import sys

with open(os.environ["FAKE_WARM_LOG"], "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\n")
if "--set-idle" in sys.argv:
    print(json.dumps({"ok": True, "idle_seconds": int(sys.argv[sys.argv.index("--set-idle") + 1]),
                      "applied": {"asr": "updated", "tts": "updated"}}))
else:
    print(json.dumps({"ok": True, "started": {}}))
