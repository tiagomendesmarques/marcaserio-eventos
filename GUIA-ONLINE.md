# marcAsério Check-in online: GCP + Cloudflare

Põe a app em **https://eventos.marcaserio.pt**, acessível a partir de qualquer dispositivo:
portátil, tablet ou telemóvel das hospedeiras.

## Como funciona

```
 Hospedeiras (qualquer browser)
        │  https://eventos.marcaserio.pt
        ▼
   Cloudflare ──── Cloudflare Tunnel ────►  VM no GCP (Madrid)
                                            app + base de dados SQLite
                                                 ▲
                                                 │ HTTPS (só ligações de saída)
   Mac do evento + Honeywell ── agente_impressao.py
```

- **A VM não tem portas abertas para a web.** A app só escuta em `127.0.0.1` e o tráfego
  entra pelo Cloudflare Tunnel. O HTTPS fica a cargo da Cloudflare, por isso não há
  certificados para gerir.
- **A impressora continua ligada ao Mac do evento.** O servidor gera os mesmos comandos
  Direct Protocol (a função da v2.6 não muda). O **agente de impressão**, que corre no Mac,
  vai buscá-los e envia-os para a Honeywell com o mesmo comando `lp`.
- **Os dados dos participantes ficam na UE**, numa VM em Madrid (`europe-southwest1`).

## O que precisas

1. Um **projeto GCP com faturação ativa**.
2. Acesso ao **painel da Cloudflare** do domínio `marcaserio.pt`.
3. Um sítio onde correr os scripts:
   - o **Cloud Shell** do GCP (o terminal no browser, já com `gcloud`). É o caminho recomendado
     e o que o [SETUP-CHROME.md](SETUP-CHROME.md) segue com o Claude in Chrome;
   - ou o Terminal do Mac, com o Google Cloud CLI (`brew install --cask google-cloud-sdk`,
     seguido de `gcloud auth login`).

> **O domínio `marcaserio.pt` não é alterado.** Só é criado o registo DNS `eventos`.
> Nenhuma definição da zona é mexida (SSL, Bot Fight Mode, WAF, regras).

Custo aproximado: entre 10 e 12 € por mês (VM e2-micro, disco de 10 GB, IP público e
snapshots). Confirma em https://cloud.google.com/products/calculator.

## Instalação

Corre todos os comandos a partir desta pasta.

### 1. Configurar

No Cloud Shell, o projeto ativo é usado automaticamente. No Mac, indica-o à frente de cada
comando: `PROJECT=id-do-projeto bash gcp/...`.

### 2. Criar a VM

```bash
bash gcp/1_criar_vm.sh
```

Este script cria a VM (Debian 12, Madrid) e um **snapshot diário do disco**, guardado 14 dias.

### 3. Instalar a app

```bash
bash gcp/2_publicar.sh
```

Da primeira vez, o `gcloud` pode pedir para criar uma chave SSH. Aceita.

### 4. Carregar os dados

**Opção A: levar os dados atuais** (eventos, participantes, check-ins e passwords).
Fecha a app local e corre:

```bash
bash gcp/enviar_base_dados.sh ../marcAserio_CheckIn_v1_1/marcaserio_checkin.sqlite3
```

**Opção B: começar do zero.** Define a palavra-passe de administração diretamente no servidor:

```bash
bash gcp/definir_admin.sh
```

Na versão online não é possível criar a administração pelo browser. Se fosse, a primeira
pessoa a abrir o site ficava com ela.

### 5. Ligar o domínio (Cloudflare Tunnel)

1. No painel da Cloudflare, em **DNS** de `marcaserio.pt`, vê se já existe um registo `eventos`.
   O túnel precisa de criar o seu próprio registo. Se já houver um, confirma que não está a ser
   usado e apaga **só esse**. Não mexas nos outros registos.
2. Abre **Zero Trust → Networks → Tunnels → Create a tunnel**, escolhe **Cloudflared** e dá-lhe
   o nome `marcaserio-eventos`.
3. No passo de instalação aparece um comando `cloudflared service install eyJ...`.
   **Copia só o token**, ou seja, o texto longo que começa por `eyJ`.
4. Em **Public Hostname**, preenche:
   - Subdomain: `eventos`
   - Domain: `marcaserio.pt`
   - Service: `HTTP` → `localhost:8770`

   Guarda.
