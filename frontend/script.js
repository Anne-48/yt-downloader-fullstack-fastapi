let ultimoEnvio = 0;

function bloquearBotoes(bloquear) {
    document.getElementById('buscarInfo').disabled = bloquear;
    document.getElementById('downloadFormato').disabled = bloquear;
    document.getElementById('downloadConversor').disabled = bloquear;
}

function acompanharProgresso(downloadId, botao, textoFinal, avisoId) {
    const eventSource = new EventSource(`/progress/${downloadId}`);

    eventSource.onmessage = (event) => {
        const data = JSON.parse(event.data);

        if (data.status === 'downloading') {
            botao.textContent = `Baixando... ${data.percent}%`;
        } else if (data.status === 'converting') {
            botao.textContent = `Convertendo... ${data.percent}%`;
        } else if (data.status === 'finished') {
            eventSource.close();
            sessionStorage.removeItem('activeDownload');

            botao.textContent = 'Pronto!';
            document.getElementById(avisoId).textContent =
                'Faça download do arquivo no final da página';

            document.getElementById('downloadLinkBox').innerHTML =
                `<p>Clique no link abaixo para fazer o download:</p>
                 <a id="finalDownloadLink" href="/file/${downloadId}" download="${data.filename}"> ${data.filename}</a>`;

            document
                .getElementById('finalDownloadLink')
                .addEventListener('click', () => {
                    bloquearBotoes(false);
                    botao.textContent = textoFinal;
                    document.getElementById(avisoId).textContent = '';
                    document.getElementById('finalDownloadLink').style.color =
                        'gray';
                });
        } else if (data.status === 'error') {
            eventSource.close();
            sessionStorage.removeItem('activeDownload');
            alert(`Erro: ${data.error}`);
            bloquearBotoes(false);
            botao.textContent = textoFinal;
        }
    };

    eventSource.onerror = () => {
        eventSource.close();
    };
}

function iniciarAcompanhamento(downloadId, tipo) {
    sessionStorage.setItem(
        'activeDownload',
        JSON.stringify({ downloadId, tipo }),
    );

    const botao =
        tipo === 'formato'
            ? document.getElementById('downloadFormato')
            : document.getElementById('downloadConversor');
    const avisoId =
        tipo === 'formato' ? 'avisoDownloadFormato' : 'avisoDownloadConversor';
    const textoFinal =
        tipo === 'formato' ? 'Download' : 'Converter e fazer Download';

    acompanharProgresso(downloadId, botao, textoFinal, avisoId);
}

function buscarInfo() {
    const agora = Date.now();
    if (agora - ultimoEnvio < 10000) {
        const restante = Math.ceil((10000 - (agora - ultimoEnvio)) / 1000);
        alert(`Aguarde ${restante}s antes de tentar de novo.`);
        return;
    }

    const videoUrl = document.getElementById('videoUrl').value;

    if (!videoUrl.trim()) {
        alert('Cole a URL do vídeo antes de enviar.');
        return;
    }

    ultimoEnvio = agora;

    const botao = document.getElementById('buscarInfo');
    botao.textContent = 'Buscando...';
    botao.disabled = true;

    fetch(`/info?url=${encodeURIComponent(videoUrl)}`)
        .then((response) => response.json())
        .then((data) => {
            document.getElementById('infoBox').innerHTML = `
               <img src="${data.thumbnail}" alt="Thumbnail do vídeo" style="width: 320px; max-width: 100%; border-radius: 6px; display: block; margin-bottom: 12px;">
                <h2>${data.title}</h2>
                <p><strong>Canal:</strong> ${data.channel}</p>
                <p><strong>Duração:</strong> ${formatarDuracao(data.duration)}</p>
                <p><strong>Visualizações:</strong> ${data.view_count.toLocaleString('pt-BR')}</p>
                <p><strong>Descrição:</strong> ${data.description}</p>
            `;

            const audioFormats = data.formats.filter(
                (f) => f.resolution === 'audio only',
            );
            const videoFormats = data.formats.filter(
                (f) => f.resolution !== 'audio only',
            );

            document.getElementById('infoVideo').innerHTML =
                `<h3>Vídeo</h3>` +
                videoFormats
                    .map(
                        (f) =>
                            `<label><input type="radio" name="formato" value="${f.format_id}"> ${f.resolution} (${f.ext})</label>`,
                    )
                    .join('<br>');

            document.getElementById('infoAudio').innerHTML =
                `<h3>Áudio</h3>` +
                audioFormats
                    .map(
                        (f) =>
                            `<label><input type="radio" name="formato" value="${f.format_id}"> ${f.ext} - ${Math.round(f.abr)}kbps (${(f.filesize / 1024 / 1024).toFixed(1)}MB)</label>`,
                    )
                    .join('<br>');

            botao.textContent = 'Enviar';
            botao.style.backgroundColor = '';
            botao.disabled = false;
        })
        .catch((error) => {
            console.error('Erro ao buscar informações:', error);
            document.getElementById('infoBox').innerHTML =
                `<p>Erro ao buscar informações do vídeo.</p>`;

            botao.textContent = 'Enviar';
            botao.style.backgroundColor = '';
            botao.disabled = false;
        });
}

