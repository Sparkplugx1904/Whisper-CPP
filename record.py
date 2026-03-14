import requests
import socket
import subprocess
import time
import datetime
import signal
import sys
import os
import argparse
from internetarchive import upload
import threading

# --- Setup dasar ---
os.system("chmod +x ffmpeg ffprobe")

# Zona waktu WITA (UTC+8)
WITA_OFFSET = datetime.timedelta(hours=8)
WITA_TZ = datetime.timezone(WITA_OFFSET)

MY_ACCESS_KEY = os.environ.get("MY_ACCESS_KEY")
MY_SECRET_KEY = os.environ.get("MY_SECRET_KEY")

def log(msg):
    ts = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%H:%M:%S")
    print(f"\033[34m[{ts}]\033[0m {msg}", flush=True)

if not MY_ACCESS_KEY or not MY_SECRET_KEY:
    log("[ ERROR ] GitHub secrets MY_ACCESS_KEY atau MY_SECRET_KEY belum diset!")
    sys.exit(1)


def now_wita():
    return datetime.datetime.now(datetime.UTC).astimezone(WITA_TZ)


def is_past_cutoff():
    """Cek apakah sudah melewati jam 18:30 WITA"""
    now = now_wita()
    return (now.hour > 18) or (now.hour == 18 and now.minute >= 30)


