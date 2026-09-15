from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from pydantic import BaseModel
from yt_dlp.utils import sanitize_filename, DownloadCancelled
import yt_dlp
import os
import uuid
import threading
import subprocess
import asyncio
import json
import time

app = FastAPI()

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

AUDIO_FORMATS = {"mp3", "m4a", "opus", "vorbis", "wav", "flac", "aac"}
VIDEO_FORMATS = {"mp4", "mkv", "webm", "avi", "mov", "flv"}

AUDIO_CODEC_MAP = {
    "mp3": "libmp3lame",
    "m4a": "aac",
    "aac": "aac",
    "opus": "libopus",
    "vorbis": "libvorbis",
    "flac": "flac",
    "wav": "pcm_s16le",
}

downloads_store = {}
progress_store = {}
last_seen = {}
cancelled_ids = set()

GRACE_PERIOD = 5


def cleanup_download(download_id: str):
    entry = downloads_store.pop(download_id, None)
    if entry and os.path.exists(entry["path"]):
        os.remove(entry["path"])
    progress_store.pop(download_id, None)
    last_seen.pop(download_id, None)
    cancelled_ids.discard(download_id)


def reaper_loop():
    while True:
        time.sleep(1)
        agora = time.time()
        for download_id, ts in list(last_seen.items()):
            status = progress_store.get(download_id, {}).get("status")
            if status in ("finished", "error"):
                continue
            if agora - ts > GRACE_PERIOD:
                cancelled_ids.add(download_id)


threading.Thread(target=reaper_loop, daemon=True).start()


def parse_out_time(value):
    try:
        h, m, s = value.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception:
        return None


def run_ffmpeg_with_progress(download_id, input_path, output_path, ffmpeg_args, total_duration):
    cmd = ["ffmpeg", "-y", "-i", input_path, *ffmpeg_args, "-progress", "pipe:1", "-nostats", output_path]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)

    for line in process.stdout:
        if download_id in cancelled_ids:
            process.kill()
            return False

        line = line.strip()
        if line.startswith("out_time="):
            seconds_done = parse_out_time(line.split("=", 1)[1])
            if seconds_done is not None and total_duration:
                percent = min(seconds_done / total_duration * 100, 100)
                progress_store[download_id] = {"status": "converting", "percent": round(percent, 1)}

    process.wait()
    return process.returncode == 0


def run_simple_download(download_id, url, format_id):
    progress_store[download_id] = {"status": "downloading", "percent": 0}

    def hook(d):
        if download_id in cancelled_ids:
            raise DownloadCancelled("Cancelado: página fechada")
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate')
            downloaded = d.get('downloaded_bytes', 0)
            if total:
                percent = downloaded / total * 100
                progress_store[download_id] = {"status": "downloading", "percent": round(percent, 1)}

    try:
        ydl_opts_info = {'quiet': True, 'no_warnings': True, 'noplaylist': True}
        with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
            info = ydl.extract_info(url, download=False)

        selected_format = None
        for f in info["formats"]:
            if f["format_id"] == format_id:
                selected_format = f
                break

        if selected_format is None:
            progress_store[download_id] = {"status": "error", "error": "Formato não encontrado"}
            return

        format_string = format_id if selected_format.get("vcodec") == "none" else f"{format_id}+bestaudio"

        ydl_opts_download = {
            'format': format_string,
            'outtmpl': f'downloads/{download_id}.%(ext)s',
            'merge_output_format': selected_format.get("ext"),
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'progress_hooks': [hook],
        }

        with yt_dlp.YoutubeDL(ydl_opts_download) as ydl:
            info_dl = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info_dl)

        ext = os.path.splitext(filename)[1].lstrip(".")
        display_name = f"{sanitize_filename(info.get('title', 'video'))}.{ext}"
        display_name = display_name.replace(" ", "_")

        downloads_store[download_id] = {"path": filename, "display_name": display_name}
        progress_store[download_id] = {"status": "finished", "percent": 100, "filename": display_name}

        threading.Timer(300, cleanup_download, args=[download_id]).start()

    except DownloadCancelled:
        cleanup_download(download_id)
    except Exception as e:
        progress_store[download_id] = {"status": "error", "error": str(e)}


