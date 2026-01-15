using System;
using System.IO;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;

namespace AudioEnhancer.Core
{
    public class PythonInstaller
    {
        // Pixeldrain Direct API URL
        // https://pixeldrain.com/api/file/ID
        private const string FileId = "cn71ZPJc";
        private const string PythonLibUrl = "https://pixeldrain.com/api/file/" + FileId;

        public event EventHandler<double> ProgressChanged;
        public event EventHandler<string> StatusChanged;

        public async Task<bool> InstallAsync(CancellationToken ct = default)
        {
            try
            {
                string destinationDir = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "pythonlib");
                string tempFile = Path.Combine(Path.GetTempPath(), "pythonlib_setup.zip");

                // 1. Download
                StatusChanged?.Invoke(this, "Downloading components from Pixeldrain (3GB)...");
                await DownloadFileAsync(PythonLibUrl, tempFile, ct);

                // Validation: Check if we actually got a large file
                var info = new FileInfo(tempFile);
                if (info.Length < 100 * 1024 * 1024) // < 100MB is suspicious for a 3GB file
                {
                    throw new Exception($"Download too small ({info.Length / 1024 / 1024} MB). Link might be expired or rate-limited.");
                }

                // 2. Prepare Directory
                if (Directory.Exists(destinationDir))
                {
                    StatusChanged?.Invoke(this, "Removing old version...");
                    try { Directory.Delete(destinationDir, true); } catch { /* ignore in use errors */ }
                }

                // 3. Extract
                StatusChanged?.Invoke(this, "Extracting (this may take a while)...");
                await Task.Run(() =>
                {
                    // Clean destination
                    if (Directory.Exists(destinationDir)) Directory.Delete(destinationDir, true);

                    // Extract to a temp folder first to check structure
                    string tempExtractDir = Path.Combine(Path.GetTempPath(), "pythonlib_extract_" + Guid.NewGuid());
                    Directory.CreateDirectory(tempExtractDir);

                    try
                    {
                        System.IO.Compression.ZipFile.ExtractToDirectory(tempFile, tempExtractDir);

                        // Check if it's nested (pythonlib/pythonlib)
                        string nestedDir = Path.Combine(tempExtractDir, "pythonlib");
                        if (Directory.Exists(nestedDir))
                        {
                            // Move the inner folder to destination
                            Directory.Move(nestedDir, destinationDir);
                        }
                        else
                        {
                            // Move the whole extract dir to destination
                            Directory.Move(tempExtractDir, destinationDir);
                        }
                    }
                    finally
                    {
                        // Cleanup extract dir if it still exists (e.g. after move)
                        if (Directory.Exists(tempExtractDir)) Directory.Delete(tempExtractDir, true);
                    }
                }, ct);

                // 4. Cleanup
                File.Delete(tempFile);

                StatusChanged?.Invoke(this, "Ready!");
                return true;
            }
            catch (Exception ex)
            {
                StatusChanged?.Invoke(this, $"Error: {ex.Message}");
                return false;
            }
        }

        private async Task DownloadFileAsync(string url, string outputPath, CancellationToken ct)
        {
            // Simple robust HTTP client for Direct Download
            // Pixeldrain does not require complex cookie handling for public files
            using (HttpClient client = new HttpClient())
            {
                client.Timeout = TimeSpan.FromHours(1); // Allow long download time for 3GB
                client.DefaultRequestHeaders.Add("User-Agent", "AudioEnhancer/1.0");

                using (HttpResponseMessage response = await client.GetAsync(url, HttpCompletionOption.ResponseHeadersRead, ct))
                {
                    response.EnsureSuccessStatusCode();

                    long? totalBytes = response.Content.Headers.ContentLength;

                    using (Stream contentStream = await response.Content.ReadAsStreamAsync(ct))
                    using (FileStream fileStream = new FileStream(outputPath, FileMode.Create, FileAccess.Write, FileShare.None, 8192, true))
                    {
                        var buffer = new byte[65536]; // 64KB buffer for speed
                        long totalRead = 0;
                        int isMoreToRead = 1;
                        long lastReport = 0;

                        do
                        {
                            int read = await contentStream.ReadAsync(buffer, 0, buffer.Length, ct);
                            if (read == 0)
                            {
                                isMoreToRead = 0;
                            }
                            else
                            {
                                await fileStream.WriteAsync(buffer, 0, read, ct);

                                totalRead += read;

                                // Report every 1MB to avoid UI flooding
                                if (totalRead - lastReport > 1024 * 1024)
                                {
                                    lastReport = totalRead;
                                    if (totalBytes.HasValue)
                                    {
                                        double progress = (double)totalRead / totalBytes.Value * 100;
                                        ProgressChanged?.Invoke(this, progress);
                                    }
                                    else
                                    {
                                        // Estimate based on 3.0 GB if Content-Length is missing (rare for direct links)
                                        double fakePct = (double)totalRead / (3.0 * 1024 * 1024 * 1024) * 100;
                                        if (fakePct > 99) fakePct = 99;
                                        ProgressChanged?.Invoke(this, fakePct);
                                    }
                                }
                            }
                        }
                        while (isMoreToRead == 1);
                    }
                }
            }
        }
    }
}
