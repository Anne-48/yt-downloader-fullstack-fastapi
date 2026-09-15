# YT Downloader

API fullstack para buscar informações de vídeos do YouTube, baixar nos formatos disponíveis e converter (áudio/vídeo) via `ffmpeg`, com progresso em tempo real.

Projeto de aprendizado/portfólio - backend em **Python (FastAPI)** e frontend em **HTML/CSS/JavaScript puro** (sem framework).

> **Nota:** projeto pensado para rodar localmente. Frontend e backend se comunicam entre si na mesma máquina/container — não há deploy público nem exposição externa configurada.

## Funcionalidades

- Busca de metadata do vídeo: título, thumbnail, duração, descrição, canal, visualizações e legendas (original + tradução)
- Listagem de formatos de vídeo e áudio disponíveis, com resolução, extensão, bitrate e tamanho
- Download direto no formato escolhido
- Conversão de formato (áudio: mp3, m4a, opus, vorbis, wav, flac, aac / vídeo: mp4, mkv, webm, avi, mov, flv) via `ffmpeg`
- Progresso em tempo real (baixando % / convertendo %) via Server-Sent Events (SSE)
- Cancelamento automático do processo caso a página seja fechada (com tolerância para reload)
- Link de download temporário (expira em 5 minutos), sem manter arquivos salvos no servidor
- Rate limiting por IP (`slowapi`)
- Containerizado com Docker - um único container: o próprio FastAPI serve o frontend estático

## Tecnologias

**Backend:** Python, FastAPI, [yt-dlp](https://github.com/yt-dlp/yt-dlp), ffmpeg, slowapi, uvicorn

**Frontend:** HTML, CSS e JavaScript puro (Fetch API, EventSource/SSE, sessionStorage)

**Infraestrutura:** Docker (container único - FastAPI serve API e frontend juntos)

## Endpoints da API

| Método | Rota | Descrição |
|---|---|---|
| GET | `/info?url=` | Retorna metadata e formatos disponíveis do vídeo |
| GET | `/conversion-formats` | Lista os formatos de conversão suportados |
| POST | `/download` | Inicia o download de um formato específico, retorna `download_id` |
| POST | `/download-convertido` | Inicia download + conversão, retorna `download_id` |
| GET | `/progress/{download_id}` | Stream (SSE) com o progresso do processo |
| GET | `/file/{download_id}` | Serve o arquivo pronto para download |

## Como rodar

### Com Docker (recomendado)

Um único container: o FastAPI serve tanto a API quanto o frontend (arquivos estáticos), tudo na mesma porta.

**Build da imagem:**
```bash
docker build -t yt-downloader .
```

**Rodar (em background):**
```bash
docker run -d --name yt-downloader -p 8000:8000 yt-downloader
```

Acesse `http://localhost:8000`.

**Ver logs:**
```bash
docker logs -f yt-downloader
```

**Parar / iniciar de novo (sem rebuildar):**
```bash
docker stop yt-downloader
docker start yt-downloader
```

**Atualizar após mudanças no código:**
```bash
docker stop yt-downloader
docker rm yt-downloader
docker build -t yt-downloader .
docker run -d --name yt-downloader -p 8000:8000 yt-downloader
```

**Exportar a imagem como arquivo `.tar`** (para transportar ou rodar em outra máquina, sem precisar do código-fonte nem rebuildar):
```bash
docker save -o yt-downloader.tar yt-downloader:latest
```

**Importar e rodar esse `.tar` em outra máquina** (com Docker instalado, mesma arquitetura de processador):
```bash
docker load -i yt-downloader.tar
docker run -d --name yt-downloader -p 8000:8000 yt-downloader
```

### Sem Docker (desenvolvimento local)

**Backend:**
```bash
cd backend
python3 -m venv venv
source venv/bin/activate    # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Instale o `ffmpeg` no sistema (necessário para conversões):
- Ubuntu/Debian: `sudo apt install ffmpeg`
- macOS: `brew install ffmpeg`
- Windows: baixe em [ffmpeg.org](https://ffmpeg.org) e adicione ao PATH

```bash
uvicorn main:app --reload
```

**Frontend:**

Abra `frontend/index.html` com uma extensão tipo Live Server, ou qualquer servidor estático simples. Certifique-se de que a URL do backend no `script.js` (`http://127.0.0.1:8000`) bate com onde o backend está rodando.

## Estrutura do projeto

```
yt_api/
├── Dockerfile
├── backend/
│   ├── main.py
│   └── requirements.txt
└── frontend/
    ├── index.html
    ├── script.js
    └── style.css
```

## Limitações conhecidas

- Progresso e limites de requisição ficam em memória - são reiniciados junto com o servidor
- Progresso de conversão é calculado a partir da duração total do vídeo, não é um percentual exato de todos os cenários
- Conversão de vídeo tenta copiar o stream sem recodificar (`-c copy`); quando o codec não é compatível com o container de destino, recodifica com `libx264`/`aac`

## Autor

Anne Szczypior Pinheiro Lima
[LinkedIn](https://www.linkedin.com/in/anne-pinheiro-16a607424/)
