# YT Downloader

API fullstack para buscar informações de vídeos do YouTube, baixar nos formatos disponíveis e converter (áudio/vídeo) via `ffmpeg`, com progresso em tempo real

> **Nota:** projeto pensado para rodar localmente

## Funcionalidades

- Busca de metadata do vídeo: título, thumbnail, duração, descrição, canal, visualizações e legendas disponíveis (inglês original e português brasileiro, manual ou automática)
- Listagem de formatos de vídeo (agrupados por resolução, com codec e tamanho) e áudio (com bitrate e tamanho) disponíveis
- Download direto no formato escolhido
- Conversão de formato (áudio: mp3, m4a, opus, vorbis, wav, flac, aac / vídeo: mp4, mkv, webm, avi, mov, flv) via `ffmpeg`, com mkv como primeira opção da lista
- Legendas embutidas no vídeo (múltiplos idiomas ao mesmo tempo), disponível apenas ao converter para mkv
- Progresso em tempo real (baixando % / convertendo %) via Server-Sent Events (SSE)
- Cancelamento automático do processo caso a página seja fechada (com tolerância de 10s)
- Link de download temporário (expira em 3 minutos), sem manter arquivos salvos no servidor
- Containerizado com Docker em único container: o FastAPI serve frontend estático

## Tecnologias

**Backend:** Python, FastAPI, [yt-dlp](https://github.com/yt-dlp/yt-dlp), ffmpeg, uvicorn

**Frontend:** HTML, CSS e JavaScript puro (Fetch API, EventSource/SSE, sessionStorage)

**Infraestrutura:** Docker (container único - FastAPI serve API e frontend juntos)

## Endpoints da API

| Método | Rota                      | Descrição                                                                                                                                                           |
| ------ | ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/info?url=`              | Retorna metadata e formatos disponíveis do vídeo                                                                                                                    |
| GET    | `/conversion-formats`     | Lista os formatos de conversão suportados                                                                                                                           |
| POST   | `/download`               | Inicia o download de um formato específico, retorna `download_id`                                                                                                   |
| POST   | `/download-convertido`    | Inicia download + conversão, retorna `download_id`. Aceita `subtitles` (lista opcional de idiomas) para embutir legendas - só válido quando `target_format` é `mkv` |
| GET    | `/progress/{download_id}` | Stream (SSE) com o progresso do processo                                                                                                                            |
| GET    | `/file/{download_id}`     | Serve o arquivo pronto para download                                                                                                                                |

## Como rodar

### Com Docker (recomendado)

Um único container: o FastAPI serve tanto a API quanto o frontend (arquivos estáticos), tudo na mesma porta

**Build da imagem:**

```bash
docker build -t yt-downloader .
```

**Rodar (em background):**

```bash
docker run -d --name yt-downloader -p 8000:8000 yt-downloader
```

Acesse `http://localhost:8000`

**Ver logs:**

```bash
docker logs -f yt-downloader
```

**Parar / iniciar sem rebuild:**

```bash
docker stop yt-downloader
docker start yt-downloader
```

**Atualizar/rebuild após mudanças no código:**

```bash
docker stop yt-downloader
docker rm yt-downloader
docker build -t yt-downloader .
docker run -d --name yt-downloader -p 8000:8000 yt-downloader
```

**Exportar a imagem como `.tar`:**

```bash
docker save -o yt-downloader.tar yt-downloader:latest
```

**Importar e rodar `.tar`:**

```bash
docker load -i yt-downloader.tar
docker run -d --name yt-downloader -p 8000:8000 yt-downloader
```

### Sem Docker (desenvolvimento local)

**Pré-requisito: instale `ffmpeg` no sistema** (necessário para conversões):

- Ubuntu/Debian: `sudo apt install ffmpeg`
- macOS: `brew install ffmpeg`
- Windows: baixe em [ffmpeg.org](https://ffmpeg.org) e adicione ao PATH

**Backend:**

```bash
cd backend
python3 -m venv venv
source venv/bin/activate    # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Acesse `http://localhost:8000`
<br>

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

## Limitações

- Sem autenticação de usuário
- Sem rate limiting (uso pessoal, local)
- Progresso fica em memória, reiniciado junto com o servidor
- Progresso de conversão é calculado a partir da duração total do vídeo, não é um percentual exato de todos os cenários
- Conversão de vídeo tenta copiar o stream sem recodificar (`-c copy`); quando o codec não é compatível com o container de destino, recodifica com `libx264`/`aac`
- Legendas filtradas para inglês (original) e português brasileiro (`pt-BR`); vídeos que só oferecem `pt` genérico (sem distinguir Brasil/Portugal) não mostram legenda em pt-BR

## Autor

Anne Szczypior Pinheiro Lima
[LinkedIn](https://www.linkedin.com/in/anne-s-pinheiro-lima-16a607424/)
