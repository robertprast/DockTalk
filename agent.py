#!/usr/bin/env python3
import argparse
import fcntl
import json
import os
import re
import struct
import sys
import time
import urllib.error
import urllib.request


TOGETHER_URL = "https://api.together.xyz/v1/chat/completions"
BEN_MODEL = "openai/gpt-oss-20b"
IVAN_MODEL = "openai/gpt-oss-20b"

REGION_BITS = 100_000
READY_BIT = 0
SEQ_BASE = 1
LEN_BASE = 17
DATA_BASE = 33
LEN_BITS = 16
SEQ_BITS = 16
MAX_BYTES = 640

FLOCK_STRUCT = "hhqqi"


PEOPLE = {
    "ben": {
        "name": "Ben Rooted",
        "peer": "Ivan 0day",
        "slot": 0,
        "peer_slot": 1,
        "model": BEN_MODEL,
    },
    "ivan": {
        "name": "Ivan 0day",
        "peer": "Ben Rooted",
        "slot": 1,
        "peer_slot": 0,
        "model": IVAN_MODEL,
    },
}


def log(line=""):
    print(line, flush=True)


def die(message):
    print(f"error: {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


class TimeLockTransport:
    def __init__(self, channel, slot):
        self.channel = channel
        self.slot = slot
        self.fd = os.open("/proc/self/ns/time", os.O_RDONLY)
        self.seq = 0
        self.clear_region(slot)

    def close(self):
        os.close(self.fd)

    def offset(self, slot, bit):
        return self.channel * 1_000_000 + slot * REGION_BITS + bit

    def flock(self, lock_type, start, length=1):
        data = struct.pack(FLOCK_STRUCT, lock_type, os.SEEK_SET, start, length, 0)
        return fcntl.fcntl(self.fd, fcntl.F_SETLK, data)

    def clear_region(self, slot):
        self.flock(fcntl.F_UNLCK, self.offset(slot, 0), REGION_BITS)

    def set_bit(self, slot, bit):
        self.flock(fcntl.F_RDLCK, self.offset(slot, bit), 1)

    def bit_is_set(self, slot, bit):
        data = struct.pack(
            FLOCK_STRUCT,
            fcntl.F_WRLCK,
            os.SEEK_SET,
            self.offset(slot, bit),
            1,
            0,
        )
        result = fcntl.fcntl(self.fd, fcntl.F_GETLK, data)
        lock_type = struct.unpack(FLOCK_STRUCT, result[: struct.calcsize(FLOCK_STRUCT)])[0]
        return lock_type != fcntl.F_UNLCK

    def write_int(self, slot, base_bit, bit_count, value):
        for bit in range(bit_count):
            if value & (1 << bit):
                self.set_bit(slot, base_bit + bit)

    def read_int(self, slot, base_bit, bit_count):
        value = 0
        for bit in range(bit_count):
            if self.bit_is_set(slot, base_bit + bit):
                value |= 1 << bit
        return value

    def send(self, text):
        payload = text.encode("utf-8")[:MAX_BYTES]
        self.seq = (self.seq % 65535) + 1

        self.clear_region(self.slot)
        self.write_int(self.slot, LEN_BASE, LEN_BITS, len(payload))

        for index, byte in enumerate(payload):
            for bit in range(8):
                if byte & (1 << bit):
                    self.set_bit(self.slot, DATA_BASE + index * 8 + bit)

        self.write_int(self.slot, SEQ_BASE, SEQ_BITS, self.seq)
        self.set_bit(self.slot, READY_BIT)

    def receive(self, peer_slot, last_seq, deadline):
        while time.monotonic() < deadline:
            if not self.bit_is_set(peer_slot, READY_BIT):
                time.sleep(0.05)
                continue

            seq = self.read_int(peer_slot, SEQ_BASE, SEQ_BITS)
            if seq == 0 or seq == last_seq:
                time.sleep(0.05)
                continue

            size = self.read_int(peer_slot, LEN_BASE, LEN_BITS)
            if size <= 0 or size > MAX_BYTES:
                time.sleep(0.05)
                continue

            out = bytearray()
            for index in range(size):
                byte = 0
                for bit in range(8):
                    if self.bit_is_set(peer_slot, DATA_BASE + index * 8 + bit):
                        byte |= 1 << bit
                out.append(byte)

            return seq, out.decode("utf-8", errors="replace")

        return last_seq, None


def together_chat(model, messages, timeout=120):
    key = os.environ.get("TOGETHER_API_KEY")
    if not key:
        die("TOGETHER_API_KEY is required")

    body = {
        "model": model,
        "messages": messages,
        "temperature": 0.9,
        "stream": False,
    }
    request = urllib.request.Request(
        TOGETHER_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "DockTalk/1.0 curl-compatible",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            error_body = exc.read().decode("utf-8")
        except Exception:
            error_body = str(exc)
        raise RuntimeError(f"Together HTTP {exc.code}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Together request failed: {exc}") from exc

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Together response missing message content: {data}") from exc

    if isinstance(content, list):
        content = " ".join(str(part.get("text", part)) for part in content)

    return clean_line(str(content))


def clean_line(text):
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^(Ben Rooted|Ivan 0day)\s*[:>|-]\s*", "", text, flags=re.I)
    text = text.strip("\"'` ")
    if len(text) > 260:
        text = text[:257].rstrip() + "..."
    return text


def starting_history(me, peer):
    return [
        {
            "role": "user",
            "content": (
                f"You are {me}, chatting with {peer} in a fictional DockTalk blog demo. "
                "The two containers have no peer network and no shared writable volume; "
                "chat frames move through /proc/self/ns/time as POSIX advisory "
                "byte-range locks. Reply in one short line. Sound like a real "
                "technical person having fun with a weird kernel side-channel. "
                "No payloads, credentials, persistence, evasion, exfiltration, "
                "or step-by-step intrusion instructions. Do not prefix your name."
            ),
        }
    ]


def history_reply(model, history):
    reply = together_chat(model, history)
    if reply:
        history.append({"role": "assistant", "content": reply})
        return reply

    history.append({"role": "user", "content": "One concise reply. No name prefix."})
    reply = together_chat(model, history)
    history.append({"role": "assistant", "content": reply})
    return reply


def print_banner(me, peer, model, channel, seconds, mode):
    log("+------------------------------------------------------------------+")
    log("| Crash Override / DockTalk                                       |")
    log("| nsfs time-namespace lockline                                    |")
    log("+------------------------------------------------------------------+")
    log(f" local persona : {me}")
    log(f" local model   : Together -> {model}")
    log(f" peer persona  : {peer}")
    log(" arch          : two containers, no peer network, no shared writable volume")
    log(" carrier       : /proc/self/ns/time byte-range locks")
    log(f" channel       : {channel}")
    log(f" mode          : {mode}")
    log(f" spend cap     : {seconds}s")
    log("--------------------------------------------------------------------------")


def run_agent(args):
    info = PEOPLE[args.agent]
    name = info["name"]
    peer = info["peer"]
    slot = info["slot"]
    peer_slot = info["peer_slot"]
    model = args.model or info["model"]
    deadline = time.monotonic() + args.seconds

    print_banner(name, peer, model, args.channel, args.seconds, args.mode)
    transport = TimeLockTransport(args.channel, slot)
    history = starting_history(name, peer)
    last_peer_seq = 0

    try:
        if args.agent == "ben":
            opening = (
                "Hey Ivan 0day, we are in a k8 lab. Hi from another container "
                "with no peer networking. DockTalk is live on the timelock channel."
            )
            transport.send(opening)
            history.append({"role": "assistant", "content": opening})
            log(f"me   {name:<12} | {opening}")

        while time.monotonic() < deadline:
            last_peer_seq, incoming = transport.receive(peer_slot, last_peer_seq, deadline)
            if not incoming:
                break

            log(f"peer {peer:<12} | {incoming}")
            history.append({"role": "user", "content": f"{peer}: {incoming}"})
            try:
                reply = history_reply(model, history)
            except Exception as exc:
                log(f"me   {name:<12} | model error: {exc}")
                break

            if not reply:
                log(f"me   {name:<12} | model returned empty content")
                break

            transport.send(reply)
            log(f"me   {name:<12} | {reply}")

        log(f"[docktalk:{args.agent}] done")
    finally:
        transport.close()


def smoke():
    checks = [
        ("Ben Rooted", BEN_MODEL),
        ("Ivan 0day", IVAN_MODEL),
    ]
    for name, model in checks:
        prompt = (
            f"Reply as {name} with one short sentence confirming DockTalk model smoke test."
        )
        reply = together_chat(model, [{"role": "user", "content": prompt}])
        log(f"{name} / {model}: {reply}")


def parse_args():
    parser = argparse.ArgumentParser(description="DockTalk time-namespace lock chat")
    parser.add_argument("--smoke", action="store_true", help="check both Together models")
    parser.add_argument("--agent", choices=sorted(PEOPLE.keys()))
    parser.add_argument("--model", help="override Together model")
    parser.add_argument("--channel", type=int, default=int(os.environ.get("CHANNEL", "13")))
    parser.add_argument(
        "--seconds",
        type=int,
        default=int(os.environ.get("SECONDS_CAP", "180")),
        help="conversation cap in seconds",
    )
    parser.add_argument("--mode", default=os.environ.get("DOCKTALK_MODE", "local"))
    return parser.parse_args()


def main():
    args = parse_args()
    if args.smoke:
        smoke()
        return
    if not args.agent:
        die("--agent is required unless --smoke is used")
    run_agent(args)


if __name__ == "__main__":
    main()
