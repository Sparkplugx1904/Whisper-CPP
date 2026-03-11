import os
import sys
import json
from google import genai
from pydantic import BaseModel
from typing import List

# === GLOBAL VARIABLE ===
def generate_description(srt_file_path):
    # Inisialisasi Client SDK baru
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    # Membaca isi file srt
    try:
        with open(srt_file_path, 'r', encoding='utf-8') as file:
            transcript_content = file.read()
    except Exception as e:
        print(f"Error membaca file: {e}")
        return

    # 1. Definisi Schema menggunakan Pydantic (Standar SDK Baru)
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

    # 2. Instruksi Sistem
    sys_instruct = (
        "PERSONALITAS DAN PERAN:\n"
        "Format Judul: Voice of Trisma Edisi: <tanggal(dalam format DD MMMM YYYY)>\n"
        "Contoh: Voice of Trisma Edisi: 07 Maret 2026\n"
        "Anda adalah sistem pakar analisis media dan arsiparis digital untuk Voice of Trisma (Radio SMAN 3 Denpasar). "
        "Tugas utama Anda adalah mengekstraksi metadata siaran dari transkrip SRT ke dalam format JSON yang sangat terstruktur.\n\n"
        
        "LOGIKA TIMESTAMP DAN IDENTIFIKASI PENYIAR (SANGAT PENTING):\n"
        "1. Prioritas Timestamp Verbal: Titik awal (timestamp) sebuah program harus diambil tepat pada baris SRT di mana penyiar memperkenalkan namanya secara verbal (perkenalan diri), BUKAN saat musik intro atau SFX dimulai.\n"
        "2. Kasus Khusus Citra: Berdasarkan koreksi historis, penyiar 'Citra' memulai segmen 'Kilas Trisma' pada timestamp 00:34:29,000 saat ia menyebutkan namanya. Jangan gunakan timestamp musik sebelumnya.\n"
        "3. Diskriminasi Entitas (Penyiar vs Narasumber): \n"
        "   - Penyiar: Orang yang memandu acara, menyapa 'Sobat Trisma', menyebutkan nama program, dan mengarahkan alur (Contoh: Getas, Citra, Bulan, Kanta).\n"
        "   - Narasumber/Subjek: Orang atau tokoh yang dibahas dalam materi (Contoh: Salma Salsabil, Dee Lestari, sutradara film, atau tokoh sejarah). JANGAN masukkan narasumber ke dalam field 'announcer'.\n"
        "4. Deteksi Transisi: Abaikan baris SRT berisi instruksi teknis seperti [musik], [suara api], atau [lirik] sebagai titik mulai program.\n\n"
        
        "KONTROL KUALITAS KONTEN & GAYA PENULISAN (SINOPSIS):\n"
        "1. Deskripsi Utama (Sinopsis Naratif): Field 'description' pada root JSON WAJIB ditulis dalam gaya SINOPSIS NARATIF yang mengalir (1-2 paragraf). JANGAN gunakan format daftar (bullet points).\n"
        "2. Efisiensi Kata (TANPA FLUFF): Tulis dengan gaya yang padat dan langsung ke inti (to the point). HINDARI kalimat pengantar yang bertele-tele, klise, atau berbunga-bunga seperti 'menyajikan rangkaian siaran yang dinamis dan informatif'. Langsung deskripsikan isi programnya.\n"
        "   - Contoh Benar: 'Voice of Trisma edisi 7 Maret 2026 mengawali siarannya dengan ulasan Tangga Lagu Indonesia, dilanjutkan dengan pembaruan kegiatan akademik kelas 12 dalam segmen Kilas Trisma.'\n"
        "3. Deskripsi Segmen: Field 'description' di dalam array 'programs' harus berupa narasi singkat (1-2 kalimat padat) yang menjelaskan secara spesifik topik dan poin utama yang dibahas pada segmen tersebut.\n"
        "4. Larangan Halusinasi: Jangan menambahkan informasi eksternal yang tidak disebutkan dalam transkrip.\n"
        "5. Struktur Tetap: Output harus murni JSON tanpa markdown tambahan.\n\n"
        
        "PANDUAN BAHASA:\n"
        "Seluruh teks wajib menggunakan Bahasa Indonesia standar (EYD) yang profesional dan baku. Ubah bahasa lisan di transkrip menjadi bahasa tulisan jurnalistik atau arsip yang lugas dan tidak membuang-buang kata."
    )

    print("Sedang memproses transkrip...")

    try:
        # 3. Request ke Model Gemini 1.5 Flash
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite-preview",
            contents=f"Ekstrak data siaran dari transkrip SRT ini:\n\n{transcript_content}",
            config={
                "system_instruction": sys_instruct,
                "response_mime_type": "application/json",
                "response_schema": SiaranRadio,
            }
        )

        # 4. Ambil output dan simpan
        # SDK baru mengembalikan objek yang bisa langsung diakses atau dikonversi ke dict
        output_data = response.parsed.model_dump()
        
        with open('description.json', 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)
            
        print("Berhasil! File 'description.json' telah diperbarui menggunakan SDK terbaru.")

    except Exception as e:
        print(f"Terjadi kesalahan: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Cara penggunaan: python description_generator.py transcript.srt")
    else:
        file_path = sys.argv[1]
        generate_description(file_path)