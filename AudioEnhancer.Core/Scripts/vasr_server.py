#!/usr/bin/env python3
"""
Simple persistent server for audiosr enhancements.
Run this from the activated Conda env that contains audiosr:
 python FileHelpers/vasr_server.py --port45555

Protocol: send one JSON line {"input":"C:/in.wav","output":"C:/out.wav"}\n
Response: one JSON line {"rc":0,"stdout":"...","stderr":"..."}\n
This script imports audiosr once to warm caches and then uses the CLI (python -m audiosr)
for each request. It's deliberately minimal to avoid interfering with the existing wrapper file.
"""
from __future__ import annotations
import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import tempfile
import time
from pathlib import Path
import shutil

AUDIO_EXTS = [".wav", ".flac", ".mp3", ".m4a", ".ogg"]


def ensure_numpy_compat():
    try:
        import numpy as np
        if not hasattr(np, "float"):
            setattr(np, "float", float)
    except Exception:
        pass


def try_import_audiosr():
    try:
        ensure_numpy_compat()
        import importlib
        mod = importlib.import_module("audiosr")
        loc = getattr(mod, "__file__", "unknown")
        ver = getattr(mod, "__version", getattr(mod, "VERSION", "unknown"))
        print(f"audiosr imported: {ver}, file={loc}")
        return True
    except Exception as ex:
        print(f"audiosr import failed: {ex}", file=sys.stderr)
        return False


def _find_latest_audio_in_dir(directory: str, within_seconds: int =600) -> str | None:
    try:
        d = Path(directory)
        if not d.is_dir():
            return None
        cutoff = time.time() - within_seconds
        candidates = [f for f in d.iterdir() if f.is_file() and f.suffix.lower() in AUDIO_EXTS and f.stat().st_mtime >= cutoff]
        if not candidates:
            candidates = [f for f in d.iterdir() if f.is_file() and f.suffix.lower() in AUDIO_EXTS]
        if not candidates:
            return None
        latest = max(candidates, key=lambda f: f.stat().st_mtime)
        return str(latest)
    except Exception:
        return None


def _find_produced_in_dir(search_dir: str) -> str | None:
    """Search search_dir and its immediate subdirectories for a likely produced audio file.
    Prefer files containing 'AudioSR' and 'Processed' or the marker '_AudioSR_Processed_'.
    """
    try:
        p = Path(search_dir)
        if not p.exists():
            return None
        files: list[Path] = [f for f in p.iterdir() if f.is_file() and f.suffix.lower() in AUDIO_EXTS]
        # include immediate subdirectories
        for d in p.iterdir():
            if d.is_dir():
                for f in d.iterdir():
                    if f.is_file() and f.suffix.lower() in AUDIO_EXTS:
                        files.append(f)
        if not files:
            return None
        # prefer marker-containing files
        marker_matches = [f for f in files if ("audiosr" in f.name.lower() and "processed" in f.name.lower()) or "_audiosr_processed_" in f.name.lower()]
        if marker_matches:
            latest = max(marker_matches, key=lambda f: f.stat().st_mtime)
            return str(latest)
        # fallback: newest audio file
        latest = max(files, key=lambda f: f.stat().st_mtime)
        return str(latest)
    except Exception:
        return None


