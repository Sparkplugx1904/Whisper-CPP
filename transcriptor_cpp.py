#!/usr/bin/env python3

import sys
import os
import subprocess
import re
import traceback
from pathlib import Path
from typing import List, Tuple, Optional
import argparse

try:
    import numpy as np
except ImportError as e:
    print(f"[✗✗✗] FATAL: GAGAL MENGIMPOR PUSTAKA PENTING: {e}", file=sys.stderr)
    print("[✗✗✗] FATAL: Pastikan Anda telah menjalankan 'pip install -r requirements.txt' (minimal numpy)", file=sys.stderr)
    sys.exit(1)

# --- Sistem Logging Kustom Sederhana ---

def log_info(msg):
    """Mencatat pesan informasi."""
    print(f"[+] {msg}")

def log_success(msg):
    """Mencatat pesan sukses."""
    print(f"[✓] {msg}")

def log_warn(msg):
    """Mencatat pesan peringatan."""
    print(f"[!] {msg}")

def log_error(msg, exit_app=False):
    """Mencatat pesan error. Jika exit_app=True, hentikan skrip."""
    print(f"[✗] ERROR: {msg}", file=sys.stderr)
    if exit_app:
        sys.exit(1)

# --- Konfigurasi ---
VALID_MODELS = ["tiny", "base", "small", "medium", "large-v1", "large-v2", "large-v3", "large-v3-turbo"]
DEFAULT_MODEL_NAME = "small"
# --------------------

# --- Fungsi Inti ---

def check_dependencies():
    """Memeriksa dependensi eksternal 'curl' dan 'whisper-cli'."""
    log_info("Memeriksa dependensi...")
    dependencies_ok = True
    
    if subprocess.run(['which', 'curl'], capture_output=True).returncode != 0:
        log_error("'curl' tidak ditemukan. Harap instal 'curl'.")
        dependencies_ok = False
    
    whisper_cli_path = Path("bin/whisper-cli")
    if not whisper_cli_path.exists():
        log_error(f"'{whisper_cli_path}' tidak ditemukan. Pastikan Anda telah mengompilasi whisper.cpp.")
        dependencies_ok = False
    
    if not dependencies_ok:
        log_error("Dependensi tidak lengkap. Keluar.", exit_app=True)
        
    log_success("Semua dependensi inti ditemukan.")
    return whisper_cli_path
    
def download_file(url: str, dest: Path) -> bool:
    """Mengunduh file menggunakan curl."""
    log_info(f"Mengunduh: {url} → {dest}")
    os.makedirs(dest.parent, exist_ok=True)
    try:
        subprocess.run(
            ["curl", "-f", "-L", "-o", str(dest), "-m", "600", url],
            check=True
        )
        print()
        log_success(f"Unduhan selesai: {dest}")
        return True
    except subprocess.CalledProcessError as e:
        print()
        log_error(f"Gagal mengunduh file (curl return code: {e.returncode}). URL: {url}", exit_app=False)
        if dest.exists():
            dest.unlink()
        return False
    except Exception as e:
        log_error(f"Terjadi error tak terduga saat mengunduh: {e}", exit_app=False)
        return False

def ensure_model_exists(model_name: str, custom_model_url: Optional[str]) -> Path:
    """Memastikan model GGML/GGUF ada di folder ./models/."""
    
    if custom_model_url:
        model_filename = Path(custom_model_url).name
        if not model_filename or '.' not in model_filename:
            log_error("URL model kustom tidak valid.", exit_app=True)
            
        model_path = Path(f"./models/{model_filename}")
        log_info(f"Memeriksa model kustom: {model_path}")
        
        if model_path.exists():
            log_success(f"Model kustom ditemukan: {model_path}")
            return model_path
        
        log_warn(f"Model kustom belum ada, mengunduh dari: {custom_model_url}")
        if not download_file(custom_model_url, model_path):
            log_error("Gagal mengunduh model kustom. Membatalkan.", exit_app=True)
        return model_path
        
    else:
        log_info(f"Memeriksa model standar: {model_name}")
        if model_name not in VALID_MODELS:
            log_error(f"Nama model standar tidak valid: '{model_name}'. Pilihan: {', '.join(VALID_MODELS)}", exit_app=True)

        os.makedirs("models", exist_ok=True)
        model_path = Path(f"./models/ggml-{model_name}.bin")
        
        if model_path.exists():
            log_success(f"Model ditemukan: {model_path}")
            return model_path
        
        log_warn(f"Model standar '{model_name}' belum ada, mengunduh...")
        url = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{model_name}.bin" 
        if not download_file(url, model_path):
            log_error("Gagal mengunduh model standar. Membatalkan.", exit_app=True)
            
        return model_path

def download_audio(url: str, output_path: Path):
    """Wrapper untuk mengunduh file audio."""
    log_info(f"Mengunduh audio dari: {url}")
    if not download_file(url, output_path):
        log_error("Gagal mengunduh audio. Membatalkan.", exit_app=True)
    log_success(f"Audio berhasil diunduh ke {output_path}")

