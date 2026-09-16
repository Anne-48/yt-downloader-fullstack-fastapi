from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List
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

# Formatos de saída aceitos na conversão
AUDIO_FORMATS = {"mp3", "m4a", "opus", "vorbis", "wav", "flac", "aac"}
VIDEO_FORMATS = {"mp4", "mkv", "webm", "avi", "mov", "flv"}
VIDEO_FORMAT_ORDER = ["mkv", "mp4", "webm", "avi", "mov", "flv"] # apenas para ter mkv primeiro

# Especifica codec para cada formato de áudio
AUDIO_CODEC_MAP = {
    "mp3": "libmp3lame",
    "m4a": "aac",
    "aac": "aac",
    "opus": "libopus",
    "vorbis": "libvorbis",
    "flac": "flac",
    "wav": "pcm_s16le",
}

# Guarda onde está o arquivo pronto de cada download_id, para /file usar
downloads_store = {}

# Guarda o status atual (downloading/converting/finished/error) de cada download_id
progress_store = {}

# Guarda quando cada download_id foi visto pela última vez via SSE ativa (usado se user fechou página)
last_seen = {}

# Guarda quais downloads devem ser abortados (marcado pelo reaper_loop)
cancelled_ids = set()

# Quantos segundos sem nenhuma conexão SSE até considerar "página fechada" e cancelar download/conversão
GRACE_PERIOD = 10


def cleanup_download(download_id: str):
    """Remove arquivo do disco e limpa todos os registros desse download_id"""
    entry = downloads_store.pop(download_id, None)
    if entry and os.path.exists(entry["path"]):
        os.remove(entry["path"])
    progress_store.pop(download_id, None)
    last_seen.pop(download_id, None)
    cancelled_ids.discard(download_id)


def reaper_loop():
    """
    Roda em background (thread separada)
    Checa a cada 1s se algum download ficou sem conexão SSE ativa por mais que GRACE_PERIOD
    Se sim, marca como cancelado o download/conversão em andamento
    Checa cancelled_ids e aborta
    """
    while True:
        time.sleep(1)
        agora = time.time()
        for download_id, ts in list(last_seen.items()):
            status = progress_store.get(download_id, {}).get("status")
            if status in ("finished", "error"):
                continue
            if agora - ts > GRACE_PERIOD:
                cancelled_ids.add(download_id)


# Inicia reaper quando servidor sobe
threading.Thread(target=reaper_loop, daemon=True).start()


def parse_out_time(value):
    """
    Converte o tempo que ffmpeg imprime durante a conversão
    (formato "HH:MM:SS.microssegundos") em segundos totais
    Para comparar com a duração total do vídeo e calcular %
    """
    try:
        h, m, s = value.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception:
        return None

#wrapper para ffmpeg
def run_ffmpeg_with_progress(download_id, input_path, output_path, ffmpeg_args, total_duration):
    """
    Roda ffmpeg como processo separado (subprocess), com -progress pipe:1
    Imprime o próprio progresso enquanto processa, calculando % e atualizando o progress_store
    Usado tanto pra conversão simples (sem legenda) quanto pra extração de áudio
    """
    cmd = ["ffmpeg", "-y", "-i", input_path, *ffmpeg_args, "-progress", "pipe:1", "-nostats", output_path]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)

    for line in process.stdout:
        # Se reaper marcou esse download como cancelado, mata o processo ffmpeg
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
    return process.returncode == 0  # True se o ffmpeg terminou sem erro


