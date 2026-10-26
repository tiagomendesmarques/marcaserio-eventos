# marcAsério Check-in online: Cloud Run + Firestore

A app fica em **https://eventos.marcaserio.pt**, sem servidores para manter e sem custo fixo.
Só corre quando alguém a está a usar.

## Como funciona

```
 Hospedeiras (qualquer browser)
        │  https://eventos.marcaserio.pt
        │  (DNS na Cloudflare: CNAME → Google, «DNS only»)
        ▼
   Cloud Run «marcaserio-eventos» (europe-west1)  ── arranca a pedido, 0 a 3 contentores
        │  conta de serviço com acesso só a esta base de dados
        ▼
   Firestore «marcaserio-eventos» (europe-west1)  ── eventos, participantes, fila de impressão
        ▲                                             cópia de segurança diária, 14 dias
        │ HTTPS (só ligações de saída)
   Mac do evento + Honeywell ── agente_impressao.py
```

- **Cloud Run:** corre a app num contentor que só arranca quando há pedidos. Sem uso, desliga-se
  e não custa nada. O primeiro acesso depois de parada demora 1 a 2 segundos.
- **Firestore:** usa uma base de dados própria, `marcaserio-eventos`. É separada da `(default)`
  e de qualquer outra que o projeto já tenha.
- **Check-ins em simultâneo:** cada check-in é uma transação. Se duas hospedeiras fizerem o
  check-in da mesma pessoa ao mesmo tempo, só uma passa e só sai uma etiqueta.
- **Atualização automática:** a lista atualiza-se a cada 10 segundos em todos os dispositivos.
  Só é pedido o que mudou desde a última atualização.
- **Impressão:** a Honeywell continua ligada ao Mac do evento. O servidor gera os mesmos
  comandos Direct Protocol (a função da v2.6 não muda). O **agente de impressão** vai buscá-los
  e envia-os com o mesmo `lp`.
- **Dados na UE:** tanto o Cloud Run como o Firestore ficam na Bélgica (`europe-west1`).

### O que é criado no projeto GCP

| Recurso | Nome |
|---|---|
| Serviço Cloud Run | `marcaserio-eventos` |
| Base de dados Firestore (Native) | `marcaserio-eventos`, com proteção contra eliminação |
| Agendamento de cópias do Firestore | diário, guardado 14 dias |
| Conta de serviço | `marcaserio-eventos@<projeto>.iam.gserviceaccount.com`, só com acesso à base de dados acima |
| Imagens do contentor | Artifact Registry `cloud-run-source-deploy`, criado pelo `gcloud run deploy` |

Nada do que já existe no projeto é alterado.

### O que muda na Cloudflare

- **Um registo CNAME novo:** `eventos` → `ghs.googlehosted.com`, com *Proxy status*
  **DNS only** (nuvem cinzenta). O certificado HTTPS é da Google.
- **Um registo TXT novo na raiz**, para a Google Search Console confirmar que o domínio é teu.
- **O registo A de `marcaserio.pt`, o site atual, não é tocado.** Também não muda nenhuma
  definição da zona.

### Custo

Fica tipicamente em **cêntimos por mês**:

- **Cloud Run:** cabe no escalão gratuito.
- **Firestore:** uma base de dados com nome não tem escalão gratuito, mas um evento faz poucas
  dezenas de milhares de leituras, o que dá cêntimos.
- **Cópias de segurança e imagens:** cêntimos.

Confirma em https://cloud.google.com/products/calculator.

## Instalação

Corre todos os comandos no **Cloud Shell** do projeto: o ícone `>_` na consola do Google Cloud.
O [SETUP-CHROME.md](SETUP-CHROME.md) faz tudo isto com o Claude in Chrome.

```bash
git clone <este repositório> ~/marcaserio-eventos && cd ~/marcaserio-eventos

bash gcp/1_preparar.sh       # APIs, Firestore, cópias diárias, conta de serviço
bash gcp/2_publicar.sh       # constrói e publica no Cloud Run (3 a 5 minutos)
bash gcp/definir_admin.sh    # escreves tu a palavra-passe de administração
bash gcp/3_dominio.sh        # verificação do domínio e ligação de eventos.marcaserio.pt
```

Notas sobre o `3_dominio.sh`:

- **Da primeira vez, mostra o link da Search Console.** Ela pede um registo **TXT** na
  Cloudflare. Depois de o domínio ser verificado, corre o script outra vez.
- **Depois mostra o registo a criar na Cloudflare:** `eventos` → `ghs.googlehosted.com`,
  como **DNS only**.
- **O certificado HTTPS demora entre 15 e 60 minutos.** Entretanto, `bash gcp/verificar.sh`
  mostra o estado.

### Trazer os dados da versão local (opcional)