5. No Mac, corre:

   ```bash
   bash gcp/3_ligar_cloudflare.sh eyJ...o-teu-token...
   ```

6. Quando o túnel aparecer como **HEALTHY** no painel, corre `bash gcp/verificar.sh`
   e abre https://eventos.marcaserio.pt

> Os nomes dos menus da Cloudflare mudam de vez em quando. Se não encontrares algum,
> procura "Tunnels" no painel Zero Trust.

## No dia do evento

1. Liga a Honeywell ao Mac, como até aqui.
2. Abre **`Abrir_Agente_Impressao.command`**, escolhe o evento e introduz a palavra-passe do evento.
3. Deixa a janela aberta. O Mac não adormece enquanto o agente estiver a correr.
4. No topo da app, a indicação da impressora passa a **«Impressora ligada»**.
   Se mostrar **«Impressora desligada»**, verifica o agente e a internet do Mac.

Notas:

- **Usa um agente por evento.** Com dois agentes ligados ao mesmo evento, as etiquetas
  dividem-se entre as duas impressoras.
- **Se o agente estiver desligado, as etiquetas ficam em fila durante 15 minutos.**
  Imprimem assim que o agente voltar. Depois desse tempo são descartadas, para não saírem
  dezenas de etiquetas de surpresa; nesse caso usa **Reimprimir badge**.
- Várias hospedeiras podem fazer check-in ao mesmo tempo. A lista atualiza-se a cada 10 segundos.

## Manutenção

| Para…                              | Corre                                                                                     |
|------------------------------------|-------------------------------------------------------------------------------------------|
| Atualizar a app (código novo)      | `bash gcp/2_publicar.sh`                                                                   |
| Descarregar cópia da base de dados | `bash gcp/descarregar_base_dados.sh`                                                       |
| Repor a palavra-passe de admin     | `bash gcp/definir_admin.sh`                                                                |
| Verificar servidor e domínio       | `bash gcp/verificar.sh`                                                                    |
| Ver os registos do servidor        | `gcloud compute ssh marcaserio-eventos --zone europe-southwest1-a -- sudo journalctl -u marcaserio-checkin -f` |

Cópias de segurança:

- **Snapshots diários** do disco da VM, guardados 14 dias. Estão em Compute Engine → Snapshots.
- **Antes de cada «Substituir lista»**, é guardada uma cópia em `/var/lib/marcaserio/copias_seguranca/`.
- Antes de cada evento, descarrega também uma cópia para o Mac.

## Segurança

A app passa a estar na internet. Por isso, na versão online:

- **Usa palavras-passe de evento fortes**, com 12 ou mais caracteres, e muda-as depois de cada evento.
- O mesmo endereço IP só pode falhar a palavra-passe **10 vezes em 15 minutos**.
- Os cookies de sessão só circulam em HTTPS. Os pedidos vindos de outros sites são recusados.
- A VM não tem conta de serviço do GCP; não precisa de nenhuma API do Google.

## Problemas comuns

- **O agente mostra «Resposta HTTP 403 inesperada».** Provavelmente a proteção anti-bots
  da Cloudflare está a bloquear o agente. Antes de mudar alguma coisa, vê o motivo em
  **Security → Events**. Prefere uma exceção limitada a `eventos.marcaserio.pt`, por exemplo
  uma regra WAF com `http.host eq "eventos.marcaserio.pt"` e um *User-Agent* que contém
  `marcAserio-agente`. O *Bot Fight Mode* aplica-se à zona inteira: desligá-lo afeta também
  `marcaserio.pt`.
- **O agente dá um erro de certificado SSL.** Corre o *Install Certificates.command*
  que está na pasta do Python, em Aplicações.
- **O site mostra «Bad gateway» (erro 502).** A app não está a correr. Vê os registos
  do servidor (tabela acima) e corre `bash gcp/2_publicar.sh` de novo.
- **Não há internet no local do evento.** Plano B: abre a versão local
  (`Abrir_marcAserio_CheckIn.command`) com uma cópia descarregada da base de dados.
  No fim, envia-a de volta com `enviar_base_dados.sh`. Atenção: isto **substitui** os dados
  do servidor; não os junta.
