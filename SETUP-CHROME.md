# Instalação guiada com o Claude in Chrome

Este ficheiro tem um prompt para colar na extensão **Claude in Chrome**. Com ele, o Claude
instala a app no **Cloud Run + Firestore**, no projeto `website-marcaserio-pt`, e liga
`eventos.marcaserio.pt` pela Cloudflare. Tudo é feito no teu Chrome, com as tuas sessões
iniciadas. A arquitetura está explicada no [GUIA-ONLINE.md](GUIA-ONLINE.md).

## Antes de começar

1. Abre o Chrome com sessão iniciada em:
   - **Google Cloud**: https://console.cloud.google.com;
   - **Cloudflare**: https://dash.cloudflare.com;
   - **GitHub**: https://github.com.
2. Abre a extensão Claude in Chrome, no ícone da barra do Chrome, e cola o prompt abaixo.
3. Fica por perto. O Claude vai parar e pedir-te confirmação:
   - antes de criar recursos no projeto `website-marcaserio-pt`;
   - para escreveres tu a palavra-passe de administração, que ele nunca vê;
   - se encontrar um registo `eventos` que já exista na Cloudflare.

Demora cerca de 30 minutos, mais o tempo de emissão do certificado (15 a 60 minutos).

---

## Prompt (copiar a partir daqui)

