import os
from internetarchive import upload

def log(msg):
    print(msg)

def upload_transcripts_to_existing_item(folder_path, item_id, access_key, secret_key):
    """
    Upload semua file dari folder_path ke item Archive.org yang sudah ada.
    folder_path : str -> path folder (misal 'transcripts/')
    item_id     : str -> item Archive.org yang sudah ada
    access_key  : str -> API key
    secret_key  : str -> Secret key
    """
    if not os.path.isdir(folder_path):
        log(f"[ ERROR ] Folder '{folder_path}' tidak ditemukan!")
        return

    files_to_upload = [os.path.join(folder_path, f) for f in os.listdir(folder_path) if os.path.isfile(os.path.join(folder_path, f))]
    if not files_to_upload:
        log(f"[ WARNING ] Tidak ada file di '{folder_path}' untuk diupload.")
        return

    log(f"[ UPLOAD ] Mulai upload {len(files_to_upload)} file ke Archive.org item: {item_id}")

    try:
        upload(
            item_id,
            files=files_to_upload,
            metadata={
                'subject': 'Description-Transcription',
                'description': 'Auto transcription and description added by Python script'
            },
            access_key=access_key,
            secret_key=secret_key,
            verbose=True
        )

        details_url = f"https://archive.org/details/{item_id}"
        log(f"[ DONE ] Upload berhasil: {details_url}")
        for f in files_to_upload:
            filename = os.path.basename(f)
            download_url = f"https://archive.org/download/{item_id}/{filename}"
            log(f"[ LINK ] {filename}: {download_url}")

    except Exception as e:
        log(f"[ ERROR ] Upload gagal: {e}")


# ===============================
# Contoh pemakaian
# ===============================
if __name__ == "__main__":
    # Ambil key dari environment variable
    MY_ACCESS_KEY = os.environ.get("MY_ACCESS_KEY")
    MY_SECRET_KEY = os.environ.get("MY_SECRET_KEY")
    ITEM_ID = os.environ.get("ITEM_ID")  # ID dari GitHub Actions matrix

    if not all([MY_ACCESS_KEY, MY_SECRET_KEY, ITEM_ID]):
        log("[ ERROR ] Pastikan MY_ACCESS_KEY, MY_SECRET_KEY, dan ITEM_ID sudah di-set di environment.")
    else:
        upload_transcripts_to_existing_item("transcripts", ITEM_ID, MY_ACCESS_KEY, MY_SECRET_KEY)