def transcribe_single_audio(audio_path: Path, model_path: Path, whisper_cli_path: Path):
    """Mentranskripsi seluruh file audio tunggal menggunakan whisper.cpp CLI."""
    os.makedirs("transcripts", exist_ok=True)
    
    final_txt = Path("transcripts/transcript.txt")
    final_srt = Path("transcripts/transcript.srt")
    output_base_path_temp = audio_path.stem
    temp_txt_file = Path(output_base_path_temp).with_suffix(".txt")
    temp_srt_file = Path(output_base_path_temp).with_suffix(".srt")

    try:
        final_txt.write_text("", encoding="utf-8")
        final_srt.write_text("", encoding="utf-8")
        if temp_txt_file.exists(): temp_txt_file.unlink()
        if temp_srt_file.exists(): temp_srt_file.unlink()
    except IOError as e:
        log_error(f"Gagal membersihkan/membuat file transkrip: {e}", exit_app=True)

    log_info(f"Mentranskripsi: {audio_path.name}")
    
    cmd = [
        str(whisper_cli_path),
        "-m", str(model_path),
        "-f", str(audio_path),
        # "--temperature", "0.6",
        "-of", str(output_base_path_temp),
        "-otxt",
        "-osrt",
        "-l", "id", # Bahasa Indonesia
        "-pp" 
    ]
    
    log_info(f"Menjalankan whisper-cli...")
    
    try:
        subprocess.run(cmd, check=True, capture_output=False)
        print()
            
    except subprocess.CalledProcessError as e:
        print()
        log_error(f"whisper-cli GAGAL (return code: {e.returncode}). Proses dihentikan.", exit_app=True)
    except Exception as e:
        log_error(f"Error tak terduga saat menjalankan whisper-cli: {e}", exit_app=True)

    # Pindahkan TXT
    try:
        if temp_txt_file.exists():
            final_txt.write_text(temp_txt_file.read_text(encoding="utf-8").strip(), encoding="utf-8")
            temp_txt_file.unlink()
            log_success(f"TXT disimpan ke {final_txt}.")
        else:
            log_warn(f"File TXT output tidak ditemukan: {temp_txt_file}. Transkripsi mungkin gagal.")
    except Exception as e:
        log_error(f"Gagal memproses file TXT: {e}")

    # Pindahkan SRT
    try:
        if temp_srt_file.exists():
            final_srt.write_text(temp_srt_file.read_text(encoding="utf-8").strip(), encoding="utf-8")
            temp_srt_file.unlink()
            log_success(f"SRT disimpan ke {final_srt}.")
        else:
            log_warn(f"File SRT output tidak ditemukan: {temp_srt_file}. Transkripsi mungkin gagal.")
    except Exception as e:
        log_error(f"Gagal memproses file SRT: {e}")

    log_success("Transkripsi selesai.")

# -----------------------------------------------------
# FUNGSI MAIN
# -----------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Skrip transkripsi audio menggunakan whisper.cpp.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("source", help="URL audio atau path file lokal.")
    parser_model_group = parser.add_mutually_exclusive_group(required=False)
    parser_model_group.add_argument(
        "model", 
        nargs='?', 
        default=DEFAULT_MODEL_NAME,
        help=f"Nama model standar ({', '.join(VALID_MODELS)}). Default: {DEFAULT_MODEL_NAME}"
    )
    parser_model_group.add_argument(
        "-cm", "--custom-model", 
        help="URL lengkap ke file model GGML/GGUF kustom (.bin/.gguf)."
    )
    
    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        sys.exit(1)
        
    args = parser.parse_args()

    original_audio_path = Path("original_audio_download")
    audio_path_to_process = None
    is_source_url = not os.path.exists(args.source)
    
    try:
        whisper_cli_path = check_dependencies()
        log_info(f"Source: {args.source}")
        
        # 1. Pastikan Model Tersedia
        model_path = ensure_model_exists(args.model, args.custom_model)
        
        # 2. Penentuan Path Audio Input
        if is_source_url:
            download_audio(args.source, original_audio_path)
            audio_path_to_process = original_audio_path
        else:
            log_info(f"Menggunakan file lokal: {args.source}")
            audio_path_to_process = Path(args.source)

        # 3. Transkripsi
        transcribe_single_audio(audio_path_to_process, model_path, whisper_cli_path)
        
    except Exception as e:
        log_error(f"Terjadi error fatal yang tidak terduga: {e}", exit_app=False)
        traceback.print_exc()
        sys.exit(1)
        
    finally:
        # 4. Pembersihan
        if is_source_url and original_audio_path.exists():
            try:
                original_audio_path.unlink()
                log_info(f"Berhasil menghapus: {original_audio_path}")
            except Exception as e:
                log_warn(f"Gagal menghapus {original_audio_path}: {e}")
        
        log_success("====== PROSES SELESAI ======")
        log_info("Output akhir ada di folder ./transcripts/ (transcript.txt & transcript.srt)")

# -----------------------------------------------------
# BLOK EKSEKUSI UTAMA
# -----------------------------------------------------
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[✗✗✗] ERROR GLOBAL TIDAK TERDUGA: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)