```text
Vais publicar a app "marcAsério Check-in" no Google Cloud (Cloud Run + Firestore) e ligá-la a
https://eventos.marcaserio.pt. O código está no repositório privado
github.com/tiagomendesmarques/marcaserio-eventos (lê GUIA-ONLINE.md e a pasta gcp/ se
precisares de detalhes). Trabalha em separadores novos e fala comigo em português.

REGRAS (obrigatórias)
1. Cloudflare: o registo A de marcaserio.pt (o site atual) NÃO pode ser alterado nem apagado.
   Só podes CRIAR: o registo TXT de verificação que a Google Search Console pedir, e o
   registo CNAME "eventos" → ghs.googlehosted.com, com Proxy status "DNS only" (nuvem cinzenta).
   Não alteres nenhum outro registo nem nenhuma definição da zona (SSL/TLS, Bot Fight Mode,
   WAF, regras, cache, redirecionamentos).
   Se já existir um registo "eventos", NÃO o apagues: mostra-me o tipo e o valor e espera.
2. GCP: usa o projeto "website-marcaserio-pt". Esse projeto pode alojar o site marcaserio.pt,
   por isso NÃO alteres, pares, reinicies nem apagues nada do que já lá existe (VMs, outros
   serviços Cloud Run, App Engine, load balancers, buckets, a base de dados Firestore
   "(default)" ou outras, redes, firewall, permissões existentes). Os scripts só CRIAM:
   o serviço Cloud Run "marcaserio-eventos", a base de dados Firestore "marcaserio-eventos",
   um agendamento de cópias, a conta de serviço "marcaserio-eventos" e a respetiva permissão,
   e (passo 6) o repositório de imagens "marcaserio-eventos", a conta "marcaserio-deployer",
   o pool de Workload Identity "github" (se ainda não existir) e o provider "marcaserio-eventos".
3. Palavras-passe: nunca as inventes, escrevas ou guardes. Quando um comando pedir uma,
   para e diz-me para a escrever eu no terminal.
4. Se algo falhar 2 vezes seguidas, para e explica-me o erro em vez de improvisar.

PASSO 0. Verificação inicial (só ler)
- Abre https://marcaserio.pt e anota o que aparece.
- Na Cloudflare, abre a zona marcaserio.pt → DNS → Records. Anota o registo A de
  marcaserio.pt (valor e proxy) e diz-me se já existe algum registo "eventos".
- Na consola do Google Cloud, seleciona o projeto "website-marcaserio-pt". Diz-me o ID do
  projeto, se a faturação está ativa e se já existe algum serviço Cloud Run ou base de dados
  Firestore chamado "marcaserio-eventos". Se existir, para e pergunta-me.
- Pede-me confirmação para avançar.

PASSO 1. Código no Cloud Shell
- Abre o Cloud Shell no projeto (ícone ">_"). Confirma com:  gcloud config get-value project
  (se não for o ID certo:  gcloud config set project <ID>). Depois corre:
    ssh-keygen -t ed25519 -N "" -f ~/.ssh/marcaserio_deploy -C cloudshell-marcaserio
    cat ~/.ssh/marcaserio_deploy.pub
- No GitHub, em https://github.com/tiagomendesmarques/marcaserio-eventos/settings/keys,
  adiciona uma Deploy key com o título "Cloud Shell" e essa chave pública
  (SEM "Allow write access").
- No Cloud Shell:
    GIT_SSH_COMMAND="ssh -i ~/.ssh/marcaserio_deploy -o StrictHostKeyChecking=accept-new" \
      git clone git@github.com:tiagomendesmarques/marcaserio-eventos.git ~/marcaserio-eventos
    cd ~/marcaserio-eventos
    git config core.sshCommand "ssh -i ~/.ssh/marcaserio_deploy"

PASSO 2. Preparar o projeto
- bash gcp/1_preparar.sh
  (ativa as APIs Cloud Run, Firestore, Cloud Build e Artifact Registry; cria a base de dados
  Firestore "marcaserio-eventos" em europe-west1, as cópias diárias e a conta de serviço)
- Na consola: Firestore → base de dados "marcaserio-eventos" → Rules. As regras devem
  negar todo o acesso direto:  allow read, write: if false;
  Se estiverem diferentes, substitui-as por estas e publica (só nesta base de dados):
    rules_version = '2';
    service cloud.firestore {
      match /databases/{database}/documents {
        match /{document=**} { allow read, write: if false; }
      }
    }

PASSO 3. Publicar no Cloud Run
- bash gcp/2_publicar.sh   (demora 3 a 5 minutos; no fim mostra um endereço *.run.app)
- Se falhar com um erro de permissões do Cloud Build, vê "Problemas comuns" no GUIA-ONLINE.md,
  aplica a correção indicada e tenta outra vez.
- Abre o endereço *.run.app num separador: deve aparecer o ecrã "Escolher evento" da marcAsério.

PASSO 4. Palavra-passe de administração
- bash gcp/definir_admin.sh
- Quando aparecer "Nova palavra-passe de administração:", PARA e diz-me para a escrever eu
  (mínimo de 10 caracteres). Continua só quando eu disser.

PASSO 5. Domínio
- bash gcp/3_dominio.sh
- Da primeira vez, o script mostra um link da Google Search Console para verificar
  marcaserio.pt. Abre-o, escolhe a verificação por registo DNS TXT e cria esse TXT na
  Cloudflare (regra 1: só um registo novo, sem mexer no A). Conclui a verificação na
  Search Console e corre de novo:  bash gcp/3_dominio.sh
- O script mostra o registo a criar. Na Cloudflare, cria:
    Type: CNAME · Name: eventos · Target: ghs.googlehosted.com · Proxy status: DNS only
- O certificado demora entre 15 e 60 minutos. Vai correndo  bash gcp/verificar.sh  de 10 em 10
  minutos até https://eventos.marcaserio.pt/ responder 200.

PASSO 6. Publicação automática pelo GitHub
- bash gcp/4_ligar_github.sh
- O script mostra 3 valores. No GitHub, em
  https://github.com/tiagomendesmarques/marcaserio-eventos/settings/variables/actions,
  cria 3 "Repository variables" (separador Variables, NÃO Secrets) com esses nomes e valores:
  GCP_PROJECT_ID, GCP_WIF_PROVIDER, GCP_DEPLOYER_SA.
- Em https://github.com/tiagomendesmarques/marcaserio-eventos/actions, abre o workflow
  "Publicar no Cloud Run", carrega em "Run workflow" (ramo main) e espera que fique verde.
  No passo "Verificar" deve aparecer "No ar: ... (versão <commit>)".
  Se falhar, mostra-me o erro do passo que falhou.

PASSO 7. Verificação final
- Abre https://eventos.marcaserio.pt: deve aparecer o ecrã "Escolher evento".
- Abre https://marcaserio.pt e confirma que está igual ao que viste no passo 0.
- Na Cloudflare → DNS, confirma que o registo A de marcaserio.pt está igual ao do passo 0
  e que os únicos registos novos são o CNAME "eventos" (DNS only) e o TXT de verificação.
- No fim, faz-me um resumo: o que foi criado, os endereços, o estado do certificado e o que
  ficou por fazer.
```

---

## Depois da instalação

- **Criar eventos:** abre https://eventos.marcaserio.pt e entra em **Administração de eventos**.
- **Trazer os dados da versão local** (opcional): ver «Trazer os dados da versão local» no
  [GUIA-ONLINE.md](GUIA-ONLINE.md).
- **No dia do evento:** no Mac ligado à Honeywell, abre `Abrir_Agente_Impressao.command`.
- **Atualizar a app:** basta um `git push` para o `main`; o GitHub Actions testa e publica sozinho.
  À mão, no Cloud Shell: `cd ~/marcaserio-eventos && git pull && bash gcp/2_publicar.sh`.
