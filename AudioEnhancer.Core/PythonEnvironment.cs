using System;
using System.IO;

namespace AudioEnhancer.Core
{
    public static class PythonEnvironment
    {
        public static string GetEmbeddedPythonPath()
        {
            // 1. Check local (Production/Distribution)
            string localPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "pythonlib", "python.exe");
            if (File.Exists(localPath)) return localPath;

            // 2. Check Dev/Project structure (search up to 5 levels)
            string currentDir = AppDomain.CurrentDomain.BaseDirectory;
            for (int i = 0; i < 5; i++)
            {
                var dirInfo = Directory.GetParent(currentDir);
                if (dirInfo == null) break;
                currentDir = dirInfo.FullName;
                
                // Check sibling "pythonlib"
                string candidate = Path.Combine(currentDir, "pythonlib", "python.exe");
                if (File.Exists(candidate)) return candidate;
            }

            // 3. Hardcoded fallback for this specific workspace (absolute fail-safe)
            string absolute = @"d:\programme\programming\projekte\pythonlib\python.exe";
            if (File.Exists(absolute)) return absolute;

            return localPath; // Return standard path (even if missing) so IsAvailable false works
        }

        public static bool IsAvailable()
        {
            return File.Exists(GetEmbeddedPythonPath());
        }
    }
}
