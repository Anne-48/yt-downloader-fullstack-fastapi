// Rate limit simples (10s)
let ultimoEnvio = 0;

// Guarda lista de legendas disponíveis do último /info chamado para renderizar checkboxes quando usuário escolher "mkv"
let captionsAtuais = [];

function bloquearBotoes(bloquear) {
  // Trava/destrava os três botões principais de uma vez, usado enquanto download/conversão está em andamento
  document.getElementById('buscarInfo').disabled = bloquear;
  document.getElementById('downloadFormato').disabled = bloquear;
  document.getElementById('downloadConversor').disabled = bloquear;
}

function renderizarLegendas() {
  // Monta checkboxes de legenda a partir de captionsAtuais
  // Cada legenda vira um <input type="checkbox">, todos com name="legenda"
  // (assim dá pra selecionar várias ao mesmo tempo)
  const legendasOpcoes = document.getElementById('legendasOpcoes');

  if (captionsAtuais.length === 0) {
    legendasOpcoes.innerHTML =
      '<p>Nenhuma legenda disponível para esse vídeo.</p>';
    return;
  }

  legendasOpcoes.innerHTML = captionsAtuais
    .map((c) => {
      const tipo = c.type === 'human' ? 'manual' : 'automática';
      // "pt" genérico não distingue Brasil de Portugal - avisa o usuário
      const aviso =
        c.lang === 'pt'
          ? ` <small>- youtube usa 'pt' como genérico (chance de ser pt portugal)</small>`
          : '';
      return `<label><input type="checkbox" name="legenda" value="${c.lang}"> ${c.lang} (${tipo})${aviso}</label>`;
    })
    .join('<br>');
}

function atualizarVisibilidadeLegendas() {
  // Chamado toda vez que o usuário clica num radio de formato de destino
  // Legenda só é aceita quando o alvo selecionado é "mkv"
  // Mostra aviso explicando isso
  const targetSelecionado = document.querySelector(
    'input[name="targetFormat"]:checked',
  );
  const aviso = document.getElementById('legendasAviso');
  const opcoes = document.getElementById('legendasOpcoes');

  if (targetSelecionado && targetSelecionado.value === 'mkv') {
    aviso.style.display = 'none';
    opcoes.style.display = 'block';
    renderizarLegendas();
  } else {
    aviso.style.display = 'block';
    opcoes.style.display = 'none';
    opcoes.innerHTML = '';
  }
}