def tcp_is_open(host, port, timeout=4):
    """Cek apakah port TCP terbuka — sangat ringan, tidak tercatat sebagai HTTP request"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def _pick_best_proxy(candidates, test_url, timeout=5):
    """
    Benchmark semua kandidat proxy secara paralel.
    Pilih yang TTFB-nya paling cepat — tidak peduli status HTTP-nya,
    karena tujuannya hanya mengukur kecepatan koneksi proxy itu sendiri.
    """
    results = {}
    lock    = threading.Lock()

    def _test(proxy):
        proxies = {"http": proxy, "https": proxy}
        try:
            t0 = time.monotonic()
            requests.head(test_url, proxies=proxies, timeout=timeout, allow_redirects=False)
            latency = time.monotonic() - t0
            with lock:
                results[proxy] = latency
        except Exception:
            pass  # Proxy tidak responsif, lewati

    threads = [threading.Thread(target=_test, args=(p,), daemon=True) for p in candidates]
    for t in threads: t.start()
    for t in threads: t.join(timeout=timeout + 1)

    if not results:
        return None
    best = min(results, key=results.get)
    ms   = int(results[best] * 1000)
    log(f"[ PROXY ] Terpilih: {best} ({ms}ms dari {len(results)} kandidat responsif)")
    return best


def _init_proxy(test_url):
    """
    Ambil daftar proxy gratis, benchmark semuanya paralel,
    pilih satu yang paling cepat. Proxy ini dipakai terus —
    hanya diganti kalau proxy itu sendiri yang mati.
    """
    log("[ PROXY ] Mencari proxy tercepat...")
    try:
        from fp.fp import FreeProxy
        candidates = FreeProxy(elite=True, rand=True, timeout=3).get_proxy_list(repeat=False)
        if not candidates:
            raise ValueError("Daftar proxy kosong")
    except Exception as e:
        log(f"[ PROXY ] Gagal ambil daftar proxy: {e}. Pakai direct.")
        return None

    log(f"[ PROXY ] Benchmark {len(candidates)} proxy secara paralel...")
    best = _pick_best_proxy(candidates, test_url)
    if not best:
        log("[ PROXY ] Tidak ada proxy responsif, pakai direct.")
    return best


# Proxy aktif — dipilih sekali di awal, dipakai terus sampai mati
_active_proxy = None


def http_is_ready(url, timeout=6):
    """
    Cek HTTP HEAD lewat _active_proxy.
    Proxy TIDAK diganti saat dapat 401 — itu respons valid dari server.
    Proxy baru dipilih hanya kalau proxy itu sendiri tidak bisa konek (error/timeout).

    Return: "ok" | "401" | "other:<code>" | "error"
    """
    global _active_proxy

    def _do(proxy_url):
        proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
        try:
            resp = requests.head(url, timeout=timeout, proxies=proxies, allow_redirects=True)
            if resp.status_code == 200:   return "ok"
            if resp.status_code == 401:   return "401"
            return f"other:{resp.status_code}"
        except Exception:
            return "error"

    result = _do(_active_proxy)

    # Proxy mati → cari pengganti, lalu coba sekali lagi
    if result == "error" and _active_proxy is not None:
        log(f"[ PROXY ] {_active_proxy} tidak responsif, cari pengganti...")
        _active_proxy = _init_proxy(url)
        result = _do(_active_proxy)  # None = direct jika tidak ada proxy

    return result


def wait_for_stream(url):
    """
    Monitor server setiap 5 detik via TCP socket (ringan, tidak spam HTTP).
    HTTP HEAD hanya dilakukan saat TCP berhasil, melalui proxy tetap yang sudah dipilih.
    Proxy tidak diganti saat 401 — itu info dari server, bukan kesalahan proxy.
    """
    global _active_proxy

    from urllib.parse import urlparse
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or 80

    TCP_INTERVAL  = 5    # Cek TCP setiap 5 detik
    HTTP_COOLDOWN = 120  # Setelah 401, tunggu sebelum HTTP check lagi

    # Pilih proxy terbaik sekali di sini sebelum mulai loop
    _active_proxy = _init_proxy(url)

    log(f"[ WAIT ] Memantau {host}:{port} setiap {TCP_INTERVAL}s (TCP)...")

    http_blocked_until = 0

    while True:
        if is_past_cutoff():
            log("[ STOP ] Sudah melewati 18:30 WITA saat menunggu stream, batalkan.")
            sys.exit(0)

        # --- Layer 1: TCP ---
        if not tcp_is_open(host, port):
            print(f"\r\033[34m[{now_wita().strftime('%H:%M:%S')}]\033[0m "
                  f"[ TCP ] {host}:{port} belum terbuka...   ", end="", flush=True)
            time.sleep(TCP_INTERVAL)
            continue

        # Port terbuka — cek cooldown
        now_ts = time.time()
        if now_ts < http_blocked_until:
            sisa = int(http_blocked_until - now_ts)
            print(f"\r\033[34m[{now_wita().strftime('%H:%M:%S')}]\033[0m "
                  f"[ TCP ] Port terbuka, cooldown 401 ({sisa}s)...   ",
                  end="", flush=True)
            time.sleep(TCP_INTERVAL)
            continue

        # --- Layer 2: HTTP HEAD via proxy tetap ---
        print()
        status = http_is_ready(url)

        if status == "ok":
            log(f"[ OK ] Stream aktif dan siap: {url}")
            return

        elif status == "401":
            log(f"[ 401 ] Server menolak. Cooldown {HTTP_COOLDOWN}s "
                f"(proxy tetap sama, TCP lanjut tiap {TCP_INTERVAL}s)...")
            http_blocked_until = time.time() + HTTP_COOLDOWN

        elif status == "error":
            log(f"[ ! ] HTTP gagal. Coba lagi dalam {TCP_INTERVAL}s...")

        else:
            code = status.split(":")[1]
            log(f"[ ! ] HTTP {code}. Coba lagi dalam {TCP_INTERVAL}s...")

        time.sleep(TCP_INTERVAL)

        time.sleep(TCP_INTERVAL)


def run_ffmpeg(url, suffix="", position=0):
    date_str = now_wita().strftime("%d-%m-%y")
    os.makedirs("recordings", exist_ok=True)

    # Deteksi codec
    try:
        codec = subprocess.check_output([
            "./ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=codec_name",
            "-of", "default=nokey=1:noprint_wrappers=1", url
        ], timeout=15).decode().strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        codec = "bin"

    ext_map = {"aac": "aac", "mp3": "mp3", "opus": "opus", "vorbis": "ogg"}
    ext = ext_map.get(codec, "bin")

    filename = f"recordings/VOT-Denpasar_{date_str}{('-' + suffix) if suffix else ''}.{ext}"

    cmd = [
        "./ffmpeg", "-y", "-hide_banner",
        # Reconnect hanya pada network error, BUKAN pada 4xx/5xx
        # Ini mencegah ffmpeg spam retry saat server mengirim 401
        "-reconnect", "1",
        "-reconnect_at_eof", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "60",       # Maks 60 detik antar reconnect
        "-reconnect_on_network_error", "1",
        # DIHAPUS: -reconnect_on_http_error 4xx,5xx
        # Alasan: 401 berarti diblacklist, retry otomatis hanya memperparah situasi
        "-timeout", "10000000",
        "-i", url,
        "-c", "copy",
        "-metadata", f"title=VOT Denpasar {date_str}",
        "-metadata", "artist=VOT Radio Denpasar",
        "-metadata", f"date={date_str}",
        filename
    ]

    log(f"[ RUN ] Mulai rekaman ke {filename}")
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )

    # Monitor stderr ffmpeg di thread terpisah
    # Filter log agar tidak spam — hanya tampilkan baris penting
    def log_ffmpeg(proc):
        SKIP_KEYWORDS = ("frame=", "fps=", "size=", "time=", "bitrate=", "speed=")
        for line in proc.stderr:
            stripped = line.strip()
            if stripped and not stripped.startswith(SKIP_KEYWORDS):
                now = datetime.datetime.now(WITA_TZ).strftime("%H:%M:%S")
                print(f"\033[34m[{now}]\033[0m [FFMPEG] {stripped}", flush=True)

    threading.Thread(target=log_ffmpeg, args=(process,), daemon=True).start()

    # Loop tunggu hingga cut-off atau ffmpeg berhenti
    while True:
        if is_past_cutoff():
            log("[ CUT-OFF ] Sudah 18:30 WITA, hentikan ffmpeg...")
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
            break

        retcode = process.poll()
        if retcode is not None:
            if retcode == 0:
                log("[ INFO ] ffmpeg selesai normal.")
            else:
                log(f"[ FAIL ] ffmpeg berhenti dengan kode {retcode}.")
            break

        time.sleep(2)

    log(f"[ DONE ] Rekaman selesai: {filename}")

    if position > 0:
        delay = position * 10
        log(f"[ DELAY ] Menunggu {delay}s sebelum upload...")
        time.sleep(delay)

    archive_url, item_id = upload_to_archive(filename)

    if archive_url and item_id:
        log(f"[ ARCHIVE ] File tersedia di {archive_url}")
        write_env_variables(archive_url, item_id)
    else:
        write_env_variables("None", "None")


def upload_to_archive(file_path, retries=5):
    log(f"[ UPLOAD ] Mulai upload {file_path} ke archive.org...")
    item_identifier = f"vot-denpasar-{now_wita().strftime('%Y%m%d-%H%M%S')}"
    filename = os.path.basename(file_path)

    for attempt in range(1, retries + 1):
        try:
            upload(
                item_identifier,
                files=[file_path],
                metadata={
                    'mediatype': 'audio',
                    'title': filename,
                    'creator': 'VOT Radio Denpasar'
                },
                access_key=MY_ACCESS_KEY,
                secret_key=MY_SECRET_KEY,
                verbose=True
            )

            details_url = f"https://archive.org/details/{item_identifier}"
            download_url = f"https://archive.org/download/{item_identifier}/{filename}"

            log(f"[ DONE ] Upload berhasil: {details_url}")
            log(f"[ LINK ] URL langsung: {download_url}")
            return download_url, item_identifier

        except Exception as e:
            log(f"[ WARN ] Upload gagal percobaan {attempt}/{retries}: {e}")
            if attempt < retries:
                wait = 30 * attempt  # Backoff: 30s, 60s, 90s, 120s
                log(f"[ RETRY ] Tunggu {wait}s sebelum mencoba lagi...")
                time.sleep(wait)
            else:
                log("[ ERROR ] Semua percobaan upload gagal.")
                return None, None


def write_env_variables(url, item_id):
    try:
        if "GITHUB_ENV" in os.environ:
            with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as env_file:
                env_file.write(f"ARCHIVE_URL={url}\n")
                env_file.write(f"ITEM_ID={item_id}\n")
            log("[ ENV ] ARCHIVE_URL dan ITEM_ID dikirim ke environment GitHub.")
        else:
            log("[ WARN ] GITHUB_ENV tidak tersedia.")
    except Exception as e:
        log(f"[ ERROR ] Gagal menulis environment: {e}")


def main_recording():
    parser = argparse.ArgumentParser(description="Record stream and upload")
    parser.add_argument("-s", "--suffix", type=str, default="")
    parser.add_argument("-p", "--position", type=int, default=0)
    parser.add_argument("--skip-check", action="store_true")
    args = parser.parse_args()

    stream_url = "https://i.klikhost.com:7051/stream"

    if args.skip_check:
        log("[ SKIP ] Pengecekan stream dilewati, langsung mulai rekam...")
    else:
        wait_for_stream(stream_url)

    run_ffmpeg(stream_url, args.suffix, args.position)
    log("[ DONE ] Semua tugas selesai.")
    return True


if __name__ == "__main__":
    log("[ START ] Memulai program recording...")

    RESTART_DELAY = 60  # Tunggu 60 detik sebelum restart jika ffmpeg gagal

    while True:
        if is_past_cutoff():
            log(f"[ STOP ] Sudah jam {now_wita().strftime('%H:%M')} WITA, program selesai.")
            break

        try:
            main_recording()
        except SystemExit:
            # Keluar bersih dari wait_for_stream jika cut-off
            break
        except Exception as e:
            log(f"[ ERROR ] Terjadi error tak terduga: {e}")

        if is_past_cutoff():
            log(f"[ STOP ] Sudah melewati 18:30 WITA setelah recording, hentikan.")
            break

        log(f"[ RESTART ] ffmpeg berhenti sebelum waktunya. "
            f"Tunggu {RESTART_DELAY}s sebelum coba stream lagi...")
        time.sleep(RESTART_DELAY)

    log("[ END ] Program selesai.")