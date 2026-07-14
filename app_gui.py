"""
Interface Gráfica (GUI) para o Conversor PDFTOMD usando Streamlit.

Permite configurar pastas, ajustar o paralelismo, iniciar a conversão,
acompanhar os logs em tempo real e ler os arquivos Markdown gerados.
"""

import os
import sys
import time
import logging
import threading
from pathlib import Path
import streamlit as st

# Garante que o diretório atual está no PATH
diretorio_script = Path(__file__).resolve().parent
sys.path.append(str(diretorio_script))

from converter import converter_alta_precisao, FORMATOS_SUPORTADOS

# Configurações de layout e visual do Streamlit
st.set_page_config(
    page_title="PDFTOMD — Conversor Universal",
    page_icon="📖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Estilização CSS customizada para visual Premium e Moderno (Rich Aesthetics)
st.markdown(
    """
    <style>
    /* Dark theme styling updates */
    .stApp {
        background-color: #0f172a;
        color: #f1f5f9;
    }
    div[data-testid="stSidebar"] {
        background-color: #1e293b;
        border-right: 1px solid #334155;
    }
    .stButton>button {
        background: linear-gradient(135deg, #3b82f6, #1d4ed8);
        color: white;
        font-weight: bold;
        border-radius: 8px;
        padding: 0.5rem 2rem;
        border: none;
        transition: transform 0.1s ease, box-shadow 0.1s ease;
    }
    .stButton>button:hover {
        transform: translateY(-1px);
        box-shadow: 0 4px 12px rgba(59, 130, 246, 0.4);
        color: white;
    }
    code {
        color: #38bdf8;
    }
    .metric-card {
        background-color: #1e293b;
        padding: 1.5rem;
        border-radius: 12px;
        border: 1px solid #334155;
        text-align: center;
        margin-bottom: 1rem;
    }
    .metric-val {
        font-size: 2rem;
        font-weight: bold;
        color: #60a5fa;
        margin-top: 0.5rem;
    }
    </style>
    """,
    unsafe_allow_html=True
)

# ==============================================================================
# PROVEDOR DE LOGS EM TEMPO REAL
# ==============================================================================

if "logs" not in st.session_state:
    st.session_state.logs = []
if "running" not in st.session_state:
    st.session_state.running = False
if "sucessos" not in st.session_state:
    st.session_state.sucessos = 0
if "falhas" not in st.session_state:
    st.session_state.falhas = 0
if "totais" not in st.session_state:
    st.session_state.totais = 0

class StreamlitLogHandler(logging.Handler):
    """Handler de logging que direciona as mensagens diretamente para a sessão do Streamlit."""
    def emit(self, record):
        msg = self.format(record)
        st.session_state.logs.append(msg)

# Registra o handler customizado no logger do conversor
logger = logging.getLogger("converter")
handler = StreamlitLogHandler()
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(handler)
logger.setLevel(logging.INFO)


# ==============================================================================
# RENDERIZAÇÃO DA INTERFACE
# ==============================================================================

# Cabeçalho Premium
st.markdown(
    """
    <div style="background: linear-gradient(135deg, #1e293b, #0f172a); padding: 2rem; border-radius: 12px; margin-bottom: 2rem; border: 1px solid #334155">
        <h1 style="color: #60a5fa; margin: 0; font-family: 'Inter', sans-serif; font-size: 2.5rem;">📖 PDFTOMD</h1>
        <p style="color: #94a3b8; margin: 5px 0 0 0; font-size: 1.1rem;">Pré-processador Universal e Conversor de Documentos de Alta Performance</p>
    </div>
    """,
    unsafe_allow_html=True
)

# Barra Lateral (Sidebar) com Configurações
st.sidebar.markdown("### ⚙️ Painel de Controle")

caminho_entrada_padrao = Path(__file__).resolve().parent / "pdf_entrada"
caminho_saida_padrao = Path(__file__).resolve().parent / "markdown_saida"

in_dir = st.sidebar.text_input("Pasta de Entrada (Documentos)", str(caminho_entrada_padrao))
out_dir = st.sidebar.text_input("Pasta de Saída (Markdown)", str(caminho_saida_padrao))

# Extensões Suportadas exibidas de forma clara
st.sidebar.markdown(
    f"""
    **Formatos suportados:**
    `{', '.join([ext.replace('*.', '').upper() for ext in FORMATOS_SUPORTADOS])}`
    """
)

st.sidebar.markdown("---")

workers = st.sidebar.slider("Threads Concorrentes", min_value=1, max_value=8, value=4, help="Número de arquivos sendo processados ao mesmo tempo.")
dry_run = st.sidebar.checkbox("Modo Simulação (Dry Run)", value=False, help="Lista os arquivos a processar sem convertê-los de verdade.")

st.sidebar.markdown("---")

# Botões de Ação na Sidebar
iniciar_btn = st.sidebar.button("🚀 Iniciar Conversão", disabled=st.session_state.running)
if st.sidebar.button("🔄 Limpar Logs", disabled=st.session_state.running):
    st.session_state.logs = []
    st.rerun()


# ==============================================================================
# CORPO PRINCIPAL
# ==============================================================================

# Cria colunas para os cards de métricas
col1, col2, col3 = st.columns(3)

with col1:
    st.markdown(
        f"""
        <div class="metric-card">
            <div style="color: #94a3b8; font-weight: bold;">ARQUIVOS FILTRADOS</div>
            <div class="metric-val">{st.session_state.totais}</div>
        </div>
        """,
        unsafe_allow_html=True
    )
with col2:
    st.markdown(
        f"""
        <div class="metric-card">
            <div style="color: #4ade80; font-weight: bold;">CONVERSÕES BEM SUCEDIDAS</div>
            <div class="metric-val" style="color: #4ade80;">{st.session_state.sucessos}</div>
        </div>
        """,
        unsafe_allow_html=True
    )
with col3:
    st.markdown(
        f"""
        <div class="metric-card">
            <div style="color: #f87171; font-weight: bold;">FALHAS REGISTRADAS</div>
            <div class="metric-val" style="color: #f87171;">{st.session_state.falhas}</div>
        </div>
        """,
        unsafe_allow_html=True
    )

# Disparo da Thread de Conversão
if iniciar_btn and not st.session_state.running:
    st.session_state.running = True
    st.session_state.logs = ["Iniciando conversão via interface gráfica..."]
    
    # Descobre a quantidade de arquivos na pasta antes de iniciar para atualizar os cards
    p_in = Path(in_dir)
    p_out = Path(out_dir)
    
    try:
        arquivos = []
        if p_in.exists():
            for padrao in FORMATOS_SUPORTADOS:
                arquivos.extend([p for p in p_in.rglob(padrao) if p_out not in p.parents])
        st.session_state.totais = len(set(arquivos))
    except Exception:
        st.session_state.totais = 0

    # Função wrapper para executar em background
    def worker():
        try:
            converter_alta_precisao(
                caminho_entrada=Path(in_dir),
                caminho_saida_base=Path(out_dir),
                max_workers=workers,
                dry_run=dry_run
            )
        except Exception as e:
            logger.error("Erro crítico na conversão: %s", e)
        finally:
            st.session_state.running = False

    t = threading.Thread(target=worker)
    t.start()
    st.rerun()

# Se o pipeline estiver rodando, monitoramos em tempo real
if st.session_state.running:
    st.markdown("### ⏳ Processando Documentos...")
    st.info("O conversor está rodando em segundo plano. Por favor, aguarde o término.")
    
    # Exibe logs em tempo real com auto-refresh
    log_container = st.empty()
    
    # Loop de atualização visual
    while st.session_state.running:
        # Pega as últimas 30 linhas de log para não sobrecarregar a tela
        log_snippet = "\n".join(st.session_state.logs[-30:])
        log_container.code(log_snippet or "Aguardando logs...")
        time.sleep(0.5)
        
    st.session_state.running = False
    
    # Atualiza as estatísticas lendo o manifest.json
    try:
        p_out = Path(out_dir)
        manifest_path = p_out / "manifest.json"
        if manifest_path.exists():
            import json
            with open(manifest_path, "r", encoding="utf-8") as f:
                dados = json.load(f)
                sucesso_count = sum(1 for v in dados.values() if v.get("status") == "ok")
                falha_count = sum(1 for v in dados.values() if v.get("status") == "falha")
                st.session_state.sucessos = sucesso_count
                st.session_state.falhas = falha_count
    except Exception:
        pass
        
    st.success("Conversão de documentos concluída!")
    st.rerun()

# Seção de Visualização e Leitura de Documentos convertidos
st.markdown("---")
st.markdown("### 📚 Biblioteca de Documentos Convertidos")

p_out = Path(out_dir)
if p_out.exists():
    pastas_md = sorted([p for p in p_out.iterdir() if p.is_dir()])
    
    if not pastas_md:
        st.info("Nenhum documento convertido disponível para leitura na pasta de saída.")
    else:
        # Combobox para escolher qual livro ler
        nomes_livros = [p.name for p in pastas_md]
        livro_selecionado = st.selectbox("Escolha um documento para ler na tela:", nomes_livros)
        
        if livro_selecionado:
            pasta_livro = p_out / livro_selecionado
            arquivo_md = pasta_livro / f"{livro_selecionado}.md"
            arquivo_meta = pasta_livro / "metadata.json"
            
            # Exibe metadados se existirem
            if arquivo_meta.exists():
                try:
                    import json
                    with open(arquivo_meta, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                        col_m1, col_m2, col_m3 = st.columns(3)
                        col_m1.markdown(f"**Páginas:** `{meta.get('paginas', 'N/A')}`")
                        col_m2.markdown(f"**Tamanho:** `{meta.get('tamanho_bytes', 0) // 1024} KB`")
                        col_m3.markdown(f"**Formato original:** `{meta.get('formato', 'N/A')}`")
                        
                        if "pdf_metadados" in meta:
                            pdf_meta = meta["pdf_metadados"]
                            st.markdown(
                                f"""
                                <div style="background-color: #1e293b; padding: 1rem; border-radius: 8px; margin-bottom: 1rem; border: 1px solid #334155">
                                    <strong>Título Original:</strong> {pdf_meta.get('titulo', 'Sem título')}<br>
                                    <strong>Autor original:</strong> {pdf_meta.get('autor', 'Desconhecido')}<br>
                                    <strong>Ferramenta de criação:</strong> {pdf_meta.get('criador', 'Desconhecida')}
                                </div>
                                """,
                                unsafe_allow_html=True
                            )
                except Exception:
                    pass

            # Visualizador do Markdown
            if arquivo_md.exists():
                with st.expander("📖 Ler Markdown Completo", expanded=True):
                    # Lê o markdown e renderiza nativamente na tela com scroll do navegador
                    conteudo_md = arquivo_md.read_text(encoding="utf-8")
                    st.markdown(conteudo_md)
            else:
                st.warning("Arquivo .md correspondente não foi encontrado na pasta do livro.")
else:
    st.info("A pasta de saída ainda não foi criada. Inicie o conversor para gerar os documentos.")

# Visualizador de Logs Históricos
st.markdown("---")
with st.expander("🪵 Visualizar Histórico de Logs Completos"):
    if st.session_state.logs:
        st.code("\n".join(st.session_state.logs))
    else:
        st.text("Nenhum log gerado nesta sessão.")
