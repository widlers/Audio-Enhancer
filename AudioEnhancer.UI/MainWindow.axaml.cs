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

            AppendLog("Application started. Ready.");
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
            if (string.IsNullOrEmpty(_currentFile) || !File.Exists(_currentFile))
            {
                AppendLog("Error: No valid file selected.");
                return;
            }

            BtnStart.IsEnabled = false;
            ProgressBar.IsVisible = true;
            ProgressBar.Value = 0;
            _cts = new CancellationTokenSource();

            // Load Settings
            var settings = SettingsManager.Load();

            // Validate Paths?
            AppendLog($"Settings Loaded: Python={settings.PythonPath}, Env={settings.CondaEnvName}");

            // Re-Init Manager with correct paths
            _manager = new AudioEnhancerManager(settings.PythonPath, settings.FfmpegPath);
            // Re-wire events (create new or refactor manager to simple properties)
            // Ideally Manager properties are mutable or we just set them.
            // Let's modify Manager to allow property update in next step or just re-wire.
            // For safety, re-wire:
            _manager.LogMessage += msg => Dispatcher.UIThread.Post(() => AppendLog(msg));
            _manager.ProgressChanged += (msg, pct) => Dispatcher.UIThread.Post(() => UpdateProgress(msg, pct));

            // Config
            _manager.CondaEnvName = settings.CondaEnvName;
            _manager.UseCondaRun = ChkConda.IsChecked ?? true;
            _manager.EnhancerDevice = (ChkGpu.IsChecked ?? true) ? "cuda" : "cpu";
            _manager.ChunkDuration = settings.ChunkSizeSeconds;


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