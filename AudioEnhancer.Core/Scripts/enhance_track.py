import sys
import os
import warnings
import torch
import soundfile as sf
import numpy as np
import tempfile
import gc

# Warnungen unterdrücken
warnings.filterwarnings("ignore")
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

# MONKEY PATCH: Fix for 'numpy has no attribute float' in older libraries
if not hasattr(np, 'float'):
    np.float = float

# OPTIMIZATION: Use all CPU cores
if "OMP_NUM_THREADS" not in os.environ:
    os.environ["OMP_NUM_THREADS"] = str(os.cpu_count())
    
try:
    import torch
    import torchaudio
    import soundfile as sf
    
    # MONKEY PATCH: torchaudio.load is broken in this nightly build (requires missing torchcodec/ffmpeg)
    # We replace it with a wrapper around soundfile
    def patched_load(filepath, **kwargs):
        # soundfile reads (frames, channels), torchaudio expects (channels, frames)
        data, sr = sf.read(filepath, dtype='float32')
        tensor = torch.from_numpy(data)
        if tensor.ndim == 1:
            tensor = tensor.unsqueeze(0) # (1, frames)
        else:
            tensor = tensor.t() # (channels, frames)
        return tensor, sr
        
    torchaudio.load = patched_load

    torch.set_num_threads(os.cpu_count())
    from audiosr import super_resolution, build_model
except ImportError:
    print("ERROR: audiosr library not found", file=sys.stderr)
    sys.exit(1)

def cleanup_memory():
    """Zwingt Python und PyTorch, Speicher freizugeben."""
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

def process_channel_data(model, data_channel, sr, channel_name="Mono", chunk_duration=5.0):
    """Verarbeitet einen Kanal in Häppchen (Salami-Taktik)"""
    
    CHUNK_DURATION = chunk_duration 
    chunk_samples = int(CHUNK_DURATION * sr)
    total_samples = len(data_channel)
    processed_parts = []
    
    TARGET_SR = 48000
    
    print(f"--> Verarbeite Kanal '{channel_name}' ({total_samples/sr:.1f}s)...", file=sys.stderr)

    for i in range(0, total_samples, chunk_samples):
        cleanup_memory() # VOR dem Chunk

        end = min(i + chunk_samples, total_samples)
        chunk = data_channel[i:end]
        
        duration_sec = len(chunk) / sr
        expected_output_samples = int(duration_sec * TARGET_SR)
        
        current_sec = i / sr
        percent = int((current_sec / (total_samples/sr)) * 100)
        print(f"    {channel_name}: Abschnitt {percent}% ({current_sec:.1f}s)...", file=sys.stderr)
        
        temp_in = os.path.join(tempfile.gettempdir(), f"chunk_{channel_name}_{i}.wav")
        sf.write(temp_in, chunk, sr)
        
        try:
            with torch.no_grad():
                waveform = super_resolution(
                    model,
                    temp_in,
                    seed=42,
                    guidance_scale=3.5,
                    ddim_steps=30, 
                    latent_t_per_second=12.8
                )
            
            res = waveform.squeeze()
            if hasattr(res, "cpu"): res = res.cpu()
            if hasattr(res, "numpy"): res = res.numpy()
            
            # Längen-Korrektur (Stille abschneiden)
            if len(res) > expected_output_samples:
                res = res[:expected_output_samples]
            elif len(res) < expected_output_samples:
                missing = expected_output_samples - len(res)
                res = np.pad(res, (0, missing))
            
            processed_parts.append(res)
            
        finally:
            if os.path.exists(temp_in): os.remove(temp_in)
            # Explizit löschen (Sauber, ohne try-catch Monster)
            del chunk
            if 'waveform' in locals(): del waveform
            if 'res' in locals(): del res
            cleanup_memory() # NACH dem Chunk

    return np.concatenate(processed_parts)

def enhance(input_file, output_file, chunk_duration=5.0):
    print(f"Initialisiere AudioSR...", file=sys.stderr)
    cleanup_memory()
    
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Nutze Hardware: {device}", file=sys.stderr)
    
    audiosr_model = build_model(model_name="basic", device=device)
    
    print(f"Lese Audio: {input_file}", file=sys.stderr)
    data, sr = sf.read(input_file)
    
    final_audio = None
    
    if data.ndim == 1:
        print(f"Modus: MONO (Chunk: {chunk_duration}s)", file=sys.stderr)
        final_audio = process_channel_data(audiosr_model, data, sr, "Mono", chunk_duration)
        
    elif data.ndim == 2 and data.shape[1] == 2:
        print(f"Modus: STEREO (Chunk: {chunk_duration}s)", file=sys.stderr)
        
        left_channel = data[:, 0]
        enhanced_left = process_channel_data(audiosr_model, left_channel, sr, "Links", chunk_duration)
        
        # Zwischenreinigen
        del left_channel
        cleanup_memory()
        
        right_channel = data[:, 1]
        enhanced_right = process_channel_data(audiosr_model, right_channel, sr, "Rechts", chunk_duration)
        
        # Längen-Check
        min_len = min(len(enhanced_left), len(enhanced_right))
        enhanced_left = enhanced_left[:min_len]
        enhanced_right = enhanced_right[:min_len]
        
        final_audio = np.column_stack((enhanced_left, enhanced_right))
        
    else:
        print(f"Modus: Mehrkanal Mixdown (Chunk: {chunk_duration}s)", file=sys.stderr)
        mixed = np.mean(data, axis=1)
        final_audio = process_channel_data(audiosr_model, mixed, sr, "Mix", chunk_duration)

    print(f"Speichere High-Res Audio: {output_file}", file=sys.stderr)
    sf.write(output_file, final_audio, 48000)
    return True

if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(1)

    in_path = sys.argv[1]
    out_path = sys.argv[2]
    
    chunk_dur = 5.0
    if len(sys.argv) >= 4:
        try:
            chunk_dur = float(sys.argv[3])
        except:
            pass

    if not os.path.exists(in_path):
        print(f"File not found: {in_path}", file=sys.stderr)
        sys.exit(1)

    try:
        success = enhance(in_path, out_path, chunk_dur)
        if success:
            print("SUCCESS")
        else:
            print("FAILED")
    except Exception as e:
        if "out of memory" in str(e).lower():
            print("CRITICAL: GPU Speicher voll!", file=sys.stderr)
        else:
            import traceback
            traceback.print_exc()
            print(f"CRITICAL ERROR: {e}", file=sys.stderr)
        sys.exit(1)