function acompanharProgresso(downloadId, botao, textoFinal, avisoId) {
  // Abre uma conexão SSE (Server-Sent Events) com o backend
  // Fica escutando atualizações de progresso até "finished" ou "error"
  const eventSource = new EventSource(`/progress/${downloadId}`);

  eventSource.onmessage = (event) => {
    const data = JSON.parse(event.data);

    if (data.status === 'downloading') {
      botao.textContent = `Baixando... ${data.percent}%`;
    } else if (data.status === 'converting') {
      botao.textContent = `Convertendo... ${data.percent}%`;
    } else if (data.status === 'finished') {
      // fecha conexão SSE ao terminar download/conversão, logo depois mostra o link de download
      eventSource.close();
      sessionStorage.removeItem('activeDownload');

      botao.textContent = '✅ Pronto!';
      document.getElementById(avisoId).textContent =
        'Faça download do arquivo no final da página';

      document.getElementById('downloadLinkBox').innerHTML =
        `<p>Clique no link abaixo para fazer o download:</p>
                 <a id="finalDownloadLink" href="/file/${downloadId}" download="${data.filename}">📥 ${data.filename}</a>`;

      // Destrava botões e reseta texto quando usuário clicar no link
      document
        .getElementById('finalDownloadLink')
        .addEventListener('click', () => {
          bloquearBotoes(false);
          botao.textContent = textoFinal;
          document.getElementById(avisoId).textContent = '';
          document.getElementById('finalDownloadLink').style.color = 'gray'; // marca visualmente "já clicado"
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
  // Guarda no sessionStorage (sobrevive a reload, mas some se a aba fechar)
  // Para reconectar ao SSE automaticamente se o usuário der F5
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

  // Rate limit simples: não deixa mandar (outra url) novamente antes de 10s desde o último envio
  if (agora - ultimoEnvio < 10000) {
    const restante = Math.ceil((10000 - (agora - ultimoEnvio)) / 1000);
    alert(`Aguarde ${restante}s antes de tentar de novo.`);
    return;
  }

  const inputUrl = document.getElementById('videoUrl');
  const videoUrl = inputUrl.value;

  if (!videoUrl.trim()) {
    alert('Cole a URL do vídeo antes de enviar.');
    return;
  }

  ultimoEnvio = agora;

  const botao = document.getElementById('buscarInfo');
  botao.textContent = 'Buscando...';
  botao.disabled = true;
  inputUrl.classList.remove('input-erro'); // limpa qualquer erro visual de uma tentativa anterior

  fetch(`/info?url=${encodeURIComponent(videoUrl)}`)
    .then((response) => {
      // Fetch cai no .catch em falha de REDE — erro HTTP (400, etc.)
      // Ainda conta como "sucesso" pro fetch, por isso checa response.ok manualmente
      if (!response.ok) {
        return response.json().then((err) => {
          throw new Error(err.detail || 'URL inválida');
        });
      }
      return response.json();
    })
    .then((data) => {
      document.getElementById('infoBox').innerHTML = `
                <img src="${data.thumbnail}" alt="Thumbnail do vídeo" style="width: 320px; max-width: 100%; border-radius: 6px; display: block; margin-bottom: 12px;">
                <h3>${data.title}</h3>
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

      // Agrupa os formatos de vídeo por resolução, mantendo a ordem em que chegaram
      const gruposPorResolucao = {};
      videoFormats.forEach((f) => {
        if (!gruposPorResolucao[f.resolution]) {
          gruposPorResolucao[f.resolution] = [];
        }
        gruposPorResolucao[f.resolution].push(f);
      });

      document.getElementById('infoVideo').innerHTML =
        `<h4>Vídeo</h4>` +
        Object.entries(gruposPorResolucao)
          .map(([resolucao, formatos]) => {
            const linhas = formatos
              .map((f) => {
                // "avc1.4d401e" -> "avc1" (só o nome base do codec, sem detalhes de perfil)
                const codecSimplificado = f.vcodec
                  ? f.vcodec.split('.')[0]
                  : '?';
                return `<label><input type="radio" name="formato" value="${f.format_id}"> ${f.resolution} ${f.ext} (codec ${codecSimplificado} | ${(f.filesize / 1024 / 1024).toFixed(1)}MB)</label>`;
              })
              .join('<br>');
            // <hr> separa visualmente cada grupo de resolução do próximo
            return `<p><strong>${resolucao}</strong></p>${linhas}`;
          })
          .join('<hr>');

      document.getElementById('infoAudio').innerHTML =
        `<h4>Áudio</h4>` +
        audioFormats
          .map(
            (f) =>
              `<label><input type="radio" name="formato" value="${f.format_id}"> ${f.ext} - ${Math.round(f.abr)}kbps (${(f.filesize / 1024 / 1024).toFixed(1)}MB)</label>`,
          )
          .join('<br>');

      // Guarda as legendas disponíveis pra usar depois, quando/se o usuário escolher "mkv" como formato de conversão
      captionsAtuais = data.captions || [];
      // Reflete a lista nova de legendas na hora, caso "mkv" já esteja selecionado de uma busca anterior
      atualizarVisibilidadeLegendas();

      inputUrl.classList.remove('input-erro');
      botao.textContent = 'Enviar';
      botao.style.backgroundColor = '';
      botao.disabled = false;
    })
    .catch((error) => {
      console.error('Erro ao buscar informações:', error);
      document.getElementById('infoBox').innerHTML =
        `<p>Erro ao buscar informações do vídeo.</p>`;
      inputUrl.classList.add('input-erro'); // deixa a borda do input vermelha

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
      // Backend responde na hora com só o download_id, o processo de verdade roda em background acompanhado via SSE
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

  // Pega todos os checkboxes de legenda marcados
  // Só existe/importa quando o target é "mkv"
  // Box mostra o aviso em vez das opções, lista lista vem vazia
  const legendasMarcadas = Array.from(
    document.querySelectorAll('input[name="legenda"]:checked'),
  ).map((el) => el.value);

  bloquearBotoes(true);
  document.getElementById('downloadConversor').textContent = 'Iniciando...';

  fetch('/download-convertido', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      url: videoUrl,
      format_id: formatoSelecionado.value,
      target_format: targetFormatSelecionado.value,
      // Só manda o campo "subtitles" se realmente tiver alguma marcada, undefined faz o JSON.stringify omitir o campo
      subtitles: legendasMarcadas.length > 0 ? legendasMarcadas : undefined,
    }),
  })
    .then((response) => {
      if (!response.ok) {
        // Erros aqui são imediatos (ex: 400 "legenda só com mkv")
        return response.json().then((err) => {
          throw new Error(err.detail || 'Erro desconhecido');
        });
      }
      return response.json();
    })
    .then((data) => {
      iniciarAcompanhamento(data.download_id, 'conversor');
    })
    .catch((error) => {
      console.error('Erro ao iniciar conversão:', error);
      alert(`Erro: ${error.message}`);
      bloquearBotoes(false);
      document.getElementById('downloadConversor').textContent =
        'Converter e fazer Download';
    });
}

function formatarDuracao(segundos) {
  // Converte segundos (número puro que o backend manda) em "m:ss" ou "h:mm:ss"
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

  // Se usuário recarregou a página no meio de um download/conversão reconecta no SSE automaticamente até o GRACE_PERIOD expirar
  const salvo = sessionStorage.getItem('activeDownload');
  if (salvo) {
    const { downloadId, tipo } = JSON.parse(salvo);
    bloquearBotoes(true);
    iniciarAcompanhamento(downloadId, tipo);
  }
});

function carregarFormatosConversao() {
  // Roda uma vez ao carregar a página. Busca do backend quais formatos de conversão existem e monta os radios.
  const status = document.getElementById('backendStatus');

  fetch('/conversion-formats')
    .then((response) => {
      if (!response.ok) throw new Error('Backend respondeu com erro');
      return response.json();
    })
    .then((data) => {
      document.getElementById('conversorVideo').innerHTML =
        `<h4>Vídeo</h4>` +
        data.video
          .map(
            (fmt) =>
              `<label><input type="radio" name="targetFormat" value="${fmt}" onchange="atualizarVisibilidadeLegendas()"> ${fmt}</label>`,
          )
          .join('<br>');

      document.getElementById('conversorAudio').innerHTML =
        `<h4>Áudio</h4>` +
        data.audio
          .map(
            (fmt) =>
              `<label><input type="radio" name="targetFormat" value="${fmt}" onchange="atualizarVisibilidadeLegendas()"> ${fmt}</label>`,
          )
          .join('<br>');

      atualizarVisibilidadeLegendas();
    })
    .catch((error) => {
      // Se isso falhar, é o sinal mais confiável de "backend não está rodando"
      status.innerHTML = `
                <strong>Backend não conectado!</strong><br>
                - O servidor não está rodando<br>
                - Porta errada<br>
                - CORS bloqueando requisição<br>
                (veja o Console, F12)
            `;
      status.style.color = 'red';
    });
}