Envia o ficheiro `marcaserio_checkin.sqlite3` para o Cloud Shell: menu ⋮ → **Upload**.
Depois corre:

```bash
bash gcp/migrar_sqlite.sh ~/marcaserio_checkin.sqlite3
```

O script copia:

- a palavra-passe de administração, se ainda não houver uma;
- os eventos, com as respetivas palavras-passe;
- os participantes e os check-ins.

Pode ser repetido: os eventos que já existam são ignorados.

## No dia do evento

1. Liga a Honeywell ao Mac, como até aqui.
2. Abre **`Abrir_Agente_Impressao.command`**, escolhe o evento e introduz a palavra-passe do evento.
3. Deixa a janela aberta. O Mac não adormece enquanto o agente estiver a correr.
4. No topo da app, a indicação da impressora passa a **«Impressora ligada»**.

Notas:

- **Usa um agente por evento.** Com dois agentes ligados ao mesmo evento, as etiquetas
  dividem-se entre as duas impressoras.
- **Se o agente estiver desligado, as etiquetas ficam em fila durante 15 minutos.**
  Depois disso são descartadas; nesse caso usa **Reimprimir badge**.
- **Para eliminar o arranque de 1 a 2 segundos durante o evento**, podes manter um contentor
  sempre ligado e, no fim, voltar ao normal. Enquanto estiver ligado, custa menos de 1 € por dia:

  ```bash
  gcloud run services update marcaserio-eventos --region europe-west1 --min-instances 1   # durante o evento
  gcloud run services update marcaserio-eventos --region europe-west1 --min-instances 0   # no fim
  ```

## Manutenção

| Para…                             | Corre no Cloud Shell                                   |
|-----------------------------------|--------------------------------------------------------|
| Publicar uma versão nova          | `git pull && bash gcp/2_publicar.sh`                   |
| Repor a palavra-passe de admin    | `bash gcp/definir_admin.sh`                            |
| Ver o estado                      | `bash gcp/verificar.sh`                                |
| Ver os registos da app            | Consola → Cloud Run → marcaserio-eventos → Logs        |

Cópias de segurança:

- **Firestore:** uma cópia completa por dia, guardada 14 dias. Está em Consola → Firestore →
  marcaserio-eventos → Disaster recovery. Uma cópia restaura-se para uma base de dados nova.
- **Antes de cada «Substituir lista»:** a lista anterior fica guardada no Firestore, em
  `events/<evento>/lists/<versão anterior>`.
- **Exportação manual:** «Exportar CSV com check-ins», na app.

## Segurança

- **Usa palavras-passe de evento fortes**, com 12 ou mais caracteres, e muda-as depois de cada evento.
- **Mudar a palavra-passe de um evento termina as sessões** abertas desse evento.
- **Tentativas falhadas:** o mesmo endereço IP só pode falhar 10 vezes em 15 minutos.
- **Sessões:** são cookies assinados, que só circulam em HTTPS. Os pedidos vindos de outros
  sites são recusados.
- **Administração:** não se cria pelo browser; define-se com `gcp/definir_admin.sh`.
- **Acesso ao projeto:** a conta de serviço só tem acesso à base de dados `marcaserio-eventos`,
  e não ao resto do projeto.

## Problemas comuns

- **`gcloud run deploy` falha com um erro de permissões no Cloud Build.** Em projetos recentes,
  o Cloud Build usa a conta de serviço predefinida do Compute Engine. Dá-lhe o papel
  *Cloud Build Service Account*:

  ```bash
  N=$(gcloud projects describe $(gcloud config get-value project) --format='value(projectNumber)')
  gcloud projects add-iam-policy-binding $(gcloud config get-value project) \
    --member="serviceAccount:$N-compute@developer.gserviceaccount.com" --role=roles/cloudbuild.builds.builder
  ```

- **`--allow-unauthenticated` é recusado por uma política da organização.** A organização
  proíbe serviços públicos (*Domain restricted sharing*). É preciso uma exceção para este
  projeto, definida em Organization Policies.
- **Cloudflare:** se o certificado não sair ou aparecer «too many redirects», confirma que o
  registo `eventos` está como **DNS only**, com a nuvem cinzenta.
- **O agente mostra «Resposta HTTP 403 inesperada».** Confirma o endereço: por omissão é
  https://eventos.marcaserio.pt. Para usar outro, corre o agente com `MA_URL=... python3 agente_impressao.py`.
- **O agente dá um erro de certificado SSL no Mac.** Corre o *Install Certificates.command*
  que está na pasta do Python, em Aplicações.
- **Não há internet no local do evento.** Plano B: abre a versão local
  (`Abrir_marcAserio_CheckIn.command`), que funciona com SQLite no Mac. Os dados ficam separados
  dos da versão online: os check-ins feitos offline não passam sozinhos para a versão online.
  Fica com o CSV exportado como registo.
