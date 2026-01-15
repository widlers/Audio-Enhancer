using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Interactivity;
using Avalonia.Threading;
using AudioEnhancer.Core;
using System;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using Avalonia.Platform.Storage;
using MsBox.Avalonia;

namespace AudioEnhancer.UI
{
    public partial class MainWindow : Window
    {
        private AudioEnhancerManager _manager;
        private string _currentFile;
        private CancellationTokenSource _cts;

        public MainWindow()
        {
            InitializeComponent();

            // Setup events
            AddHandler(DragDrop.DropEvent, OnDrop);
            AddHandler(DragDrop.DragOverEvent, OnDragOver);
            BtnSelectFile.Click += BtnSelectFile_Click;
            BtnStart.Click += BtnStart_Click;

            BtnSettings.Click += (s, e) =>
            {
                var dlg = new SettingsWindow();
                dlg.ShowDialog(this);
            };

            // Init Manager with placeholder (will be refreshed on Start)
            _manager = new AudioEnhancerManager("python", "ffmpeg");
            _manager.LogMessage += msg => Dispatcher.UIThread.Post(() => AppendLog(msg));
            _manager.ProgressChanged += (msg, pct) => Dispatcher.UIThread.Post(() => UpdateProgress(msg, pct));

            // Trigger startup check
            this.Opened += async (s, e) => await CheckAndDownloadPythonAsync();
        }

        private async Task CheckAndDownloadPythonAsync()
        {
            // Only prompt if not available AND not using conda fallback (or prefer portable)
            if (!PythonEnvironment.IsAvailable())
            {
                var result = await MessageBoxManager.GetMessageBoxStandard(
                    "Python Components Missing",
                    "The required AI components (Python + PyTorch) are missing.\nDo you want to download them now? (~3 GB)",
                    MsBox.Avalonia.Enums.ButtonEnum.YesNo).ShowAsync();

                if (result == MsBox.Avalonia.Enums.ButtonResult.Yes)
                {
                    await PerformDownloadAsync();
                }
            }
        }

        private async Task PerformDownloadAsync()
        {
            var installer = new PythonInstaller();
            installer.StatusChanged += (s, msg) => Dispatcher.UIThread.Post(() => AppendLog(msg));
            installer.ProgressChanged += (s, pct) => Dispatcher.UIThread.Post(() =>
            {
                ProgressBar.IsVisible = true;
                ProgressBar.Value = pct;
                TxtStatus.Text = $"Downloading... {pct:F0}%";
            });

            BtnStart.IsEnabled = false;
            try
            {
                bool success = await installer.InstallAsync();
                if (success)
                {
                    AppendLog("Installation complete. Restarting environment check...");
                    TxtStatus.Text = "Installation Complete.";
                    // Force refresh
                    BtnStart_Click(null, null);
                }
                else
                {
                    AppendLog("Installation failed.");
                    TxtStatus.Text = "Error during installation.";
                }
            }
            finally
            {
                BtnStart.IsEnabled = true;
                ProgressBar.IsVisible = false;
            }
        }

        protected override void OnClosing(WindowClosingEventArgs e)
        {
            if (_cts != null)
            {
                _cts.Cancel();
                _cts.Dispose();
                _cts = null;
            }
            base.OnClosing(e);
        }

        private void OnDragOver(object sender, DragEventArgs e)
        {
            if (e.Data.Contains(DataFormats.Files))
                e.DragEffects = DragDropEffects.Copy;
            else
                e.DragEffects = DragDropEffects.None;
        }

        private void OnDrop(object sender, DragEventArgs e)
        {
            if (e.Data.Contains(DataFormats.Files))
            {
                var files = e.Data.GetFiles();
                foreach (var f in files)
                {
                    // Take first file
                    string path = Uri.UnescapeDataString(f.Path.AbsolutePath);
                    // Standardize path (remove file:///)
                    if (f.Path.IsAbsoluteUri) path = f.Path.LocalPath;

                    SetFile(path);
                    break;
                }
            }
        }

        private async void BtnSelectFile_Click(object sender, RoutedEventArgs e)
        {
            var topLevel = TopLevel.GetTopLevel(this);
            var files = await topLevel.StorageProvider.OpenFilePickerAsync(new FilePickerOpenOptions
            {
                Title = "Select Audio File",
                AllowMultiple = false
            });

            if (files.Count >= 1)
            {
                SetFile(files[0].Path.LocalPath);
            }
        }

