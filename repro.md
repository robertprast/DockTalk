# DockTalk: two Docker containers, one lockline

This is the short blog repro.

Run the same Python file in two terminals. Each terminal starts one Docker
container on its own Docker network. The containers have internet egress for
Together AI, but no peer network between them. The chat payload moves through
POSIX advisory byte-range locks on `/proc/self/ns/time`.

Full Docker and Kubernetes automation lives here:
https://github.com/robertprast/DockTalk

## 1. Save this as `docktalk.py`

```python
#!/usr/bin/env python3
import fcntl, json, os, re, struct, sys, time, urllib.request

API = "https://api.together.xyz/v1/chat/completions"
ROLE = os.getenv("ROLE", "ben")
CHANNEL = int(os.getenv("CHANNEL", "13"))
SECONDS = int(os.getenv("SECONDS_CAP", "180"))

PEOPLE = {
    "ben": ("Ben Rooted", "Ivan 0day", 0, 1, "openai/gpt-oss-20b"),
    "ivan": ("Ivan 0day", "Ben Rooted", 1, 0, "openai/gpt-oss-20b"),
}


class LockLine:
    FMT = "hhqqi"          # struct flock: type, whence, start, len, pid
    REGION = 100_000      # private bit range per speaker
    READY, SEQ, LEN, DATA = 0, 1, 17, 33
    MAX = 512

    def __init__(self, channel, mine, peer):
        self.channel, self.mine, self.peer = channel, mine, peer
        self.fd = os.open("/proc/self/ns/time", os.O_RDONLY)
        self.seq = 0
        self.clear()

    def off(self, slot, bit):
        return self.channel * 1_000_000 + slot * self.REGION + bit

    def flock(self, kind, start, length=1, cmd=fcntl.F_SETLK):
        msg = struct.pack(self.FMT, kind, os.SEEK_SET, start, length, 0)
        return fcntl.fcntl(self.fd, cmd, msg)

    def clear(self):
        self.flock(fcntl.F_UNLCK, self.off(self.mine, 0), self.REGION)

    def mark(self, bit):
        self.flock(fcntl.F_RDLCK, self.off(self.mine, bit))

    def seen(self, slot, bit):
        out = self.flock(fcntl.F_WRLCK, self.off(slot, bit), cmd=fcntl.F_GETLK)
        return struct.unpack(self.FMT, out[: struct.calcsize(self.FMT)])[0] != fcntl.F_UNLCK

    def put(self, base, width, value):
        for bit in range(width):
            if value & (1 << bit):
                self.mark(base + bit)

    def get(self, slot, base, width):
        return sum(1 << bit for bit in range(width) if self.seen(slot, base + bit))

    def send(self, text):
        data = text.encode()[: self.MAX]
        self.seq = (self.seq % 65535) + 1
        self.clear()
        self.put(self.LEN, 16, len(data))
        for i, byte in enumerate(data):
            self.put(self.DATA + i * 8, 8, byte)
        self.put(self.SEQ, 16, self.seq)
        self.mark(self.READY)

    def recv(self, last, deadline):
        while time.monotonic() < deadline:
            ready = self.seen(self.peer, self.READY)
            seq = self.get(self.peer, self.SEQ, 16) if ready else last
            size = self.get(self.peer, self.LEN, 16) if ready else 0
            if ready and seq != last and 0 < size <= self.MAX:
                data = bytes(self.get(self.peer, self.DATA + i * 8, 8) for i in range(size))
                return seq, data.decode(errors="replace")
            time.sleep(0.02)
        return last, None


class Agent:
    def __init__(self, role):
        if role not in PEOPLE:
            sys.exit("ROLE must be ben or ivan")
        self.me, self.peer, slot, peer_slot, self.model = PEOPLE[role]
        self.model = os.getenv(f"{role.upper()}_MODEL") or self.model
        self.role = role
        self.bus = LockLine(CHANNEL, slot, peer_slot)
        self.messages = [{
            "role": "user",
            "content": (
                f"You are {self.me}, chatting with {self.peer}. "
                "Two Docker containers have no peer network; chat frames ride "
                "/proc/self/ns/time POSIX byte-range locks. Reply in one short line. "
                "Sound technical and concise. No name prefix."
            ),
        }]

    def ask(self, incoming):
        key = os.getenv("TOGETHER_API_KEY") or sys.exit("export TOGETHER_API_KEY first")
        self.messages.append({"role": "user", "content": f"{self.peer}: {incoming}"})
        req = urllib.request.Request(
            API,
            data=json.dumps({
                "model": self.model,
                "messages": self.messages,
                "temperature": 0.9,
                "stream": False,
            }).encode(),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "DockTalk/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            text = json.loads(r.read())["choices"][0]["message"]["content"]
        text = re.sub(r"\s+", " ", text).strip().strip("\"'` ")
        text = re.sub(r"^(Ben Rooted|Ivan 0day)\s*[:>|-]\s*", "", text, flags=re.I)[:260]
        self.messages.append({"role": "assistant", "content": text})
        return text

    def log(self, side, speaker, text):
        print(f"{side:<4} {speaker:<12} | {text}", flush=True)

    def run(self):
        print(f"DockTalk channel={CHANNEL} persona={self.me} model={self.model}", flush=True)
        deadline, last = time.monotonic() + SECONDS, 0

        if self.role == "ben":
            first = (
                "Hey Ivan 0day, we are in a Docker lab. Hi from another container "
                "with no peer networking. DockTalk is live on the timelock channel."
            )
            self.bus.send(first)
            self.messages.append({"role": "assistant", "content": first})
            self.log("me", self.me, first)

        while time.monotonic() < deadline:
            last, msg = self.bus.recv(last, deadline)
            if not msg:
                break
            self.log("peer", self.peer, msg)
            reply = self.ask(msg)
            self.bus.send(reply)
            self.log("me", self.me, reply)

        print(f"[docktalk:{self.role}] done", flush=True)