def run_ffmpeg_embed_subtitles(download_id, video_path, subtitle_files, output_path, video_args, total_duration):
    """
    Versão especial do ffmpeg pra quando tem legenda(s) pra embutir no vídeo
    Aqui o ffmpeg recebe múltiplos -i: 1 para o vídeo, 1 para cada .srt de legenda
    Mapeia todas as faixas(vídeo, áudio, e cada legenda) para o arquivo final com -map
    """
    cmd = ["ffmpeg", "-y", "-i", video_path]

    # Um -i extra pra cada arquivo de legenda
    for sub_path, _ in subtitle_files:
        cmd += ["-i", sub_path]

    # -map 0 = inclui todas as faixas do primeiro input (vídeo + áudio original)
    cmd += ["-map", "0"]

    # -map 1, -map 2... = inclui cada legenda (input 1 = 1ª legenda, input 2 = 2ª, etc.)
    for idx in range(1, len(subtitle_files) + 1):
        cmd += ["-map", str(idx)]

    cmd += video_args        # ex: ["-c:v", "copy", "-c:a", "copy"] ou recodificando
    cmd += ["-c:s", "srt"]   # codec das legendas dentro do mkv

    # Marca o idioma de cada faixa de legenda
    for idx, (_, lang) in enumerate(subtitle_files):
        cmd += [f"-metadata:s:s:{idx}", f"language={lang}"]

    cmd += ["-progress", "pipe:1", "-nostats", output_path]

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
    """
    Worker do /download (sem conversão)
    Roda em thread separada pra o endpoint poder responder na hora sem esperar download terminar
    """
    progress_store[download_id] = {"status": "downloading", "percent": 0}

    def hook(d):
        # Chamado pelo yt-dlp n vezes por segundo durante download
        if download_id in cancelled_ids:

            # Levanta exceção especial do yt-dlp, que aborta download de dentro do hook
            raise DownloadCancelled("Cancelado: página fechada")
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate')
            downloaded = d.get('downloaded_bytes', 0)
            if total:
                percent = downloaded / total * 100
                progress_store[download_id] = {"status": "downloading", "percent": round(percent, 1)}

    try:
        # Busca info novamente, pois backend não guarda requisição
        ydl_opts_info = {'quiet': True, 'no_warnings': True, 'noplaylist': True}
        with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
            info = ydl.extract_info(url, download=False)

        # Acha na lista de formatos item que bate com format_id escolhido
        selected_format = None
        for f in info["formats"]:
            if f["format_id"] == format_id:
                selected_format = f
                break

        if selected_format is None:
            progress_store[download_id] = {"status": "error", "error": "Formato não encontrado"}
            return

        # Se for áudio only baixa direto; se for vídeo mescla com o melhor áudio disponível
        format_string = format_id if selected_format.get("vcodec") == "none" else f"{format_id}+bestaudio"

        ydl_opts_download = {
            'format': format_string,
            'outtmpl': f'downloads/{download_id}.%(ext)s',  # Nome do arquivo = download_id
            'merge_output_format': selected_format.get("ext"),  # Mantém o container do formato escolhido (evita cair pra mkv)
            'no_warnings': True,
            'noplaylist': True,
            'progress_hooks': [hook],
        }

        with yt_dlp.YoutubeDL(ydl_opts_download) as ydl:
            info_dl = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info_dl)

        # Nome do download para o usuário (título real do vídeo com _)
        ext = os.path.splitext(filename)[1].lstrip(".")
        display_name = f"{sanitize_filename(info.get('title', 'video'))}.{ext}"
        display_name = display_name.replace(" ", "_")

        downloads_store[download_id] = {"path": filename, "display_name": display_name}
        progress_store[download_id] = {"status": "finished", "percent": 100, "filename": display_name}

        # Agenda limpeza automática do arquivo pra 3 minutos
        threading.Timer(180, cleanup_download, args=[download_id]).start()

    except DownloadCancelled:
        cleanup_download(download_id)
    except Exception as e:
        progress_store[download_id] = {"status": "error", "error": str(e)}


