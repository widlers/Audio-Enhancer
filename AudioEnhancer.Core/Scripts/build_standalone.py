import os
import sys
import subprocess
import shutil

# Sicherstellen, dass PyInstaller installiert ist
try:
    import PyInstaller
except ImportError:
    print("PyInstaller nicht gefunden. Installiere...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

def build():
    # Pfade
    script_dir = os.path.dirname(os.path.abspath(__file__))
    target_script = os.path.join(script_dir, "enhance_track.py")
    output_dir = os.path.join(script_dir, "dist")
    work_dir = os.path.join(script_dir, "build")

    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    if os.path.exists(work_dir):
        shutil.rmtree(work_dir)

    print(f"Baue Standalone EXE von: {target_script}")

    # PyInstaller Befehl
    # Wir nutzen --onedir (Verzeichnis), das ist schneller beim Start als --onefile
    # und einfacher zu debuggen.
    cmd = [
        "pyinstaller",
        "--noconfirm",
        "--onedir",
        "--console",  # Console anzeigen (gut für Debugging, kann man später auf --windowed ändern)
        "--name", "enhance_track",
        "--clean",
        
        # Wichtige Hidden Imports für AudioSR und Abhängigkeiten
        "--hidden-import", "scipy.special.cython_special",
        "--hidden-import", "sklearn.utils._typedefs",
        "--hidden-import", "sklearn.neighbors._partition_nodes",
        "--hidden-import", "sklearn.metrics._pairwise_distances_reduction",
        "--hidden-import", "sklearn.metrics._pairwise_distances_reduction._datasets_pair",
        "--hidden-import", "sklearn.metrics._pairwise_distances_reduction._middle_term_computer",
        "--hidden-import", "pytorch_lightning",
        "--hidden-import", "huggingface_hub",
        "--hidden-import", "librosa",
        
        # Pfad zum Skript
        target_script
    ]

    # Abhängig von der audiosr version müssen ggf. Datenfiles mitkopiert werden.
    # --collect-all ist oft sicherer, aber macht es riesig. Wir probieren erst den minimalen Ansatz.
    
    print("Führe PyInstaller aus...")
    subprocess.check_call(cmd, cwd=script_dir)

    print(f"Build abgeschlossen. Ausgabe liegt in: {output_dir}")
    print("Kopiere dist Ordner nach 'bin'...")
    
    # Optional: Kopiere das Ergebnis an einen Ort, wo die C# App es findet
    # Zum Beispiel ../../bin/Standalone
    # Das machen wir später manuell oder per CI Skript.

if __name__ == "__main__":
    build()