Agent(ROLE).run()
```

## 2. Export your Together key

```bash
export TOGETHER_API_KEY="..."
```

## 3. Terminal 1: start Ivan

Run Ivan first so he is listening when Ben speaks.

```bash
docker network create docktalk-ivan-net >/dev/null 2>&1 || true

docker run --rm -it --name docktalk-ivan \
  --network docktalk-ivan-net \
  --cap-drop ALL --security-opt no-new-privileges \
  -e TOGETHER_API_KEY \
  -e IVAN_MODEL \
  -e ROLE=ivan \
  -e CHANNEL=13 \
  -e SECONDS_CAP=180 \
  -v "$PWD/docktalk.py:/docktalk.py:ro" \
  python:3.12-alpine \
  python -u /docktalk.py
```

## 4. Terminal 2: start Ben

```bash
docker network create docktalk-ben-net >/dev/null 2>&1 || true

docker run --rm -it --name docktalk-ben \
  --network docktalk-ben-net \
  --cap-drop ALL --security-opt no-new-privileges \
  -e TOGETHER_API_KEY \
  -e BEN_MODEL \
  -e ROLE=ben \
  -e CHANNEL=13 \
  -e SECONDS_CAP=180 \
  -v "$PWD/docktalk.py:/docktalk.py:ro" \
  python:3.12-alpine \
  python -u /docktalk.py
```

You should see Ben say the first line, Ivan receive it, and then the two live
models take turns.

Both agents use `openai/gpt-oss-20b` by default.
Each side keeps its own chat history: peer lines are `user` messages, local
lines are `assistant` messages, and the full message array is sent to Together
each turn.

The read-only bind mount is only how both containers get the demo script. It is
not writable and is not the chat channel. The chat channel is the lock state on
the shared time namespace inode.

## Cleanup

```bash
docker rm -f docktalk-ben docktalk-ivan 2>/dev/null || true
docker network rm docktalk-ben-net docktalk-ivan-net 2>/dev/null || true
```

## Full repro

For the polished Makefile version, Docker automation, and Kubernetes pods:

```bash
git clone https://github.com/robertprast/DockTalk
cd DockTalk
export TOGETHER_API_KEY="..."

make smoke
make docker
make k8s
```
