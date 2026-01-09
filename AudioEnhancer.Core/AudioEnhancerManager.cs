using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Collections.Generic;
using System.Net.Sockets;

namespace AudioEnhancer.Core
{
    public class AudioEnhancerManager
    {
        private readonly string _pythonPath;
        private readonly string _ffmpegPath;
        private readonly string _scriptsDir;

        // Configuration
        public string CondaEnvName { get; set; } = "vasr-cuda13";
        public bool UseCondaRun { get; set; } = true;
        public string EnhancerDevice { get; set; } // "cuda" or "cpu"
        public double ChunkDuration { get; set; } = 30.0;
        public bool AlwaysCopy { get; set; } = false;

        public event Action<string>? LogMessage;
        public event Action<string, int>? ProgressChanged; // message, percent

        public AudioEnhancerManager(string pythonPath, string ffmpegPath, string scriptsDir = null)
        {
            _pythonPath = pythonPath;
            _ffmpegPath = ffmpegPath;
            if (string.IsNullOrEmpty(scriptsDir))
            {
                _scriptsDir = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "Scripts");
            }
            else
            {
                _scriptsDir = scriptsDir;
            }
        }

        private void Log(string msg) => LogMessage?.Invoke(msg);

        public async Task<bool> ProcessFileAsync(string inputFile, string outputFile, CancellationToken ct)
        {
            if (!File.Exists(inputFile))
            {
                Log($"Input file not found: {inputFile}");
                return false;
            }

            string tmpWav = Path.Combine(Path.GetTempPath(), Guid.NewGuid() + ".wav");
            string enhancedWav = Path.Combine(Path.GetTempPath(), Guid.NewGuid() + ".enhanced.wav");

            try
            {
                // 1. Convert to WAV (ffmpeg)
                ProgressChanged?.Invoke("Converting to WAV...", 0);
                bool wavOk = await RunFfmpegAsync(inputFile, tmpWav, ct);
                if (!wavOk) return false;

                // 2. Enhance (Python)
                ProgressChanged?.Invoke("Enhancing audio (AI)...", 10);
                bool aiOk = await RunEnhancerAsync(tmpWav, enhancedWav, ct);

                // Fallback if AI fails but we want to proceed? No, usually fail.
                // But logic in FrmEnhanceTest did fallback copy.
                if (!aiOk)
                {
                    Log("Enhancement failed.");
                    return false;
                }

                // 3. Convert to Output (FLAC/MP3/Original) - assuming FLAC for now as per FrmEnhanceTest
                ProgressChanged?.Invoke("Finalizing...", 90);
                bool finalOk = await RunFfmpegAsync(enhancedWav, outputFile, ct);

                ProgressChanged?.Invoke("Done!", 100);
                return finalOk;
            }
            finally
            {
                // Cleanup
                try { if (File.Exists(tmpWav)) File.Delete(tmpWav); } catch { }
                try { if (File.Exists(enhancedWav)) File.Delete(enhancedWav); } catch { }
            }
        }

        private async Task<bool> RunFfmpegAsync(string input, string output, CancellationToken ct)
        {
            // Simple conversions suitable for temp files
            var args = new[] { "-y", "-i", input, output };
            // If converting to FLAC, add compression
            if (output.EndsWith(".flac"))
                args = new[] { "-y", "-i", input, "-compression_level", "8", output };
            else if (output.EndsWith(".wav"))
                args = new[] { "-y", "-i", input, "-ar", "44100", "-ac", "2", output };

            var (ok, stdout, stderr) = await RunProcessWithArgumentListAsync(_ffmpegPath, args, TimeSpan.FromMinutes(5), ct);
            if (!ok)
            {
                Log($"FFmpeg Error: {stderr}");
            }
            return ok;
        }

