"""Spike: how does ClaudeSDKClient behave when a new message arrives mid-run?"""
import asyncio, sys, time
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, PermissionResultAllow

MODE = sys.argv[1] if len(sys.argv) > 1 else "interrupt"
T0 = time.time()

def log(*a):
    print(f"[{time.time()-T0:5.1f}s]", *a, flush=True)

async def can_use_tool(name, tool_input, ctx):
    log("PERMISSION ASK:", name, str(tool_input)[:80])
    return PermissionResultAllow()

async def drain(client, label):
    async for msg in client.receive_response():
        kind = type(msg).__name__
        text = ""
        for b in getattr(msg, "content", []) or []:
            text += getattr(b, "text", "") or getattr(b, "name", "") or ""
        log(label, kind, text[:90].replace("\n", " "))

async def main():
    opts = ClaudeAgentOptions(
        cwd=sys.argv[2],
        can_use_tool=can_use_tool,
        allowed_tools=[],
        max_turns=6,
    )
    async with ClaudeSDKClient(options=opts) as client:
        await client.query("Run the bash command `sleep 20; echo finished-first-task`, then reply 'FIRST DONE'.")
        first = asyncio.create_task(drain(client, "A"))
        await asyncio.sleep(8)
        if MODE == "interrupt":
            log(">>> interrupt() then new query")
            await client.interrupt()
            await first
            await client.query("Forget that. Just reply with exactly: SECOND MESSAGE RECEIVED")
            await drain(client, "B")
        else:
            log(">>> new query WITHOUT interrupt")
            await client.query("Also, when done, reply with: SECOND MESSAGE RECEIVED")
            await first
            await drain(client, "B")

asyncio.run(asyncio.wait_for(main(), 75))
