# Instalação guiada com o Claude in Chrome

Este ficheiro tem um prompt para colar na extensão **Claude in Chrome**. Com ele, o Claude
instala a app no GCP e liga `eventos.marcaserio.pt` pela Cloudflare. Tudo é feito no teu
Chrome, com as tuas sessões iniciadas.

## Antes de começar

1. Abre o Chrome com sessão iniciada em:
   - **Google Cloud**: https://console.cloud.google.com, com a conta que vai pagar a VM;
   - **Cloudflare**: https://dash.cloudflare.com;
   - **GitHub**: https://github.com.
2. Abre a extensão Claude in Chrome, no ícone da barra do Chrome, e cola o prompt abaixo.
3. Fica por perto. O Claude vai parar e pedir-te confirmação:
   - antes de criar a VM, porque é aí que começa a ter custos;
   - para escolheres o projeto GCP;
   - para escreveres tu a palavra-passe de administração, que ele nunca vê.

Demora cerca de 20 a 30 minutos.

---

## Prompt (copiar a partir daqui)

```text
Vais instalar a app "marcAsério Check-in" no Google Cloud e publicá-la em
https://eventos.marcaserio.pt através de um Cloudflare Tunnel. O código está no repositório
privado github.com/tiagomendesmarques/marcaserio-eventos (lê GUIA-ONLINE.md e a pasta gcp/
se precisares de detalhes). Trabalha em separadores novos e fala comigo em português.

REGRAS (obrigatórias)
1. Cloudflare: o domínio marcaserio.pt fica INTACTO. Só podes criar o registo DNS "eventos"
   (criado pelo próprio túnel). Não edites nem apagues nenhum outro registo (raiz, www, MX,
   TXT, etc.). Não alteres definições da zona: SSL/TLS, Bot Fight Mode, WAF, regras, cache,
   redirecionamentos, Access.
   Se já existir um registo "eventos", NÃO o apagues: mostra-me o tipo e o valor e espera
   pela minha decisão.
2. GCP: usa só o projeto que eu confirmar. Não alteres recursos que já existam nesse projeto
   (VMs, redes, regras de firewall, IAM). Pede a minha confirmação antes de criar a VM,
   porque tem custos (cerca de 10-12 €/mês).
3. Palavras-passe: nunca as inventes, escrevas ou guardes. Quando um comando pedir uma
   palavra-passe, para e diz-me para a escrever eu no terminal.
4. Se algo falhar 2 vezes seguidas, para e explica-me o erro em vez de improvisar.

PASSO 0. Verificação inicial
- Abre https://marcaserio.pt e anota o que aparece (para confirmar no fim que não mudou).
- Na Cloudflare, abre a zona marcaserio.pt → DNS → Records. Diz-me se já existe algum registo
  "eventos" (tipo e valor). Não alteres nada.

PASSO 1. Projeto GCP
- Na consola do Google Cloud, mostra-me os projetos disponíveis e pergunta-me qual usar, ou
  se queres criar um novo chamado "marcaserio-eventos".
- Confirma que o projeto tem faturação ativa (Billing). Se não tiver, para e diz-me.

PASSO 2. Código no Cloud Shell
- Abre o Cloud Shell nesse projeto (ícone ">_" no topo da consola). Corre:
    ssh-keygen -t ed25519 -N "" -f ~/.ssh/marcaserio_deploy -C cloudshell-marcaserio
    cat ~/.ssh/marcaserio_deploy.pub
- No GitHub, em https://github.com/tiagomendesmarques/marcaserio-eventos/settings/keys,
  adiciona uma Deploy key com o título "Cloud Shell" e essa chave pública.
  NÃO ativas "Allow write access".
- No Cloud Shell:
    GIT_SSH_COMMAND="ssh -i ~/.ssh/marcaserio_deploy -o StrictHostKeyChecking=accept-new" \
      git clone git@github.com:tiagomendesmarques/marcaserio-eventos.git ~/marcaserio-eventos
    cd ~/marcaserio-eventos
    git config core.sshCommand "ssh -i ~/.ssh/marcaserio_deploy"

PASSO 3. Criar a VM (pede-me confirmação antes)
- No Cloud Shell:  bash gcp/1_criar_vm.sh
  (cria uma VM e2-micro Debian 12 em europe-southwest1-a, sem portas web abertas,
  e um snapshot diário do disco)

PASSO 4. Instalar a app
- bash gcp/2_publicar.sh
- Deve terminar com o serviço "marcaserio-checkin" ativo.

PASSO 5. Palavra-passe de administração
- bash gcp/definir_admin.sh
- Quando aparecer "Nova palavra-passe de administração:", PARA e diz-me para a escrever eu
  (mínimo de 10 caracteres). Continua só quando eu disser.

PASSO 6. Cloudflare Tunnel
- Na Cloudflare, abre Zero Trust → Networks → Tunnels → Create a tunnel → Cloudflared,
  com o nome "marcaserio-eventos".
- No passo de instalação aparece "cloudflared service install <TOKEN>". Copia só o token
  (começa por "eyJ") e corre no Cloud Shell:
    bash gcp/3_ligar_cloudflare.sh <TOKEN>
- Espera que o túnel apareça como HEALTHY / Connected.
- Adiciona um Public Hostname (ou "Published application route"):
    Subdomain: eventos · Domain: marcaserio.pt · Path: vazio
    Service Type: HTTP · URL: localhost:8770
  Se a Cloudflare disser que o registo já existe, PARA e pergunta-me (regra 1).

PASSO 7. Verificação final
- No Cloud Shell:  bash gcp/verificar.sh
  Os dois serviços devem aparecer "active"; https://eventos.marcaserio.pt/ deve responder 200.
- Abre https://eventos.marcaserio.pt num separador novo: deve aparecer o ecrã
  "Escolher evento" da marcAsério.
- Abre https://marcaserio.pt e confirma que está igual ao que viste no passo 0.
- Na Cloudflare → DNS, confirma que o único registo novo é "eventos" (CNAME para
  ...cfargotunnel.com, Proxied) e que não mudou mais nada.
- No fim, faz-me um resumo: projeto, VM, zona, estado do túnel, o que foi criado
  e o que ficou por fazer.
```

---

## Depois da instalação

- **Criar eventos:** abre https://eventos.marcaserio.pt e entra em **Administração de eventos**.
- **No dia do evento:** no Mac ligado à Honeywell, abre `Abrir_Agente_Impressao.command`.
  Ver o [GUIA-ONLINE.md](GUIA-ONLINE.md).
- **Atualizar a app:** no Cloud Shell, corre `cd ~/marcaserio-eventos && git pull && bash gcp/2_publicar.sh`.
