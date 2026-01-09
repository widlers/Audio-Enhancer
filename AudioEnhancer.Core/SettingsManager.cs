using System;
using System.IO;
using System.Text.Json;

namespace AudioEnhancer.Core
{
    public class AppSettings
    {
        public string FfmpegPath { get; set; } = "ffmpeg";
        public string PythonPath { get; set; } = "python"; // Can be conda exe
        public string CondaEnvName { get; set; } = "vasr-cuda13";
        public bool UseConda { get; set; } = true;
        public bool UseGpu { get; set; } = true;
        public double ChunkSizeSeconds { get; set; } = 30.0;
        public string DefaultOutputDir { get; set; } = "";
    }

    public static class SettingsManager
    {
        private static string SettingsPath => Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "settings.json");

        public static AppSettings Load()
        {
            if (File.Exists(SettingsPath))
            {
                try
                {
                    string json = File.ReadAllText(SettingsPath);
                    return JsonSerializer.Deserialize<AppSettings>(json) ?? new AppSettings();
                }
                catch { }
            }
            return new AppSettings();
        }

        public static void Save(AppSettings settings)
        {
            try
            {
                string json = JsonSerializer.Serialize(settings, new JsonSerializerOptions { WriteIndented = true });
                File.WriteAllText(SettingsPath, json);
            }
            catch { }
        }
    }
}