def run_convert_download(download_id, url, format_id, target_format, subtitles=None):
    """
    Worker do /download-convertido. Faz três coisas possíveis:
    1. Converter pra áudio (extrai só o som)
    2. Converter vídeo pra outro container, com legenda embutida (só quando target_format == mkv)
    3. Converter vídeo pra outro container, sem legenda
    """
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
        # Legenda só é aceita se: foi pedida, o alvo é vídeo (não áudio), e o container é mkv
        want_subtitles = bool(subtitles) and not is_audio_target and target_format == "mkv"

        if is_audio_target:
            # Alvo é áudio: ignora completamente o vídeo, pega só o melhor áudio para conversão
            format_string = format_id if selected_format.get("vcodec") == "none" else "bestaudio"
        else:
            format_string = (
                format_id
                if selected_format.get("acodec") != "none"
                else f"{format_id}+bestaudio"
            )

        ydl_opts_download = {
            'format': format_string,
            'outtmpl': f'downloads/{download_id}_raw.%(ext)s',  # "_raw" = ainda não é o arquivo final, só o material bruto
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'progress_hooks': [hook],
        }
        if not is_audio_target:
            # Container intermediário seguro pra qualquer combinação de vídeo+áudio mesclado
            ydl_opts_download['merge_output_format'] = 'mkv'

        if want_subtitles:
            # Pede pro yt-dlp baixar as legendas escolhidas, no formato .srt, junto com o vídeo bruto
            ydl_opts_download['writesubtitles'] = True
            ydl_opts_download['writeautomaticsub'] = True
            ydl_opts_download['subtitleslangs'] = subtitles
            ydl_opts_download['subtitlesformat'] = 'srt'

        with yt_dlp.YoutubeDL(ydl_opts_download) as ydl:
            info_dl = ydl.extract_info(url, download=True)
            raw_filename = ydl.prepare_filename(info_dl)

        # Checa cancelamento logo após o download bruto terminar (antes de gastar tempo convertendo)
        if download_id in cancelled_ids:
            if os.path.exists(raw_filename):
                os.remove(raw_filename)
            cleanup_download(download_id)
            return

        stored_path = f"downloads/{download_id}.{target_format}"

        # Descobre os caminhos reais dos arquivos .srt que o yt-dlp baixou
        # (o yt-dlp nomeia como "nome_do_video.idioma.srt", ao lado do vídeo)
        subtitle_files = []
        if want_subtitles:
            base_path = os.path.splitext(raw_filename)[0]
            for lang in subtitles:
                sub_path = f"{base_path}.{lang}.srt"
                if os.path.exists(sub_path):
                    subtitle_files.append((sub_path, lang))

        if is_audio_target:
            # Caminho 1: extrair só áudio
            codec = AUDIO_CODEC_MAP.get(target_format, "aac")
            ffmpeg_args = ["-vn", "-acodec", codec]  # -vn = descarta o vídeo
            if target_format == "mp3":
                ffmpeg_args += ["-q:a", "0"]  # 0 = melhor qualidade VBR
            ok = run_ffmpeg_with_progress(download_id, raw_filename, stored_path, ffmpeg_args, info.get("duration"))

        elif subtitle_files:
            # Caminho 2: vídeo + legenda(s) embutida(s)
            # Tenta primeiro sem recodificar
            ok = run_ffmpeg_embed_subtitles(
                download_id, raw_filename, subtitle_files, stored_path,
                ["-c:v", "copy", "-c:a", "copy"], info.get("duration"),
            )
            # Se falhar (codec incompatível com o container), recodifica de verdade
            if not ok and download_id not in cancelled_ids:
                ok = run_ffmpeg_embed_subtitles(
                    download_id, raw_filename, subtitle_files, stored_path,
                    ["-c:v", "libx264", "-c:a", "aac"], info.get("duration"),
                )

        else:
            # Caminho 3: só vídeo sem legenda, mudando de container
            ok = run_ffmpeg_with_progress(download_id, raw_filename, stored_path, ["-c", "copy"], info.get("duration"))
            if not ok and download_id not in cancelled_ids:
                ok = run_ffmpeg_with_progress(
                    download_id, raw_filename, stored_path,
                    ["-c:v", "libx264", "-c:a", "aac"], info.get("duration"),
                )

        # Limpa os arquivos temporários (vídeo bruto + legendas .srt) já embutidas ou não utilizadas mais
        if os.path.exists(raw_filename):
            os.remove(raw_filename)
        for sub_path, _ in subtitle_files:
            if os.path.exists(sub_path):
                os.remove(sub_path)

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
    """Lista fixa dos formatos de conversão suportados para o frontend montar as opções"""
    return {
        "audio": sorted(AUDIO_FORMATS),
        "video": VIDEO_FORMAT_ORDER
    }


