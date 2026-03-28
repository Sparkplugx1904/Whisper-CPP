import os
import re
import json
import argparse
from google import genai
from pydantic import BaseModel
from typing import List

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

def parse_model_score(name: str) -> tuple:
    """
    Ekstrak versi numerik dari nama model.
    Hanya model 'flash' yang akan dipertimbangkan.
    Contoh:
      'gemini-3.0-flash' -> (3, 0)
      'gemini-2.5-flash' -> (2, 5)
    """
    match = re.search(r'gemini-(\d+)(?:\.(\d+))?-flash', name.lower())
    if match:
        major = int(match.group(1))
        minor = int(match.group(2)) if match.group(2) else 0
        return (major, minor)
    return (-1, -1)


def get_latest_flash_model(client):
    """
    Pilih model Gemini Flash terbaru berdasarkan versi tertinggi.
    Kriteria valid: mengandung 'flash', punya nomor versi,
    bukan exp/lite/preview/tts/vision/audio/dll.
    """
    try:
        available_models = list(client.models.list())

        valid_models = []
        for m in available_models:
            n = m.name.lower()
            if any(x in n for x in ['exp', 'lite', 'preview', 'tts', 'audio', 'vision', 'embedding']):
                continue
            
            score = parse_model_score(m.name)
            if score[0] >= 0:
                valid_models.append(m.name)

        if valid_models:
            valid_models.sort(key=parse_model_score, reverse=True)
            best_model = valid_models[0]
            if best_model.startswith("models/"):
                best_model = best_model.split("/", 1)[1]
            print(f"[INFO] Auto-select model berhasil. Menggunakan: {best_model}")
            return best_model

    except Exception as e:
        print(f"[WARNING] Gagal mengambil daftar model dinamis: {e}")

    print("[INFO] Menggunakan model fallback default (gemini-3.0-flash).")
    return "gemini-3.0-flash"


def resolve_output_path(output_arg: str) -> str:
    """
    -o selalu diperlakukan sebagai path file tujuan secara langsung.
    Direktori induknya dibuat otomatis jika belum ada.
    """
    output_dir = os.path.dirname(output_arg)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    return output_arg


