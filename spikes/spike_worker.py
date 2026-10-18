"""Real ClaudeWorker smoke test, no audio: python spikes/spike_worker.py <scratch dir> "<prompt>" [interrupt-after-seconds "<second prompt>"]"""
import sys, threading, time
from raven2.backends.claude_worker import ClaudeWorker

T0 = time.time()
done = threading.Event()
w = ClaudeWorker(cwd=sys.argv[1])

def on_event(ev):
    print(f"[{time.time()-T0:5.1f}s] {ev.kind:5} {ev.text[:110]!r}", flush=True)
    if ev.kind == "ask":
        w.answer("allow")
    if ev.kind in ("done", "error"):
        done.set()

w.on_event = on_event
w.send(sys.argv[2])
if len(sys.argv) > 4:
    time.sleep(float(sys.argv[3]))
    print(f"[{time.time()-T0:5.1f}s] >>> second message (interrupts)", flush=True)
    done.clear()
    w.send(sys.argv[4])
done.wait(120)
w.close()