def run_convert_download(download_id, url, format_id, target_format):
    progress_store[download_id] = {"status": "downloading", "percent": 0}

    def hook(d):
        if download_id in cancelled_ids:
            raise DownloadCancelled("Cancelado: página fechada")
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate')
            downloaded = d.get('downloaded_bytes', 0)
            if total:
                percent = downloaded / total * 100
                progress_store[download_id] = {"status": "downloading", "percent": round(percent, 1)}

    try:
        ydl_opts_info = {'quiet': True, 'no_warnings': True, 'noplaylist': True}
        with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
            info = ydl.extract_info(url, download=False)

        selected_format = None
        for f in info["formats"]:
            if f["format_id"] == format_id:
                selected_format = f
                break

        if selected_format is None:
            progress_store[download_id] = {"status": "error", "error": "Formato não encontrado"}
            return

        if target_format not in AUDIO_FORMATS and target_format not in VIDEO_FORMATS:
            progress_store[download_id] = {"status": "error", "error": "Formato de saída inválido"}
            return

        is_audio_target = target_format in AUDIO_FORMATS

        if is_audio_target:
            format_string = format_id if selected_format.get("vcodec") == "none" else "bestaudio"
        else:
            format_string = (
                format_id
                if selected_format.get("acodec") != "none"
                else f"{format_id}+bestaudio"
            )

        ydl_opts_download = {
            'format': format_string,
            'outtmpl': f'downloads/{download_id}_raw.%(ext)s',
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'progress_hooks': [hook],
        }
        if not is_audio_target:
            ydl_opts_download['merge_output_format'] = 'mkv'

        with yt_dlp.YoutubeDL(ydl_opts_download) as ydl:
            info_dl = ydl.extract_info(url, download=True)
            raw_filename = ydl.prepare_filename(info_dl)

        if download_id in cancelled_ids:
            if os.path.exists(raw_filename):
                os.remove(raw_filename)
            cleanup_download(download_id)
            return

        stored_path = f"downloads/{download_id}.{target_format}"

        if is_audio_target:
            codec = AUDIO_CODEC_MAP.get(target_format, "aac")
            ffmpeg_args = ["-vn", "-acodec", codec]
            if target_format == "mp3":
                ffmpeg_args += ["-q:a", "0"]
            ok = run_ffmpeg_with_progress(download_id, raw_filename, stored_path, ffmpeg_args, info.get("duration"))
        else:
            ok = run_ffmpeg_with_progress(download_id, raw_filename, stored_path, ["-c", "copy"], info.get("duration"))
            if not ok and download_id not in cancelled_ids:
                ok = run_ffmpeg_with_progress(
                    download_id, raw_filename, stored_path,
                    ["-c:v", "libx264", "-c:a", "aac"], info.get("duration"),
                )

        if os.path.exists(raw_filename):
            os.remove(raw_filename)

        if download_id in cancelled_ids:
            if os.path.exists(stored_path):
                os.remove(stored_path)
            cleanup_download(download_id)
            return

        if not ok:
            progress_store[download_id] = {"status": "error", "error": "Falha na conversão"}
            return

        display_name = f"{sanitize_filename(info.get('title', 'video'))}.{target_format}"
        display_name = display_name.replace(" ", "_")

        downloads_store[download_id] = {"path": stored_path, "display_name": display_name}
        progress_store[download_id] = {"status": "finished", "percent": 100, "filename": display_name}

        threading.Timer(300, cleanup_download, args=[download_id]).start()

    except DownloadCancelled:
        cleanup_download(download_id)
    except Exception as e:
        progress_store[download_id] = {"status": "error", "error": str(e)}


@app.get("/conversion-formats")
def get_conversion_formats():
    return {
        "audio": sorted(AUDIO_FORMATS),
        "video": sorted(VIDEO_FORMATS),
    }


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request, exc):
    return JSONResponse(status_code=429, content={"detail": "Muitas requisições, tente novamente em instantes."})


@app.get("/info")
@limiter.limit("5/minute")
def get_info(request: Request, url: str):
    ydl_opts = {
        'skip_download': True,
        'no_warnings': True,
        'quiet': True,
        'noplaylist': True,
        'writeautomaticsub': True,
        'writesubtitles': True,
        'subtitleslangs': ['en', 'pt'],
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao processar a URL: {str(e)}")

    has_marked_original = any(f.get("language_preference") == 10 for f in info["formats"])

    formats = [
        {
            "format_id": f["format_id"],
            "ext": f["ext"],
            "resolution": f.get("resolution", "audio only"),
            "filesize": f.get("filesize") or f.get("filesize_approx"),
            "abr": f.get("abr"),
        }
        for f in info["formats"]
        if (
            f.get("vcodec") != "none"
            or f.get("language_preference") == 10
            or (not has_marked_original and f.get("language_preference") == -1)
        )
        and (f.get("filesize") or f.get("filesize_approx"))
        and not f["format_id"].endswith("-drc")
    ]

    captions = [
        {"lang": lang, "type": "human"}
        for lang in info.get("subtitles", {})
        if lang in ("en", "pt", "pt-BR")
    ] + [
        {"lang": lang, "type": "auto"}
        for lang in info.get("automatic_captions", {})
        if lang in ("en-orig", "pt", "pt-BR")
    ]

    return {
        "title": info.get("title"),
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "description": info.get("description"),
        "channel": info.get("channel"),
        "view_count": info.get("view_count"),
        "captions": captions,
        "formats": formats,
    }


class DownloadRequest(BaseModel):
    url: str
    format_id: str


@app.post("/download")
@limiter.limit("5/minute")
@limiter.limit("15/day")
def post_download(request: Request, download_request: DownloadRequest):
    download_id = str(uuid.uuid4())
    threading.Thread(
        target=run_simple_download,
        args=(download_id, download_request.url, download_request.format_id),
        daemon=True,
    ).start()
    return {"download_id": download_id}


class ConvertedDownloadRequest(BaseModel):
    url: str
    format_id: str
    target_format: str


@app.post("/download-convertido")
@limiter.limit("5/minute")
@limiter.limit("15/day")
def post_download_convertido(request: Request, download_request: ConvertedDownloadRequest):
    download_id = str(uuid.uuid4())
    threading.Thread(
        target=run_convert_download,
        args=(download_id, download_request.url, download_request.format_id, download_request.target_format),
        daemon=True,
    ).start()
    return {"download_id": download_id}


@app.get("/progress/{download_id}")
async def get_progress(download_id: str):
    async def event_stream():
        last_sent = None
        while True:
            last_seen[download_id] = time.time()

            data = progress_store.get(download_id)
            if data is None:
                yield f"data: {json.dumps({'status': 'error', 'error': 'ID não encontrado'})}\n\n"
                break

            if data != last_sent:
                yield f"data: {json.dumps(data)}\n\n"
                last_sent = data.copy()

            if data.get("status") in ("finished", "error"):
                break

            await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/file/{download_id}")
def get_file(download_id: str):
    entry = downloads_store.get(download_id)

    if entry is None or not os.path.exists(entry["path"]):
        raise HTTPException(status_code=404, detail="Link expirado ou inválido")

    return FileResponse(entry["path"], filename=entry["display_name"])


app.mount("/", StaticFiles(directory="frontend", html=True), name="static")