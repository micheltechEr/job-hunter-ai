# Job Hunter AI

Sistema automatizado com IA para busca, análise de compatibilidade de vagas e envio human-in-the-loop de currículos personalizados via Gmail API.

## Funcionalidades

- **Parser com LLM:** Extrai e normaliza automaticamente o conteúdo de currículos em formato PDF.
- **RAG & Embeddings:** Representa o currículo como dados estruturados e vetoriais para matching semântico.
- **AI Matching & Score ATS:** Calcula relevância ponderada por IA e emite relatórios justificando pontos fortes e gaps de competências.
- **Fila Assíncrona de ATS (`ATSWorkerQueue`):** Cadastro e ingestão de vagas respondem instantaneamente (<10ms), enquanto o cálculo ATS e elaboração de cartas rodam em segundo plano com concorrência controlada.
- **Busca em Tempo Real Não-Bloqueante:** Pesquisa multi-campo por cargo, empresa, localização ou stack no backend (`GET /api/jobs/?search=...`) e live search no frontend enquanto o ATS processa novas vagas.
- **Cache por Hash SHA-256:** Cache em memória para vetores de embeddings, análise de descrições e resultados de matching, eliminando chamadas de API repetidas e acelerando o pipeline.
- **Proteção contra Rate Limit:** Algoritmo Token Bucket assíncrono e backoff exponencial com jitter para LLM e API REST.
- **OAuth 2.0 Gmail integration:** Permite autenticar diretamente sua conta do Gmail para envio de candidaturas utilizando escopos seguros (`gmail.send` e `userinfo.email`).
- **Human-in-the-Loop:** Dashboard web local para gerenciamento: pesquisar vagas em tempo real, revisar informações, ajustar textos e aprovar envios manuais.

---

## Funcionamento Matemático e Algorítmico

O Job Hunter AI emprega uma modelagem matemática em múltiplos estágios para triagem, ranqueamento vetorial, ponderação ATS, controle de vazão de requisições e caching determinístico.

### 1. Similaridade Vetorial por Cosseno (Embedding Semantic Search)
Para pré-selecionar e ranquear o currículo mais adequado a uma determinada vaga antes da análise profunda, ambos os textos são projetados no espaço vetorial $\mathbb{R}^{d}$ ($d = 1536$):

$$\text{sim}(\vec{u}, \vec{v}) = \frac{\vec{u} \cdot \vec{v}}{\|\vec{u}\|_2 \|\vec{v}\|_2} = \frac{\sum_{i=1}^d u_i v_i}{\sqrt{\sum_{i=1}^d u_i^2} \sqrt{\sum_{i=1}^d v_i^2}}$$

Onde:
- $\vec{u} \in \mathbb{R}^{1536}$: Vetor embedding normalizado do título, empresa e descrição da vaga (com hash SHA-256 em cache).
- $\vec{v} \in \mathbb{R}^{1536}$: Vetor embedding dos dados estruturados do currículo.
- $\text{sim}(\vec{u}, \vec{v}) \in [-1, 1]$: Métrica de proximidade semântica angular.

---

### 2. Motor de Pontuação Ponderada ATS (Applicant Tracking System)

O score de compatibilidade final $S_{\text{ATS}} \in [0, 100]$ é calculado como uma combinação linear convexa de 5 critérios objetivos:

$$S_{\text{ATS}} = w_1 C_{\text{hard}} + w_2 C_{\text{exp}} + w_3 C_{\text{arch}} + w_4 C_{\text{loc}} + w_5 C_{\text{diff}}$$

Com os pesos oficiais parametrizados:
- $w_1 = 0.40$: Casamento de Hard Skills & Stack Técnica Obrigatória.
- $w_2 = 0.25$: Senioridade & Anos de Experiência Prática.
- $w_3 = 0.15$: Responsabilidades, Domínio Arquitetural e Entregas.
- $w_4 = 0.10$: Localização, Modalidade (Remoto/Híbrido) e Idiomas.
- $w_5 = 0.10$: Diferenciais e Requisitos Desejáveis (*Nice to have*).

#### Critério de Corte e Decisão:
- $S_{\text{ATS}} \ge 80$: **HIGH_MATCH** $\rightarrow$ Recomendação ativa e geração automática de rascunho de candidatura.
- $60 \le S_{\text{ATS}} < 80$: **GOOD_MATCH** $\rightarrow$ Alinhamento favorável.
- $40 \le S_{\text{ATS}} < 60$: **REVIEW** $\rightarrow$ Avaliação manual necessária.
- $S_{\text{ATS}} < 40$: **IGNORE** $\rightarrow$ Descarte por falta de requisitos essenciais.