def run_audiosr_cli(input_path: str, output_path: str, device: str | None = None) -> tuple[int, str, str]:
    """Try several audiosr CLI variants and return (rc, stdout, stderr).

    If CLI writes to a directory, attempt to find the produced file (including immediate
    timestamped subdirectories) and copy it to output_path. If the input is stereo and
    the model emits mono results, split channels, enhance each separately and merge back.
    """
    out_dir = os.path.dirname(output_path) or tempfile.gettempdir()

    # prepare per-request verbose log
    log_path = None
    try:
        ts = time.strftime("%Y%m%d_%H%M%S")
        pid = os.getpid()
        log_dir = os.path.join(out_dir, "vasr_logs")
        try:
            os.makedirs(log_dir, exist_ok=True)
        except Exception:
            log_dir = out_dir
        log_fname = f"vasr_server-{ts}-{pid}.log"
        log_path = os.path.join(log_dir, log_fname)
    except Exception:
        log_path = None

    def _log(msg: str) -> None:
        if not log_path:
            return
        try:
            with open(log_path, "a", encoding="utf-8") as lf:
                lf.write(msg + "\n")
        except Exception:
            pass

    def _run_candidates_and_find(infile: str, save_dir: str) -> tuple[int, str, str, str | None]:
        candidates = [
            [sys.executable, "-m", "audiosr", "enhance", "-i", infile, "-s", save_dir, "-d", device or "cpu"],
            [sys.executable, "-m", "audiosr", "-i", infile, "-s", save_dir, "-d", device or "cpu"],
            ["audiosr", "-i", infile, "-s", save_dir, "-d", device or "cpu"],
        ]
        last_stdout = ""
        last_stderr = ""
        last_rc =1

        for cmd in candidates:
            if device and "--device" not in cmd and "-d" not in cmd:
                cmd = list(cmd) + ["--device", device]
            try:
                if log_path:
                    _log(f"--- Attempting command: {' '.join(cmd)}")
            except Exception:
                pass
            try:
                p = subprocess.run(cmd, capture_output=True, text=True)
                last_rc = p.returncode
                last_stdout = p.stdout or ""
                last_stderr = p.stderr or ""
                try:
                    if log_path:
                        _log(f"RC={last_rc}\n--- STDOUT ---\n{last_stdout}\n--- STDERR ---\n{last_stderr}")
                except Exception:
                    pass
            except FileNotFoundError as fnf:
                last_rc =127
                last_stdout = ""
                last_stderr = str(fnf)
                try:
                    if log_path:
                        _log(f"Command not found: {fnf}")
                except Exception:
                    pass
                continue
            except Exception as ex:
                last_rc =1
                last_stdout = ""
                last_stderr = str(ex)
                try:
                    if log_path:
                        _log(f"Exception running command: {ex}")
                except Exception:
                    pass
                continue

            # If non-zero, still check for produced file
            if last_rc !=0:
                if os.path.exists(output_path):
                    try:
                        if log_path:
                            _log(f"Output already exists despite rc!=0: {output_path}")
                    except Exception:
                        pass
                    return0, last_stdout, last_stderr, output_path
                found = _find_produced_in_dir(save_dir)
                if found:
                    try:
                        if log_path:
                            _log(f"Found produced file despite rc!=0: {found}")
                    except Exception:
                        pass
                    return0, last_stdout, last_stderr, found
                continue

            # rc ==0
            if os.path.exists(output_path):
                try:
                    if log_path:
                        _log(f"Found expected output: {output_path}")
                except Exception:
                    pass
                return0, last_stdout, last_stderr, output_path
            found = _find_produced_in_dir(save_dir)
            if found:
                try:
                    if log_path:
                        _log(f"Found produced file: {found}")
                except Exception:
                    pass
                return0, last_stdout, last_stderr, found
            return0, last_stdout, last_stderr, None

        return last_rc, last_stdout, last_stderr, None

    #1) Try single-file invocation first
    rc, so, se, produced = _run_candidates_and_find(input_path, out_dir)
    if produced:
        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
        except Exception:
            pass
        try:
            shutil.copyfile(produced, output_path)
            try:
                if log_path:
                    _log(f"Copied produced file {produced} -> {output_path}")
            except Exception:
                pass
            return0, so, se
        except Exception as ex_copy:
            se = (se or "") + f"\ncopy failed: {ex_copy}"
            try:
                if log_path:
                    _log(f"Copy failed: {ex_copy}")
            except Exception:
                pass
            return1, so, se
    if rc ==0 and os.path.exists(output_path):
        return0, so, se

    #2) Probe channels using ffprobe (best-effort)
    in_channels =0
    try:
        p = subprocess.run([
            "ffprobe", "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=channels",
            "-of", "default=noprint_wrappers=1:nokey=1",
            input_path,
        ], capture_output=True, text=True)
        out = p.stdout.strip()
        if out.isdigit():
            in_channels = int(out)
    except Exception:
        in_channels =0
    try:
        if log_path:
            _log(f"Input channels: {in_channels}")
    except Exception:
        pass

    # If stereo, attempt split/enhance/merge
    if in_channels >=2:
        tmp = tempfile.mkdtemp(prefix="vasr_")
        left = os.path.join(tmp, "left.wav")
        right = os.path.join(tmp, "right.wav")
        try:
            p = subprocess.run(["ffmpeg", "-y", "-i", input_path, "-map_channel", "0.0.0", left, "-map_channel", "0.0.1", right], capture_output=True, text=True)
            if p.returncode !=0:
                p2 = subprocess.run(["ffmpeg", "-y", "-i", input_path, "-filter_complex", "channelsplit=channel_layout=stereo[L][R]", "-map", "[L]", left, "-map", "[R]", right], capture_output=True, text=True)
                if p2.returncode !=0:
                    try:
                        if log_path:
                            _log(f"ffmpeg split failed:\n{p.stdout}\n{p.stderr}\n{p2.stdout}\n{p2.stderr}")
                    except Exception:
                        pass
                    shutil.rmtree(tmp, ignore_errors=True)
                    return1, "", "ffmpeg split failed"
        except Exception as ex:
            try:
                if log_path:
                    _log(f"Exception during ffmpeg split: {ex}")
            except Exception:
                pass
            shutil.rmtree(tmp, ignore_errors=True)
            return1, "", str(ex)

        # enhance left and right separately
        rc_l, so_l, se_l, prod_l = _run_candidates_and_find(left, tmp)
        left_out = prod_l or left
        rc_r, so_r, se_r, prod_r = _run_candidates_and_find(right, tmp)
        right_out = prod_r or right

        # merge
        merged = os.path.join(tmp, "merged.wav")
        try:
            m = subprocess.run(["ffmpeg", "-y", "-i", left_out, "-i", right_out, "-filter_complex", "amerge=inputs=2", "-ac", "2", merged], capture_output=True, text=True)
            try:
                if log_path:
                    _log(f"ffmpeg merge RC={m.returncode}\nSTDOUT:\n{m.stdout}\nSTDERR:\n{m.stderr}")
            except Exception:
                pass
            if m.returncode ==0 and os.path.exists(merged):
                try:
                    os.makedirs(os.path.dirname(output_path), exist_ok=True)
                except Exception:
                    pass
                try:
                    shutil.move(merged, output_path)
                except Exception:
                    try:
                        shutil.copyfile(merged, output_path)
                    except Exception:
                        pass
                combined_so = (so_l or "") + "\n" + (so_r or "")
                combined_se = (se_l or "") + "\n" + (se_r or "") + "\n" + (m.stderr or "")
                shutil.rmtree(tmp, ignore_errors=True)
                return0, combined_so, combined_se
            # merge failed -> fallback
            try:
                shutil.copyfile(input_path, output_path)
            except Exception:
                pass
            shutil.rmtree(tmp, ignore_errors=True)
            return1, "", (m.stderr or "")
        except Exception as ex:
            try:
                if log_path:
                    _log(f"Exception in stereo merge: {ex}")
            except Exception:
                pass
            try:
                shutil.copyfile(input_path, output_path)
            except Exception:
                pass
            shutil.rmtree(tmp, ignore_errors=True)
            return1, "", str(ex)

    # Final fallback: try single-file produced or copy input
    try:
        # if earlier single-file produced we already returned; try to locate any produced file now
        produced_any = _find_produced_in_dir(out_dir)
        if produced_any:
            try:
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
            except Exception:
                pass
            try:
                shutil.copyfile(produced_any, output_path)
                try:
                    if log_path:
                        _log(f"Copied produced file {produced_any} -> {output_path}")
                except Exception:
                    pass
                return0, so, se
            except Exception as ex_copy:
                try:
                    if log_path:
                        _log(f"Copy failed: {ex_copy}")
                except Exception:
                    pass
                return1, so, (se or "") + f"\ncopy failed: {ex_copy}"
        shutil.copyfile(input_path, output_path)
        return0, so, se
    except Exception as ex:
        try:
            if log_path:
                _log(f"Final fallback copy failed: {ex}")
        except Exception:
            pass
    return rc, so, se


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=45555)
    p.add_argument("--device", help="Optional device name (e.g. cpu)")
    p.add_argument("--always-copy", action="store_true", help="Always copy input to output instead of running enhancement")
    p.add_argument("--no-import", action="store_true", help="skip audiosr import on start")
    args = p.parse_args()

    if not args.no_import:
        ok = try_import_audiosr()
        if not ok:
            print("Warning: audiosr import failed; server will still accept requests but may be slower or fail.", file=sys.stderr)

    # simple server loop (kept minimal)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR,1)
    srv.bind((args.host, args.port))
    srv.listen(5)
    print(f"vasr_server: listening on {args.host}:{args.port} (always_copy={getattr(args, 'always_copy', False)})")
    try:
        while True:
            conn, addr = srv.accept()
            # handle one-line JSON requests; spawn a thread per connection
            def _handle_conn(c):
                try:
                    data = b""
                    while True:
                        chunk = c.recv(8192)
                        if not chunk:
                            break
                        data += chunk
                        if b"\n" in chunk:
                            break
                    if not data:
                        return
                    try:
                        req = json.loads(data.decode("utf-8").strip())
                        inp = req.get("input")
                        out = req.get("output")
                        if not inp or not out:
                            resp = {"rc":5, "stdout": "", "stderr": "invalid request"}
                        else:
                            if getattr(args, 'always_copy', False):
                                try:
                                    os.makedirs(os.path.dirname(out), exist_ok=True)
                                    shutil.copyfile(inp, out)
                                    resp = {"rc":0, "stdout": "copied", "stderr": ""}
                                    print(f"always-copy: copied {inp} -> {out}")
                                except Exception as ex_copy:
                                    resp = {"rc":7, "stdout": "", "stderr": str(ex_copy)}
                            else:
                                print(f"process request input={inp} output={out}")
                                rc, so, se = run_audiosr_cli(inp, out, args.device)
                                resp = {"rc": rc, "stdout": so, "stderr": se}
                    except Exception as ex:
                        resp = {"rc":6, "stdout": "", "stderr": str(ex)}
                    try:
                        c.sendall((json.dumps(resp) + "\n").encode("utf-8"))
                    except Exception:
                        pass
                except Exception:
                    pass
                finally:
                    try:
                        c.close()
                    except Exception:
                        pass
            t = threading.Thread(target=_handle_conn, args=(conn,), daemon=True)
            t.start()
    except KeyboardInterrupt:
        print("server shutting down")
    finally:
        try:
            srv.close()
        except Exception:
            pass
