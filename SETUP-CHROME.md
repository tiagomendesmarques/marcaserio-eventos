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
   - para colares tu o *Client secret* do login com Google, que ele nunca vê;
   - se encontrar um registo `eventos` que já exista na Cloudflare.
4. No fim, entra em https://eventos.marcaserio.pt com o teu Google (`tiago.marques@marcaserio.pt`).
   Ficas logo como administrador e podes convidar as hospedeiras em **Gestão → Utilizadores**.

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
   (passo 5) o cliente OAuth "marcAsério Check-in" e os segredos "marcaserio-google-client-id"
   e "marcaserio-google-client-secret", e (passo 6) o repositório de imagens
   "marcaserio-eventos", a conta "marcaserio-deployer", o pool de Workload Identity "github"
   (se ainda não existir) e o provider "marcaserio-eventos".
   Se o ecrã de consentimento OAuth (Google Auth Platform) do projeto JÁ estiver configurado,
   não lhe alteres nada: só crias o cliente novo.
3. Palavras-passe e segredos: nunca os inventes, escrevas ou guardes. Quando um comando pedir
   uma palavra-passe ou o Client secret, para e diz-me para o escrever eu no terminal.
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
  (ativa as APIs Cloud Run, Firestore, Cloud Build, Artifact Registry e Secret Manager; cria a
  base de dados Firestore "marcaserio-eventos" em europe-west1, as cópias diárias, a limpeza
  automática dos registos ao fim de 12 meses e a conta de serviço)
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
- Abre o endereço *.run.app num separador: deve aparecer o ecrã "Entrar" da marcAsério.
  (Ainda não dá para entrar: o login com Google fica ativo no passo 5.)

PASSO 4. Domínio
- bash gcp/3_dominio.sh
- Da primeira vez, o script mostra um link da Google Search Console para verificar
  marcaserio.pt. Abre-o, escolhe a verificação por registo DNS TXT e cria esse TXT na
  Cloudflare (regra 1: só um registo novo, sem mexer no A). Conclui a verificação na
  Search Console e corre de novo:  bash gcp/3_dominio.sh
- O script mostra o registo a criar. Na Cloudflare, cria:
    Type: CNAME · Name: eventos · Target: ghs.googlehosted.com · Proxy status: DNS only
- O certificado demora entre 15 e 60 minutos. Vai correndo  bash gcp/verificar.sh  de 10 em 10
  minutos até https://eventos.marcaserio.pt/ responder 200. Podes avançar para o passo 5
  entretanto.

PASSO 5. Login com Google
- Na consola, abre "Google Auth Platform" (APIs & Services → OAuth consent screen).
  Se ainda não estiver configurado:
    Branding: App name "marcAsério Check-in", User support email tiago.marques@marcaserio.pt.
    Audience: "Internal". Se só houver "External", escolhe External e depois "Publish app".
    Contact information: tiago.marques@marcaserio.pt.
  Se já estiver configurado (regra 2), não lhe mexas e diz-me o que lá está.
- Clients → Create client:
    Application type: Web application · Name: marcAsério Check-in
    Authorized redirect URIs: https://eventos.marcaserio.pt/auth/google/callback
  Carrega em Create. Aparece uma janela com o Client ID e o Client secret: NÃO a feches
  (o Client secret só é mostrado uma vez).
- No Cloud Shell, corre:  bash gcp/5_login_google.sh
  Escreve o Client ID quando o pedir. Quando pedir o "Client secret", PARA (regra 3): diz-me
  para eu copiar o Client secret da janela do Google e colá-lo no terminal.
- O script guarda os dois valores no Secret Manager e volta a publicar a app (3 a 5 minutos).

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
- Abre https://eventos.marcaserio.pt: deve aparecer o ecrã "Entrar", com o botão
  "Entrar com Google (@marcaserio.pt)". Diz-me para eu entrar com o meu Google e confirmar
  que fico como Administrador (não entres tu por mim).
- Abre https://marcaserio.pt e confirma que está igual ao que viste no passo 0.
- Na Cloudflare → DNS, confirma que o registo A de marcaserio.pt está igual ao do passo 0
  e que os únicos registos novos são o CNAME "eventos" (DNS only) e o TXT de verificação.
- No fim, faz-me um resumo: o que foi criado, os endereços, o estado do certificado e o que
  ficou por fazer.
```

---

## Depois da instalação

- **Entrar:** em https://eventos.marcaserio.pt, com o Google. Tu e o Miguel são administradores;
  as outras contas @marcaserio.pt entram como editores.
- **Criar eventos e convidar hospedeiras:** **Gestão → Eventos** e **Gestão → Utilizadores**
  (email, palavra-passe e eventos). Os registos estão em **Gestão → Registos**.
- **Trazer os dados da versão local** (opcional): ver «Trazer os dados da versão local» no
  [GUIA-ONLINE.md](GUIA-ONLINE.md).
- **No dia do evento:** no Mac ligado à Honeywell, abre `Abrir_Agente_Impressao.command` e entra
  com uma conta com acesso ao evento (sugestão: uma hospedeira `impressora@marcaserio.pt`).
- **Atualizar a app:** basta um `git push` para o `main`; o GitHub Actions testa e publica sozinho.
  À mão, no Cloud Shell: `cd ~/marcaserio-eventos && git pull && bash gcp/2_publicar.sh`.