        private void SetFile(string path)
        {
            _currentFile = path;
            TxtSelectedFile.Text = Path.GetFileName(path);
            TxtSelectedFile.IsVisible = true;
            BtnStart.IsEnabled = true;
            AppendLog($"Selected file: {path}");
        }

        private async void BtnStart_Click(object sender, RoutedEventArgs e)
        {
            // If sender is null (manual refresh), skip file check
            if (sender != null && (string.IsNullOrEmpty(_currentFile) || !File.Exists(_currentFile)))
            {
                AppendLog("Error: No valid file selected.");
                return;
            }

            // ... (rest of logic)

            BtnStart.IsEnabled = false;
            ProgressBar.IsVisible = true;
            ProgressBar.Value = 0;
            _cts = new CancellationTokenSource();

            // Load Settings
            var settings = SettingsManager.Load();

            // Validate Paths?
            AppendLog($"Settings Loaded: Python={settings.PythonPath}, Env={settings.CondaEnvName}");


            // CHECK FOR PORTABLE PYTHON
            if (PythonEnvironment.IsAvailable())
            {
                string portablePy = PythonEnvironment.GetEmbeddedPythonPath();
                AppendLog($"[Portable] Detected embedded python at: {portablePy}");
                settings.PythonPath = portablePy;
                _manager.UseCondaRun = false; // Disable conda for portable
                ChkConda.IsChecked = false;   // Visual update
                TxtStatus.Text = "Using Portable Python";
            }
            else
            {
                _manager.UseCondaRun = ChkConda.IsChecked ?? true;
            }

            // Stop here if we don't have a file (this was just a refresh)
            if (string.IsNullOrEmpty(_currentFile))
            {
                BtnStart.IsEnabled = false; // Keep disabled until file selection
                return;
            }

            // Re-Init Manager with correct paths (potentially updated above)
            _manager = new AudioEnhancerManager(settings.PythonPath, settings.FfmpegPath);
            _manager.LogMessage += msg => Dispatcher.UIThread.Post(() => AppendLog(msg));
            _manager.ProgressChanged += (msg, pct) => Dispatcher.UIThread.Post(() => UpdateProgress(msg, pct));

            // Config
            _manager.CondaEnvName = settings.CondaEnvName;
            _manager.UseCondaRun = !PythonEnvironment.IsAvailable() && (ChkConda.IsChecked ?? true); // Force false if portable
            _manager.EnhancerDevice = (ChkGpu.IsChecked ?? true) ? "cuda" : "cpu";
            _manager.ChunkDuration = settings.ChunkSizeSeconds;
            _manager.NormalizeAudio = settings.NormalizeAudio;

            string dir = Path.GetDirectoryName(_currentFile);
            if (!string.IsNullOrEmpty(settings.DefaultOutputDir) && Directory.Exists(settings.DefaultOutputDir))
            {
                dir = settings.DefaultOutputDir;
            }

            string name = Path.GetFileNameWithoutExtension(_currentFile);
            string output = Path.Combine(dir, $"{name}_enhanced.flac");

            try
            {
                AppendLog("Starting process...");
                bool success = await Task.Run(() => _manager.ProcessFileAsync(_currentFile, output, _cts.Token));

                if (success)
                {
                    AppendLog($"SUCCESS! Output: {output}");
                    TxtStatus.Text = "Completed successfully.";
                    ProgressBar.Value = 100;
                }
                else
                {
                    AppendLog("FAILED. Check logs.");
                    TxtStatus.Text = "Failed.";
                    ProgressBar.Value = 0;
                }
            }
            catch (Exception ex)
            {
                AppendLog($"Exception: {ex.Message}");
            }
            finally
            {
                BtnStart.IsEnabled = true;
                _cts = null;
            }
        }

        private void UpdateProgress(string msg, int pct)
        {
            TxtStatus.Text = msg;
            if (pct >= 0) ProgressBar.Value = pct;
        }

        private void AppendLog(string msg)
        {
            TxtLog.Text += $"[{DateTime.Now:HH:mm:ss}] {msg}\n";
            // Auto scroll usually requires caret movement
            TxtLog.CaretIndex = TxtLog.Text.Length;
        }
    }
}