function downloadFormato() {
    const videoUrl = document.getElementById('videoUrl').value;
    const formatoSelecionado = document.querySelector(
        'input[name="formato"]:checked',
    );

    if (!formatoSelecionado) {
        alert('Selecione um formato antes de baixar.');
        return;
    }

    bloquearBotoes(true);
    document.getElementById('downloadFormato').textContent = 'Iniciando...';

    fetch('/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            url: videoUrl,
            format_id: formatoSelecionado.value,
        }),
    })
        .then((response) => response.json())
        .then((data) => {
            iniciarAcompanhamento(data.download_id, 'formato');
        })
        .catch((error) => {
            console.error('Erro ao iniciar download:', error);
            alert('Erro ao iniciar download.');
            bloquearBotoes(false);
            document.getElementById('downloadFormato').textContent = 'Download';
        });
}

function downloadConversor() {
    const videoUrl = document.getElementById('videoUrl').value;
    const formatoSelecionado = document.querySelector(
        'input[name="formato"]:checked',
    );
    const targetFormatSelecionado = document.querySelector(
        'input[name="targetFormat"]:checked',
    );

    if (!formatoSelecionado) {
        alert('Selecione um formato de origem antes de converter.');
        return;
    }
    if (!targetFormatSelecionado) {
        alert('Selecione um formato de destino antes de converter.');
        return;
    }

    bloquearBotoes(true);
    document.getElementById('downloadConversor').textContent = 'Iniciando...';

    fetch('/download-convertido', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            url: videoUrl,
            format_id: formatoSelecionado.value,
            target_format: targetFormatSelecionado.value,
        }),
    })
        .then((response) => response.json())
        .then((data) => {
            iniciarAcompanhamento(data.download_id, 'conversor');
        })
        .catch((error) => {
            console.error('Erro ao iniciar conversão:', error);
            alert('Erro ao iniciar conversão.');
            bloquearBotoes(false);
            document.getElementById('downloadConversor').textContent =
                'Converter e fazer Download';
        });
}

function formatarDuracao(segundos) {
    const h = Math.floor(segundos / 3600);
    const m = Math.floor((segundos % 3600) / 60);
    const s = segundos % 60;

    if (h > 0) {
        return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    }
    return `${m}:${String(s).padStart(2, '0')}`;
}

document.addEventListener('DOMContentLoaded', () => {
    carregarFormatosConversao();

    const salvo = sessionStorage.getItem('activeDownload');
    if (salvo) {
        const { downloadId, tipo } = JSON.parse(salvo);
        bloquearBotoes(true);
        iniciarAcompanhamento(downloadId, tipo);
    }
});

function carregarFormatosConversao() {
    const status = document.getElementById('backendStatus');

    fetch('/conversion-formats')
        .then((response) => {
            if (!response.ok) throw new Error('Backend respondeu com erro');
            return response.json();
        })
        .then((data) => {
            document.getElementById('conversorVideo').innerHTML =
                `<h3>Vídeo</h3>` +
                data.video
                    .map(
                        (fmt) =>
                            `<label><input type="radio" name="targetFormat" value="${fmt}"> ${fmt}</label>`,
                    )
                    .join('<br>');

            document.getElementById('conversorAudio').innerHTML =
                `<h3>Áudio</h3>` +
                data.audio
                    .map(
                        (fmt) =>
                            `<label><input type="radio" name="targetFormat" value="${fmt}"> ${fmt}</label>`,
                    )
                    .join('<br>');
        })
        .catch((error) => {
            status.innerHTML = `
                <strong>Backend não está conectado!</strong><br>
                - O servidor não está rodando<br>
                - Porta errada<br>
                - CORS bloqueando requisição<br>
                (veja o Console, F12)
            `;
            status.style.color = 'red';
        });
}