def generate_description(srt_file_path: str, output_arg: str):
    client = genai.Client(api_key=GEMINI_API_KEY)

    try:
        with open(srt_file_path, 'r', encoding='utf-8') as f:
            transcript_content = f.read()
    except Exception as e:
        print(f"Error membaca file: {e}")
        return

    # Schema Pydantic
    class Program(BaseModel):
        program: str
        announcer: str
        timestamp: str
        topic: str
        description: str

    class SiaranRadio(BaseModel):
        title: str
        description: str
        programs: List[Program]

    sys_instruct = """PERSONALITAS DAN PERAN:
Anda adalah arsiparis digital untuk siaran radio. Tugas Anda adalah membaca
transkrip SRT dari rekaman siaran, lalu mengekstraknya menjadi JSON metadata
yang komprehensif dan terstruktur.

FORMAT JUDUL:
Deteksi nama program siaran dan tanggal siaran secara otomatis dari isi transkrip.
Format: <Nama Siaran> Edisi: <DD MMMM YYYY>
Nama siaran diambil dari konten transkrip, bukan diasumsikan atau dikarang.
Jika nama siaran tidak disebutkan secara eksplisit, gunakan nama paling relevan
berdasarkan konteks. Contoh pola (bukan nilai tetap):
  "Voice of Trisma Edisi: 07 Maret 2026"
  "Radio Kampus XYZ Edisi: 15 April 2025"

ATURAN KONSOLIDASI PROGRAM (WAJIB DIPATUHI):
PRINSIP UTAMA:
Setiap nama program siaran HANYA boleh muncul SATU KALI di dalam array 'programs',
tidak peduli seberapa banyak sub-topik, konten, atau jeda yang ada di dalamnya.

CARA MENENTUKAN BATAS PROGRAM BARU:
[OK] Program BARU dimulai jika: nama segmen berubah ATAU penyiar secara eksplisit
  menutup satu program dan membuka program lain yang berbeda nama.
[LARANGAN] Jeda musik, pergantian sub-topik, atau selang waktu di tengah segmen BUKAN
  pertanda program baru, itu masih bagian dari program yang sama.
[LARANGAN] DILARANG membuat dua objek dengan nama 'program' yang identik.
[LARANGAN] DILARANG memecah satu program hanya karena isinya banyak atau topiknya beragam.
  Seluruh konten wajib digabung ke SATU objek.

CARA MENGGABUNGKAN KONTEN:
- Field 'timestamp'   : waktu PERTAMA kali program tersebut dibuka.
- Field 'topic'       : rangkuman singkat dari seluruh sub-topik yang dibahas.
- Field 'description' : memuat SEMUA detail tanpa terkecuali, setiap judul karya,
  nama entitas, poin informasi yang ada di transkrip untuk segmen itu.

CONTOH POLA (menggunakan nama program generik):
  SALAH : [{program: "Program A", topic: "Bagian 1"},
            {program: "Program A", topic: "Bagian 2"}]
  BENAR : [{program: "Program A", topic: "Bagian 1 dan Bagian 2",
            description: "... semua konten dari Bagian 1 dan Bagian 2 ..."}]

LOGIKA IDENTIFIKASI PENYIAR & TIMESTAMP:
1. Identifikasi Retrospektif:
   Jika penyiar belum menyebutkan namanya di awal segmen, pindai seluruh percakapan
   dalam segmen tersebut hingga nama ditemukan. Gunakan nama yang teridentifikasi.
   JANGAN isi 'Tidak Disebutkan' selama nama masih bisa dipastikan dari konteks.

2. Penentuan Timestamp Mulai:
   Ambil timestamp tepat saat penyiar pertama kali menyapa pendengar atau membuka
   segmen secara verbal. Jika penyiar langsung berbicara tanpa intro pembuka,
   gunakan timestamp baris pertama segmen tersebut.
   Catatan: beberapa penyiar mungkin memiliki pola pembuka yang konsisten antar
   episode (misalnya selalu membuka di rentang waktu yang sama). Gunakan pola itu
   sebagai petunjuk, namun tetap prioritaskan deteksi dari isi transkrip.

3. Persistensi Nama:
   Sekali penyiar teridentifikasi dalam satu segmen, nama itu berlaku untuk seluruh
   durasi segmen tersebut, meskipun ada jeda atau pergantian sub-topik.

GAYA PENULISAN (field 'description' level atas / root):
Tulis 1-2 kalimat sinopsis naratif yang RINGKAS, HANGAT, dan MENGALIR,
seperti keterangan episode di platform podcast atau streaming, bukan laporan.

ATURAN GAYA:
[OK] Gunakan sudut pandang 'insider' siaran. Sesuaikan frasa pembuka dengan nama
  siaran yang terdeteksi dari transkrip. Contoh pola:
  -> "[Nama Siaran] edisi [tanggal] hadir menemani dengan ..."
  -> "Perjalanan siaran kali ini dibuka dengan ... sebelum berlanjut ke ..."
  -> "... sebelum akhirnya ditutup dengan ..."
[OK] Ceritakan alur TRANSISI antar segmen secara natural, pembaca harus merasakan
  perjalanan siaran dari awal hingga akhir.
[OK] Variasi konten (musik, info, ulasan, dll.) cukup untuk membangun daya tarik.
[LARANGAN] "Siaran ini didominasi oleh...", "Program ini terdiri dari...",
  atau kalimat kaku bergaya laporan penelitian.
[LARANGAN] Ajakan langsung seperti "Mari dengarkan" atau "Yuk simak".

CONTOH POLA OUTPUT (nama siaran menyesuaikan transkrip, ini hanya ilustrasi):
"[Nama Siaran] edisi [tanggal] hadir menemani dengan [gambaran segmen pertama].
Perjalanan siaran berlanjut dengan [segmen berikutnya], sebelum akhirnya ditutup
oleh [segmen penutup]."

DESKRIPSI SEGMEN (field 'description' di dalam programs):
- Padat namun lengkap, cantumkan SEMUA entitas utama dari transkrip segmen itu:
  judul karya, nama orang, poin informasi penting.
- Ubah gaya bicara lisan menjadi narasi tertulis yang elegan.

KONTROL KUALITAS UMUM:
- Gunakan Bahasa Indonesia baku (EYD), lugas, dan hidup.
- DILARANG menambahkan detail yang tidak ada di transkrip (tidak boleh mengarang
  genre, tahun rilis, atau fakta eksternal lainnya).
- DILARANG menghilangkan entitas apapun yang disebutkan di dalam transkrip."""

    print("Sedang memproses transkrip...")

    try:
        selected_model = get_latest_flash_model(client)
        print(f"Model: {selected_model}\n")

        response = client.models.generate_content(
            model=selected_model,
            contents=f"Ekstrak data siaran dari transkrip SRT ini:\n\n{transcript_content}",
            config={
                "system_instruction": sys_instruct,
                "response_mime_type": "application/json",
                "response_schema": SiaranRadio,
            }
        )

        output_data = response.parsed.model_dump()

        output_file_path = resolve_output_path(output_arg)

        with open(output_file_path, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)

        print(f"Berhasil!\nFile JSON telah disimpan di: {output_file_path}")

    except Exception as e:
        print(f"Terjadi kesalahan: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate description JSON from SRT transcript.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "srt_path",
        help="Path ke file SRT transkrip"
    )
    parser.add_argument(
        "-o", "--output",
        default="output.json",
        help=(
            "Path file output JSON.\n"
            "Contoh: -o transcripts/description.json\n"
            "Folder induk akan dibuat otomatis jika belum ada.\n"
            "Default: output.json (di folder saat ini)"
        )
    )

    args = parser.parse_args()
    generate_description(args.srt_path, args.output)