---

### 3. Fila Assíncrona e Caching Determinístico (Zero-Block)

Para permitir que o usuário pesquise e navegue pelas vagas sem latência enquanto novas oportunidades são processadas:
1. **Gravação Imediata:** O endpoint `POST /api/jobs/` e o ingestor do scraper salvam a entidade `Job` com status `ANALYZING` no banco e retornam imediatamente.
2. **Worker Pool Assíncrono (`ATSWorkerQueue`):** Gerencia uma fila de tarefas com limite de concorrência ($N = 2$), executando a extração estruturada, o matching e a confecção do rascunho de forma isolada.
3. **Cache SHA-256 em Memória:** As chaves de cache são derivadas do hash criptográfico dos textos normalizados:
   $$K_{\text{embed}} = \text{SHA256}(\text{norm}(text))$$
   $$K_{\text{match}} = \text{SHA256}(\text{job\_id} \mathbin{\Vert} \text{job\_text} \mathbin{\Vert} \text{resume\_id} \mathbin{\Vert} \text{profile\_sig})$$
   Garantindo reutilização instantânea ($O(1)$) com zero chamadas à API da OpenAI/OmniRoute em duplicatas.

---

### 4. Controle de Taxa (Token Bucket) e Backoff Exponencial

Para respeitar as cotas de RPM (Requests Per Minute) da API de LLM e evitar erros 429:

#### Taxa de Preenchimento:
$$r = \frac{R_{\text{rpm}}}{60} \quad (\text{tokens/segundo})$$

#### Backoff com Full Jitter:
$$D(n) = \min\left(D_{\max}, \; D_{\text{base}} \cdot 2^{n-1} + \mathcal{U}(0.5, 2.0)\right)$$

Onde $n$ é o número da tentativa atual e $\mathcal{U}(a, b)$ é uma variável aleatória uniforme para evitar o problema de *thundering herd*.

---

## Arquitetura de Pastas

- `app/main.py`: Ponto de entrada do FastAPI, inicialização de banco, workers da fila ATS e middlewares.
- `app/database.py`: Inicialização do SQLAlchemy e conexões assíncronas SQLite/PostgreSQL.
- `app/config.py`: Parser de configurações e variáveis de ambiente do `.env`.
- `app/api/`: Controladores REST (vagas com busca instantânea, currículos, candidaturas, Gmail OAuth).
- `app/services/ats_queue.py`: Fila assíncrona e pool de workers em background para análise ATS.
- `app/services/rate_limiter.py`: Token bucket assíncrono e controle de taxa de requisições.
- `app/services/llm_service.py`: Camada de comunicação com OpenAI/LLM com retry automático.
- `app/services/embedding_service.py`: Motor de álgebra vetorial, cosine similarity e cache de embeddings.
- `app/services/job_analysis.py`: Extração estruturada de requisitos de vagas com cache por hash.
- `app/services/matching.py`: Engine de triagem ATS, cruzamento heurístico de hard skills e ranqueamento.
- `app/services/scraper_service.py`: Scrapers com Playwright para LinkedIn, Programathor e Gupy com ingestão não-bloqueante.
- `app/services/scheduler.py`: Agendador periódico APScheduler de buscas automáticas.
- `app/templates/`: Central dashboard em HTML Jinja2 com busca e filtros reativos em tempo real.

---

## Pré-requisitos & Instalação

1. Clone o repositório ou navegue até a pasta:
   ```bash
   cd "C:\Users\Ângelo Miguel\Desktop\job-hunter-ai"
   ```

2. Instale as dependências com `uv` (ou `pip`):
   ```bash
   uv pip install -r requirements.txt
   ```

3. Configure o arquivo `.env` (use o `.env.example` como base).

4. **Autenticação com Gmail:**
   - Acesse o Console de APIs do Google Cloud.
   - Habilite a **Gmail API**.
   - Crie uma tela de consentimento OAuth e adicione os escopos `https://www.googleapis.com/auth/gmail.send` e `https://www.googleapis.com/auth/userinfo.email`.
   - Adicione a URI de redirecionamento autorizada: `http://localhost:8000/api/gmail/callback`.
   - Faça o download do arquivo de Client OAuth2 JSON e salve como `credentials.json` na raiz do projeto.

---

## Executando o Projeto

Para iniciar o servidor FastAPI localmente:

```bash
uv run uvicorn app.main:app --reload --port 8000
```

Para rodar a suíte de testes unitários:

```bash
uv run python -m unittest discover -s tests -p "test_*.py"
```

Acesse o dashboard pelo navegador:
- [http://localhost:8000](http://localhost:8000)
