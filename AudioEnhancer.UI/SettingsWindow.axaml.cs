using Avalonia.Controls;
using Avalonia.Interactivity;
using Avalonia.Platform.Storage;
using AudioEnhancer.Core;
using System.Collections.Generic;
using System;

namespace AudioEnhancer.UI
{
    public partial class SettingsWindow : Window
    {
        public SettingsWindow()
        {
            InitializeComponent();

            // Load
            var settings = SettingsManager.Load();
            TxtFfmpeg.Text = settings.FfmpegPath;
            TxtPython.Text = settings.PythonPath;
            TxtOutput.Text = settings.DefaultOutputDir;
            TxtEnvName.Text = settings.CondaEnvName;
            TxtChunkSize.Text = settings.ChunkSizeSeconds.ToString("F1", System.Globalization.CultureInfo.InvariantCulture);

            BtnBrowseFfmpeg.Click += async (s, e) =>
            {
                var files = await this.StorageProvider.OpenFilePickerAsync(new FilePickerOpenOptions { Title = "Select FFmpeg.exe", AllowMultiple = false });
                if (files.Count > 0) TxtFfmpeg.Text = files[0].Path.LocalPath;
            };

            BtnBrowsePython.Click += async (s, e) =>
            {
                var files = await this.StorageProvider.OpenFilePickerAsync(new FilePickerOpenOptions { Title = "Select Python/Conda exe", AllowMultiple = false });
                if (files.Count > 0) TxtPython.Text = files[0].Path.LocalPath;
            };

            BtnBrowseOutput.Click += async (s, e) =>
            {
                var folders = await this.StorageProvider.OpenFolderPickerAsync(new FolderPickerOpenOptions { Title = "Select Output Folder" });
                if (folders.Count > 0) TxtOutput.Text = folders[0].Path.LocalPath;
            };

            BtnSave.Click += (s, e) =>
            {
                settings.FfmpegPath = TxtFfmpeg.Text ?? "";
                settings.PythonPath = TxtPython.Text ?? "";
                settings.DefaultOutputDir = TxtOutput.Text ?? "";
                settings.CondaEnvName = TxtEnvName.Text ?? "vasr-cuda13";

                if (double.TryParse(TxtChunkSize.Text, System.Globalization.NumberStyles.Any, System.Globalization.CultureInfo.InvariantCulture, out double dur))
                {
                    settings.ChunkSizeSeconds = dur;
                    if (settings.ChunkSizeSeconds < 1.0) settings.ChunkSizeSeconds = 5.0; // Minimal-Schutz
                }

                SettingsManager.Save(settings);
                Close();
            };
        }
    }
}
