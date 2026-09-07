# Job Hunter AI

Sistema automatizado com IA para busca, análise de compatibilidade de vagas e envio human-in-the-loop de currículos personalizados via Gmail API.

## Funcionalidades

- **Parser com LLM:** Extrai e normaliza automaticamente o conteúdo de currículos em formato PDF.
- **RAG & Embeddings:** Representa o currículo como dados estruturados e vetoriais para matching semântico.
- **AI Matching & Score:** Calcula relevância ponderada por IA e emite relatórios justificando pontos fortes e gaps de competências.
- **Proteção contra Rate Limit:** Algoritmo Token Bucket assíncrono e backoff exponencial com jitter para LLM e API REST.
- **OAuth 2.0 Gmail integration:** Permite autenticar diretamente sua conta do Gmail para envio.
- **Human-in-the-Loop:** Dashboard web local para gerenciamento: revisar informações, ajustar textos e aprovar envios manuais.

---

## Funcionamento Matemático e Algorítmico

O Job Hunter AI emprega uma modelagem matemática em múltiplos estágios para triagem, ranqueamento vetorial, ponderação ATS e controle de vazão de requisições.

### 1. Similaridade Vetorial por Cosseno (Embedding Semantic Search)
Para pré-selecionar e ranquear o currículo mais adequado a uma determinada vaga antes da análise profunda, ambos os textos são projetados no espaço vetorial $\mathbb{R}^{d}$ ($d = 1536$):

$$\text{sim}(\vec{u}, \vec{v}) = \frac{\vec{u} \cdot \vec{v}}{\|\vec{u}\|_2 \|\vec{v}\|_2} = \frac{\sum_{i=1}^d u_i v_i}{\sqrt{\sum_{i=1}^d u_i^2} \sqrt{\sum_{i=1}^d v_i^2}}$$

Onde:
- $\vec{u} \in \mathbb{R}^{1536}$: Vetor embedding normalizado do título, empresa e descrição da vaga.
- $\vec{v} \in \mathbb{R}^{1536}$: Vetor embedding dos dados estruturados do currículo.
- $\text{sim}(\vec{u}, \vec{v}) \in [-1, 1]$: Métrica de proximidade semântica angular.

---

### 2. Motor de Pontuação Ponderada ATS (Applicant Tracking System)
A avaliação profunda decompõe os requisitos do cargo em 5 dimensões quantitativas com pesos calibrados para padrões de mercado:

$$S_{\text{total}} = \sum_{k=1}^{5} w_k \cdot S_k$$

$$\text{Sendo } \sum_{k=1}^5 w_k = 1.00 \quad (100\%)$$

| Dimensão ($k$) | Fator Avaliado | Peso ($w_k$) | Escala ($S_k$) |
| :--- | :--- | :---: | :---: |
| **1. Hard Skills & Stack Técnica** | Linguagens, frameworks, bancos de dados, cloud e arquitetura | **0.40** (40%) | $0 \dots 100$ |
| **2. Senioridade & Tempo de Experiência** | Nível de maturidade (Jr/Pl/Sr/Lead) e anos de atuação | **0.25** (25%) | $0 \dots 100$ |
| **3. Responsabilidades & Domínio** | Vivência nas atribuições práticas exigidas pela vaga | **0.15** (15%) | $0 \dots 100$ |
| **4. Localização, Modalidade & Idiomas** | Alinhamento de modelo de trabalho (Remoto/Híbrido) e fluência | **0.10** (10%) | $0 \dots 100$ |
| **5. Diferenciais & Nice-to-Have** | Certificações, metodologias e habilidades complementares | **0.10** (10%) | $0 \dots 100$ |

#### Equação Expandida do Score Final:
$$S_{\text{total}} = 0.40 \cdot S_{\text{tech}} + 0.25 \cdot S_{\text{exp}} + 0.15 \cdot S_{\text{arch}} + 0.10 \cdot S_{\text{loc}} + 0.10 \cdot S_{\text{diff}}$$

---

### 3. Função de Decisão e Classificação de Fit
Com base no escore contínuo $S_{\text{total}} \in [0, 100]$, o motor classifica o candidato na seguinte função por partes:

$$\text{Fit}(S_{\text{total}}) = \begin{cases} 
\text{HIGH\_MATCH} & \text{se } S_{\text{total}} \ge 80 \\
\text{GOOD\_MATCH} & \text{se } 65 \le S_{\text{total}} < 80 \\
\text{REVIEW} & \text{se } 45 \le S_{\text{total}} < 65 \\
\text{IGNORE} & \text{se } S_{\text{total}} < 45
\end{cases}$$

- **HIGH_MATCH & GOOD_MATCH:** Recomendação automática habilitada para geração de proposta e e-mail.
- **REVIEW:** Encaminhado para aprovação com alerta de gap de requisitos.
- **IGNORE:** Descartado para economizar tempo e taxa de envio do usuário.

---

### 4. Controle de Vazão e Resiliência (Token Bucket & Backoff)

#### Algoritmo Token Bucket Contínuo:
Controla a taxa de requisições por minuto (RPM) para prevenir *rate limit breach*:

$$T(t) = \min\left(C, \; T(t_{\text{prev}}) + (t - t_{\text{prev}}) \cdot \frac{C}{60}\right)$$

Se $T(t) < 1.0$, o tempo de espera forçado é:
$$\Delta t_{\text{sleep}} = \frac{1.0 - T(t)}{r} \quad \text{onde } r = \frac{C}{60} \text{ tokens/s}$$

#### Backoff Exponencial com Jitter Decorrelacionado:
Em caso de erro transitório ou resposta HTTP 429 da LLM:

$$D(n) = \min\left(D_{\max}, \; D_{\text{base}} \cdot 2^{n-1} + \mathcal{U}(0.5, 2.0)\right)$$

Onde $n$ é o número da tentativa atual e $\mathcal{U}(a, b)$ é uma variável aleatória uniforme para evitar tempestade de requisições simultâneas (*thundering herd*).

---

## Arquitetura de Pastas

- `app/main.py`: Ponto de entrada do FastAPI e middlewares de taxa.
- `app/database.py`: Inicialização do SQLAlchemy e conexões assíncronas.
- `app/config.py`: Parser de variáveis do `.env`.
- `app/api/`: Controladores REST (vagas, currículos, candidaturas, Gmail).
- `app/services/rate_limiter.py`: Token bucket assíncrono e backoff de rede.
- `app/services/llm_service.py`: Camada de comunicação com OpenAI/LLM.
- `app/services/embedding_service.py`: Motor de álgebra vetorial e similaridade.
- `app/services/matching.py`: Engine de triagem ATS e ranqueamento.
- `app/templates/`: Central dashboard em HTML Jinja2.

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
   - Crie uma tela de consentimento OAuth e adicione escopo `https://www.googleapis.com/auth/gmail.send`.
   - Adicione URI de redirecionamento autorizada: `http://localhost:8000/api/gmail/callback`.
   - Faça o download do arquivo de Client OAuth2 JSON e salve como `credentials.json` na raiz do projeto.

---

## Executando o Projeto

Para iniciar o servidor FastAPI localmente:

```bash
uv run uvicorn app.main:app --reload --port 8000
```

Acesse o dashboard pelo navegador:
- [http://localhost:8000](http://localhost:8000)