@app.get("/info")
def get_info(request: Request, url: str):
    """Busca metadata do vídeo + monta a lista de formatos disponíveis """
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

    # Se algum formato de áudio tem language_preference==10 vídeo tem dublagem
    # (nesse caso, só os ==10 são o áudio original de verdade, o resto (-1) é dublagem)
    # Se nenhum formato tem ==10 não há dublagem, os -1 já são o único/original áudio
    has_marked_original = any(f.get("language_preference") == 10 for f in info["formats"])

    formats = [
    {
        "format_id": f["format_id"],
        "ext": f["ext"],
        "resolution": f.get("resolution", "audio only"),
        "filesize": f.get("filesize") or f.get("filesize_approx"),
        "abr": f.get("abr"),
        "vcodec": f.get("vcodec"),
    }
    for f in info["formats"]
    if (
        f.get("vcodec") != "none"                                                # mantém todo vídeo
        or f.get("language_preference") == 10                                    # mantém áudio original marcado
        or (not has_marked_original and f.get("language_preference") == -1)      # ou o único áudio, se não há dublagem
    )
    and (f.get("filesize") or f.get("filesize_approx"))       # descarta formatos sem tamanho conhecido
    and not f["format_id"].endswith("-drc")                   # descarta variantes de compressão dinâmica (redundantes)
]

    # Junta legenda humana + automática numa lista marcando o tipo de cada
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
def post_download(request: Request, download_request: DownloadRequest):
    """
    Cria um download_id e dispara o processo em background
    Frontend acompanha o progresso depois via /progress/{id}
    """
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
    subtitles: Optional[List[str]] = None  # lista de idiomas


@app.post("/download-convertido")
def post_download_convertido(request: Request, download_request: ConvertedDownloadRequest):
    # Legenda só faz sentido junto com mkv (único container com bom suporte a múltiplas faixas)
    if download_request.subtitles and download_request.target_format != "mkv":
        raise HTTPException(status_code=400, detail="Legendas só estão disponíveis quando o formato de destino é mkv")

    download_id = str(uuid.uuid4())
    threading.Thread(
        target=run_convert_download,
        args=(download_id, download_request.url, download_request.format_id, download_request.target_format, download_request.subtitles),
        daemon=True,
    ).start()
    return {"download_id": download_id}


@app.get("/progress/{download_id}")
async def get_progress(download_id: str):
    """
    Endpoint de Server-Sent Events (SSE) - mantém conexão aberta mandando atualizações de progresso conforme workeras gera
    Até o status virar "finished" ou "error"
    """
    async def event_stream():
        last_sent = None
        while True:
            # Marca que essa conexão está viva agora - reaper_loop usa para saber se página ainda está aberta
            last_seen[download_id] = time.time()

            data = progress_store.get(download_id)
            if data is None:
                yield f"data: {json.dumps({'status': 'error', 'error': 'ID não encontrado'})}\n\n"
                break

            # Só manda de novo se mudou algo (evita mandar a mesma % repetida sem necessidade)
            if data != last_sent:
                yield f"data: {json.dumps(data)}\n\n"
                last_sent = data.copy()

            if data.get("status") in ("finished", "error"):
                break

            await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/file/{download_id}")
def get_file(download_id: str):
    """Serve o arquivo pronto pra download. Expira sozinho em 3 minutos (cleanup_download)"""
    entry = downloads_store.get(download_id)

    if entry is None or not os.path.exists(entry["path"]):
        raise HTTPException(status_code=404, detail="Link expirado ou inválido")

    return FileResponse(entry["path"], filename=entry["display_name"])


# (a raiz "/", "/script.js", "/style.css") cai aqui, servindo o frontend estático
app.mount("/", StaticFiles(directory="../frontend", html=True), name="static")