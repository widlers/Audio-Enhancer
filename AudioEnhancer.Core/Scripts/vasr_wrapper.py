#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vasr_wrapper.py

Lightweight wrapper for audiosr CLI with optional server mode.
Usage:
 python vasr_wrapper.py <input> <output>
 python vasr_wrapper.py --server [--host HOST] [--port PORT]

Server mode accepts JSON requests (one line) over TCP:
 {"input":"C:/in.wav","output":"C:/out.wav"}\n
Responds with JSON: {"rc":0,"stdout":"...","stderr":"..."}\n
If audiosr/CLI is not available the wrapper will copy input -> output as fallback.
"""
from __future__ import annotations
import argparse
import importlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import shutil
from pathlib import Path
from typing import List, Optional, Tuple

AUDIO_EXTS = [".wav", ".flac", ".mp3", ".m4a", ".ogg"]


def ensure_numpy_compat() -> None:
    """Small compatibility shim for older code that expects np.float"""
    try:
        import numpy as np  # type: ignore
        if not hasattr(np, "float"):
            setattr(np, "float", float)
    except Exception:
        pass


def try_import_audiosr() -> Tuple[bool, str]:
    # audiosr removed/disabled � do not attempt import
    return False, ""


def find_latest_audio_in_dir(directory: str, within_seconds: int = 300) -> Optional[str]:
    try:
        d = Path(directory)
        if not d.is_dir():
            return None
        cutoff = time.time() - within_seconds
        candidates = [
            f for f in d.iterdir() if f.is_file() and f.suffix.lower() in AUDIO_EXTS and f.stat().st_mtime >= cutoff
        ]
        if not candidates:
            candidates = [f for f in d.iterdir() if f.is_file() and f.suffix.lower() in AUDIO_EXTS]
        if not candidates:
            return None
        latest = max(candidates, key=lambda f: f.stat().st_mtime)
        return str(latest)
    except Exception as ex:
        print(f"Error finding latest audio in dir {directory}: {ex}", file=sys.stderr)
        return None


def poll_for_output(out: str, candidate_dirs: List[str], timeout: float = 10.0, interval: float = 0.5) -> Optional[str]:
    end = time.time() + timeout
    while time.time() < end:
        if os.path.exists(out):
            return out
        for d in candidate_dirs:
            try:
                found = find_latest_audio_in_dir(d, within_seconds=600)
                if found:
                    try:
                        if os.path.abspath(found) == os.path.abspath(out) or (time.time() - os.path.getmtime(found)) <= (
                                timeout + 10):
                            return found
                    except Exception:
                        return found
            except Exception:
                continue
        time.sleep(interval)
    return None


def try_run_audiosr_cli(inp: str, out: str) -> int:
    """Run audiosr CLI with several unambiguous argument patterns.
    Returns exit code (0 success, non-zero failure).
    """
    out_dir = str(Path(out).parent)
    device = os.environ.get("AUDIO_SR_DEVICE", "cpu")

    # Explicit, unambiguous candidate commands
    candidates: List[Tuple[List[str], Optional[str]]] = [
        ([sys.executable, "-m", "audiosr", "-i", inp, "-s", out_dir, "--model_name", "basic", "--device", device], out_dir),
        ([sys.executable, "-m", "audiosr", "enhance", "--input_audio_file", inp, "--output", out, "--device", device], None),
        ([sys.executable, "-m", "audiosr", "enhance", "-i", inp, "-s", out_dir, "--model_name", "basic", "--device", device], out_dir),
        (["audiosr", "--input_audio_file", inp, "--save_path", out_dir, "--device", device], out_dir),
        (["audiosr", "-i", inp, "-s", out_dir, "--device", device], out_dir),
    ]

    fallback_dirs = list(dict.fromkeys([out_dir, str(Path(inp).parent), tempfile.gettempdir(), os.getcwd()]))

    for cmd, save_dir in candidates:
        try:
            print("Versuche CLI: %s" % " ".join(cmd), file=sys.stderr) # Log to stderr to ensure visibility
            res = subprocess.run(cmd, check=False, capture_output=True, text=True)
            if res.stdout:
                print(res.stdout)
            if res.stderr:
                print(f"STDERR from CLI: {res.stderr}", file=sys.stderr)
            
            if res.returncode == 0:
                dirs_to_check: List[str] = []
                if save_dir:
                    dirs_to_check.append(save_dir)
                dirs_to_check.extend(fallback_dirs)
                found = poll_for_output(out, dirs_to_check, timeout=30.0, interval=0.5)
                if found:
                    if os.path.abspath(found) != os.path.abspath(out):
                        try:
                            shutil.copyfile(found, out)
                            print(f"Copied produced file {found} -> {out}")
                        except Exception as ex:
                            print(f"Failed to copy produced file {found} -> {out}: {ex}", file=sys.stderr)
                            return 2
                    else:
                        print(f"Output found: {out}")
                    return 0
                print(f"CLI returned 0 but expected output {out} not found after wait.", file=sys.stderr)
                # Don't return 2 yet, maybe next candidate works? No, if rc=0 we assume it ran.
                # But let's allow fallback to others if this failed to produce output?
                # Actually if it says success but no file, it's likely a logic error in args.
            else:
                 print(f"CLI command returned non-zero exit code: {res.returncode}", file=sys.stderr)

        except Exception as ex:
            print(f"CLI execution exception for {cmd[0]}: {ex}", file=sys.stderr)

    # fallback: copy input -> output
    try:
        shutil.copyfile(inp, out)
        return 0
    except Exception as ex:
        print(f"Final fallback copy failed: {ex}", file=sys.stderr)
        return 1


def process_enhance(inp: str, out: str) -> Tuple[int, str, str]:
    """Enhancement disabled � report error to caller.

    The server/wrapper no longer performs enhancement or copying. This function
    returns a non-zero rc and a clear stderr message.
    """
    return 9, "", "enhancement disabled: audiosr removed"


def start_server(host: str, port: int) -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(5)
    print(f"vasr_wrapper: server listening {host}:{port}")

    def handle_client(conn: socket.socket, addr) -> None:
        try:
            data = b""
            # read until newline
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"\n" in chunk:
                    break
            try:
                payload = json.loads(data.decode("utf-8").strip())
                inp = payload.get("input")
                out = payload.get("output")
                if not inp or not out:
                    resp = {"rc": 5, "stdout": "", "stderr": "invalid request"}
                else:
                    print(f"Server: process request input={inp} output={out}")
                    rc, so, se = process_enhance(inp, out)
                    resp = {"rc": rc, "stdout": so, "stderr": se}
            except Exception as ex:
                resp = {"rc": 6, "stdout": "", "stderr": str(ex)}
            respb = (json.dumps(resp) + "\n").encode("utf-8")
            try:
                conn.sendall(respb)
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    try:
        while True:
            conn, addr = srv.accept()
            t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
            t.start()
    except KeyboardInterrupt:
        print("server shutting down")
    finally:
        try:
            srv.close()
        except Exception:
            pass


def main() -> None:
    # Special-case: if --server is present, parse only server args so positional input/output are not required
    if "--server" in sys.argv:
        p = argparse.ArgumentParser(description="vasr_wrapper server mode")
        p.add_argument("--port", type=int, default=45555, help="Server port if --server")
        p.add_argument("--host", default="127.0.0.1", help="Host to bind the server")
        p.add_argument("--no-import-warmup", action="store_true", help="Skip audiosr import on server start")
        args = p.parse_args()
        if not args.no_import_warmup:
            try:
                ok, loc = try_import_audiosr()
                if not ok:
                    print("Warning: audiosr import failed in server start; server will still accept requests and try CLI.", file=sys.stderr)
            except Exception as ex:
                print(f"audiosr import attempt failed: {ex}", file=sys.stderr)
        start_server(args.host, args.port)
        return

    # Normal single-run mode: require input and output
    p = argparse.ArgumentParser(description="vasr_wrapper single-run mode")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--cmd", help="Optionales CLI-Template (use {input} and {output})")
    args = p.parse_args()

    inp = args.input
    out = args.output

    if not inp or not out:
        print("Usage: vasr_wrapper.py <input> <output> [--cmd ...]", file=sys.stderr)
        sys.exit(2)

    print(f"vasr_wrapper: argv input={inp} output={out}", file=sys.stderr)
    print(f"vasr_wrapper: cwd={os.getcwd()}", file=sys.stderr)
    print(f"vasr_wrapper: sys.executable={sys.executable}", file=sys.stderr)
    print(f"vasr_wrapper: PATH={os.environ.get('PATH', '')}", file=sys.stderr)
    try:
        td = tempfile.gettempdir()
        print(f"vasr_wrapper: tempdir={td} listing recent files:", file=sys.stderr)
        for pth in sorted(Path(td).iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:10]:
            try:
                print(f" {pth} mtime={pth.stat().st_mtime} size={pth.stat().st_size}", file=sys.stderr)
            except Exception:
                pass
    except Exception as ex:
        print(f"vasr_wrapper: temp listing failed: {ex}", file=sys.stderr)

    if args.cmd:
        rc = run_cli_template(args.cmd, inp, out)
        sys.exit(rc)

    # Attempt to run via CLI wrapper
    rc = try_run_audiosr_cli(inp, out)
    sys.exit(rc)


if __name__ == "__main__":
    # Robust short-circuit: if called with --server, start server immediately (avoid argparse surprises)
    if "--server" in sys.argv:
        host = "127.0.0.1"
        port = 45555
        no_import = True # skip any audiosr import attempts
        for i, a in enumerate(sys.argv):
            if a in ("--host",) and i + 1 < len(sys.argv):
                host = sys.argv[i + 1]
            if a in ("--port",) and i + 1 < len(sys.argv):
                try:
                    port = int(sys.argv[i + 1])
                except Exception:
                    pass
            if a == "--no-import-warmup":
                no_import = True
        start_server(host, port)
        sys.exit(0)
    main()
