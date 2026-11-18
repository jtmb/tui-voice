import json, os, signal, sys, time
words = open(sys.argv[1]).read().split()
stop = False
def handler(*_): 
    global stop; stop = True
signal.signal(signal.SIGUSR1, handler)
def emit(o): print(json.dumps(o), flush=True)
emit({'event': 'starting'})
load = float(os.environ.get('FAKE_LOAD_SECONDS', '0'))
if load:
    emit({'event': 'model', 'state': 'loading'})
time.sleep(0.5); emit({'event': 'listening', 'device': 'fake'})
if load:
    time.sleep(load)  # audio is captured while the model loads; text waits for it
    emit({'event': 'model', 'state': 'ready', 'seconds': load})
n = 0
while not stop and n < len(words):
    n += 1; emit({'event': 'partial', 'text': ' '.join(words[:n])}); time.sleep(float(sys.argv[2]) if len(sys.argv) > 2 else 0.3)
while not stop: time.sleep(0.1)
emit({'event': 'transcribing'}); time.sleep(0.3)
print(json.dumps({'ok': True, 'text': ' '.join(words[:n]), 'device': 'fake', 'raw_audio_retained': False}), flush=True)