        private async Task<bool> RunEnhancerAsync(string inputWav, string outputWav, CancellationToken ct)
        {
            // SWITCHED TO enhance_track.py for Splitting & Memory Management
            string wrapperScript = Path.Combine(_scriptsDir, "enhance_track.py");
            if (!File.Exists(wrapperScript))
            {
                Log($"Script missing: {wrapperScript}");
                return false;
            }

            // Fallback to CLI
            Log("Starting robust enhancement (Splitting 60s & Memory Mgmt)...");

            // Construct Process
            string exe = "python";
            var args = new List<string>();

            // Check if the provided path is explicitly a python executable
            bool isExplicitPython = !string.IsNullOrEmpty(_pythonPath) &&
                                    (Path.GetFileName(_pythonPath).Equals("python.exe", StringComparison.OrdinalIgnoreCase) ||
                                     Path.GetFileName(_pythonPath).Equals("python", StringComparison.OrdinalIgnoreCase));

            // Check if it's explicitly conda
            bool isExplicitConda = !string.IsNullOrEmpty(_pythonPath) &&
                                   (Path.GetFileName(_pythonPath).Equals("conda.exe", StringComparison.OrdinalIgnoreCase) ||
                                    Path.GetFileName(_pythonPath).Equals("conda", StringComparison.OrdinalIgnoreCase));


            if (UseCondaRun && !isExplicitPython)
            {
                exe = "conda"; // Default to PATH
                if (isExplicitConda) exe = _pythonPath;

                args.Add("run");
                args.Add("-n");
                args.Add(CondaEnvName);
                args.Add("--no-capture-output");
                args.Add("python");
                args.Add(wrapperScript);
                args.Add(inputWav);
                args.Add(outputWav);
                args.Add(ChunkDuration.ToString("F1", System.Globalization.CultureInfo.InvariantCulture));
            }
            else
            {
                if (!string.IsNullOrEmpty(_pythonPath)) exe = _pythonPath;
                args.Add(wrapperScript);
                args.Add(inputWav);
                args.Add(outputWav);
                args.Add(ChunkDuration.ToString("F1", System.Globalization.CultureInfo.InvariantCulture));
            }

            var (ok, outS, errS) = await RunProcessWithArgumentListAsync(exe, args.ToArray(), TimeSpan.FromMinutes(60), ct,
               envVars: new Dictionary<string, string> { { "AUDIO_SR_DEVICE", EnhancerDevice ?? "" } }
            );

            if (!ok) Log($"Enhancer Error: {errS}");
            return ok && File.Exists(outputWav);
        }

        private async Task<bool> TryEnhanceViaServer(string wrapperScript, string input, string output, CancellationToken ct)
        {
            return false; // Not supported by enhance_track.py
        }

        // --- Process Helper ---
        private async Task<(bool Ok, string StdOut, string StdErr)> RunProcessWithArgumentListAsync(
            string exe, string[] args, TimeSpan timeout, CancellationToken ct,
            Dictionary<string, string>? envVars = null)
        {
            try
            {
                using var proc = new Process();
                proc.StartInfo.FileName = exe;
                proc.StartInfo.CreateNoWindow = true;
                proc.StartInfo.UseShellExecute = false;
                proc.StartInfo.RedirectStandardOutput = true;
                proc.StartInfo.RedirectStandardError = true;

                if (envVars != null)
                {
                    foreach (var kv in envVars) proc.StartInfo.EnvironmentVariables[kv.Key] = kv.Value;
                }

                foreach (var a in args) proc.StartInfo.ArgumentList.Add(a);

                var sbOut = new StringBuilder();
                var sbErr = new StringBuilder();

                // Regex for "Abschnitt 45%..."
                var regexProgress = new Regex(@"Abschnitt\s+(\d+)%", RegexOptions.Compiled);

                proc.OutputDataReceived += (s, e) =>
                {
                    if (e.Data != null)
                    {
                        sbOut.AppendLine(e.Data);
                        Log($"[STDOUT] {e.Data}");
                    }
                };
                proc.ErrorDataReceived += (s, e) =>
                {
                    if (e.Data != null)
                    {
                        sbErr.AppendLine(e.Data);

                        // Filter noisy logs from UI
                        bool isNoisy = e.Data.Contains("DDIM Sampler") || e.Data.Contains("|#");
                        if (!isNoisy)
                        {
                            Log($"[STDERR] {e.Data}");
                        }

                        // Progress parsing (enhance_track.py writes to stderr)
                        var match = regexProgress.Match(e.Data);
                        if (match.Success && int.TryParse(match.Groups[1].Value, out int pct))
                        {
                            ProgressChanged?.Invoke($"Enhancing... {pct}%", pct);
                        }
                    }
                };

                proc.Start();
                proc.BeginOutputReadLine();
                proc.BeginErrorReadLine();

                // Register cancellation to kill process
                using (ct.Register(() =>
                {
                    try { if (!proc.HasExited) proc.Kill(true); } catch { }
                }))
                {
                    await proc.WaitForExitAsync(ct).WaitAsync(timeout, ct);
                }

                return (proc.ExitCode == 0, sbOut.ToString(), sbErr.ToString());
            }
            catch (Exception ex)
            {
                Log($"Process Exception: {ex.Message}");
                return (false, "", ex.Message);
            }
        }
    }
}
