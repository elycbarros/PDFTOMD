"""
Pipeline de Alta Performance para Conversão de PDF para Markdown.

Este script é um PRÉ-PROCESSADOR UNIVERSAL: converte PDFs em Markdowns de alta
qualidade (com OCR calibrado para português, tabelas estruturadas e imagens
referenciadas), que podem ser consumidos por qualquer ferramenta de geração de
Skills de Agente na etapa seguinte — seja o gerar_skills.py (Gemini), o
book-to-skill (Claude / OpenCode) ou qualquer outra ferramenta compatível
com Markdown como entrada.

Utiliza a biblioteca Docling (IBM) para extrair texto (OCR) e tabelas
de arquivos PDF de forma concorrente, controlando o estado de conversão via
manifest JSON para garantir idempotência e tolerância a falhas.

Autor: Ely do Carmo Barros  
Data: 2026-07-14
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
import hashlib
import logging
import threading
import signal
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from pathlib import Path
from dataclasses import dataclass, field

# Importações do Docling (IBM) para OCR e estruturação de documentos
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.datamodel.base_models import InputFormat
from docling_core.types.doc import ImageRefMode

# Tenta carregar bibliotecas opcionais para melhorar a experiência
try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

try:
    from dotenv import load_dotenv
    load_dotenv()  # Carrega variáveis do arquivo .env, se existir
except ImportError:
    pass

# Constantes de configuração global do pipeline
MANIFEST_FILE = "manifest.json"
HASH_CHUNK = 65536  # Buffer de 64KB para leitura eficiente de arquivos em disco
HASH_LEN = 8        # Comprimento do hash curto usado nos nomes dos diretórios

# Formatos de entrada suportados pelo Docling (pré-processador universal)
# Docling converte todos esses formatos para Markdown de alta qualidade
FORMATOS_SUPORTADOS: tuple[str, ...] = (
    "*.pdf",   # PDFs (OCR + tabelas + imagens)
    "*.docx",  # Word
    "*.pptx",  # PowerPoint
    "*.xlsx",  # Excel
    "*.png",   # Imagem (OCR)
    "*.jpg",   # Imagem (OCR)
    "*.jpeg",  # Imagem (OCR)
    "*.tiff",  # Imagem (OCR)
    "*.tif",   # Imagem (OCR)
    "*.bmp",   # Imagem (OCR)
    "*.html",  # HTML
    "*.htm",   # HTML
    "*.adoc",  # AsciiDoc
    "*.md",    # Markdown (re-estrutura e normaliza)
)

# Limitação inteligente de concorrência por consumo de memória
LIMITE_CONCORRENCIA_PESADOS = 2
LIMIAR_ARQUIVO_PESADO = 5 * 1024 * 1024       # 5 MB (consome 1 slot do semáforo)
LIMIAR_ARQUIVO_CRITICO = 50 * 1024 * 1024     # 50 MB (consome ambos os slots, rodando isoladamente)
heavy_semaphore = threading.Semaphore(LIMITE_CONCORRENCIA_PESADOS)

# Isolamento de recursos de threads (Thread-Local Storage)
_thread_local = threading.local()
log = logging.getLogger("converter")


@dataclass
class Estatisticas:
    """Estrutura de dados para consolidação das métricas de processamento."""
    total: int = 0
    sucessos: list[str] = field(default_factory=list)
    falhas: list[tuple[str, str]] = field(default_factory=list)
    ignorados: int = 0


def configurar_logging(caminho_log: Path | None = None) -> None:
    """Configura o sistema de log unificado (Console + Arquivo)."""
    logger = logging.getLogger("converter")
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    if caminho_log:
        fh = logging.FileHandler(caminho_log, mode="a", encoding="utf-8")
        fh.setFormatter(formatter)
        logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)
    logger.addHandler(sh)
    logger.propagate = False


def hash_arquivo(arquivo: Path) -> str:
    """Calcula o hash SHA-256 de um arquivo usando buffer de 64KB."""
    h = hashlib.sha256()
    with open(arquivo, "rb") as f:
        while chunk := f.read(HASH_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def carregar_manifest(saida_base: Path) -> dict:
    """Carrega o estado histórico de conversões do arquivo manifest JSON."""
    manifest_path = saida_base / MANIFEST_FILE
    if manifest_path.exists():
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Manifest corrompido ou inacessível. Criando um novo. Erro: %s", e)
    return {}


def salvar_manifest(saida_base: Path, manifest: dict) -> None:
    """Salva o manifest utilizando escrita atômica via arquivo temporário (.tmp)."""
    manifest_path = saida_base / MANIFEST_FILE
    tmp = manifest_path.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        tmp.replace(manifest_path)
    except OSError as e:
        log.warning("Falha ao salvar manifest de estado: %s", e)


def criar_conversor() -> DocumentConverter:
    """Instancia o pipeline do Docling configurado para alta precisão (OCR + Tabelas + Imagens)."""
    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = True                 
    pipeline_options.do_table_structure = True     

    # Configura prioridade de idioma no OCR
    pipeline_options.ocr_options.lang = ["pt", "en", "es"]

    # Habilita extração de imagens e figuras
    pipeline_options.generate_page_images = True
    pipeline_options.generate_picture_images = True

    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )


def obter_conversor() -> DocumentConverter:
    """Retorna a instância do Docling exclusiva para a Thread corrente (Thread-Safe)."""
    if not hasattr(_thread_local, "converter"):
        _thread_local.converter = criar_conversor()
    return _thread_local.converter


def nome_pasta_unico(stem: str, hash_curto: str) -> str:
    """Gera um nome de pasta limpo e curto para o S.O."""
    nome = stem.replace(" ", "_")
    if len(nome) > 120:
        nome = nome[:120]
    return f"{nome}_{hash_curto}"


def processar_arquivo(arquivo: Path, saida_base: Path) -> tuple[str, str | None]:
    """Processa um único arquivo (PDF, DOCX, PPTX, imagem, etc.) e exporta para Markdown.
    
    Suporta todos os formatos aceitos pelo Docling:
      PDF, DOCX, PPTX, XLSX, PNG, JPG, TIFF, BMP, HTML, AsciiDoc, Markdown.
    """
    conteudo_hash = hash_arquivo(arquivo)
    nome_limpo = nome_pasta_unico(arquivo.stem, conteudo_hash[:HASH_LEN])
    subpasta_destino = saida_base / nome_limpo
    arquivo_md = subpasta_destino / f"{nome_limpo}.md"

    tamanho_bytes = arquivo.stat().st_size
    e_pesado = tamanho_bytes > LIMIAR_ARQUIVO_PESADO
    e_critico = tamanho_bytes > LIMIAR_ARQUIVO_CRITICO
    slots_adquiridos = 0

    try:
        if e_critico:
            log.info(
                "ARQUIVO CRÍTICO detectado (%d MB): %s. Bloqueando todos os slots de processamento pesado para isolamento de RAM...",
                int(tamanho_bytes / (1024 * 1024)),
                arquivo.name
            )
            # Adquire ambos os slots do semáforo consecutivamente
            heavy_semaphore.acquire()
            slots_adquiridos += 1
            heavy_semaphore.acquire()
            slots_adquiridos += 1
            log.info("Modo de isolamento ativado! Iniciando processamento do arquivo crítico: %s", arquivo.name)
        elif e_pesado:
            log.info(
                "Arquivo pesado detectado (%d MB): %s. Aguardando slot de processamento livre...",
                int(tamanho_bytes / (1024 * 1024)),
                arquivo.name
            )
            heavy_semaphore.acquire()
            slots_adquiridos += 1
            log.info("Slot livre obtido! Iniciando processamento pesado: %s", arquivo.name)

        subpasta_destino.mkdir(parents=True, exist_ok=True)

        converter = obter_conversor()
        resultado = converter.convert(str(arquivo))

        # Salva o arquivo markdown gerando as imagens de forma referenciada na subpasta com marcadores de página
        resultado.document.save_as_markdown(
            filename=arquivo_md,
            artifacts_dir=subpasta_destino,
            image_mode=ImageRefMode.REFERENCED,
            page_break_placeholder="\n\n<!-- PAGE_BREAK -->\n\n"
        )

        if not arquivo_md.exists() or arquivo_md.stat().st_size == 0:
            raise ValueError("Falha na geração do arquivo Markdown ou arquivo vazio.")

        # Torna as referências de imagens relativas para garantir portabilidade
        try:
            conteudo = arquivo_md.read_text(encoding="utf-8")
            caminho_absoluto_prefixo = str(subpasta_destino.resolve()) + "/"
            conteudo_corrigido = conteudo.replace(caminho_absoluto_prefixo, "./")
            arquivo_md.write_text(conteudo_corrigido, encoding="utf-8")
        except Exception as e:
            log.warning("Não foi possível tornar as imagens relativas em %s: %s", arquivo_md.name, e)

        # Extração e salvamento de metadados estruturados para RAG/GPTs
        try:
            meta_dados = {
                "nome_arquivo": arquivo.name,
                "caminho_original": str(arquivo.resolve()),
                "tamanho_bytes": tamanho_bytes,
                "hash_sha256": conteudo_hash,
                "formato": arquivo.suffix[1:].upper(),
                "paginas": resultado.document.num_pages() if hasattr(resultado.document, "num_pages") and callable(resultado.document.num_pages) else getattr(resultado.document, "num_pages", None),
            }

            # Se for PDF, tenta extrair metadados extras via pypdf
            if arquivo.suffix.lower() == ".pdf":
                try:
                    from pypdf import PdfReader
                    reader = PdfReader(arquivo)
                    pdf_info = reader.metadata
                    if pdf_info:
                        meta_dados["pdf_metadados"] = {
                            "titulo": pdf_info.title or "",
                            "autor": pdf_info.author or "",
                            "assunto": pdf_info.subject or "",
                            "criador": pdf_info.creator or "",
                            "produtor": pdf_info.producer or "",
                        }
                except Exception as pypdf_err:
                    log.debug("Não foi possível extrair metadados adicionais via pypdf: %s", pypdf_err)

            arquivo_meta = subpasta_destino / "metadata.json"
            with open(arquivo_meta, "w", encoding="utf-8") as f:
                json.dump(meta_dados, f, ensure_ascii=False, indent=2)
        except Exception as meta_err:
            log.warning("Erro ao gerar ou salvar metadados para %s: %s", arquivo.name, meta_err)

        return conteudo_hash, None

    except Exception as e:
        if subpasta_destino.exists():
            shutil.rmtree(subpasta_destino, ignore_errors=True)
        return conteudo_hash, str(e)

    finally:
        for _ in range(slots_adquiridos):
            heavy_semaphore.release()
        if slots_adquiridos > 0:
            log.info("Processamento de arquivo pesado/crítico concluído. %d slot(s) liberado(s) para: %s", slots_adquiridos, arquivo.name)


def converter_alta_precisao(
    caminho_entrada: Path,
    caminho_saida_base: Path,
    max_workers: int = 4,
    dry_run: bool = False,
) -> None:
    """Gerencia o pipeline concorrente de conversão utilizando ThreadPoolExecutor."""
    caminho_entrada = caminho_entrada.resolve()
    caminho_saida_base = caminho_saida_base.resolve()
    
    caminho_entrada.mkdir(parents=True, exist_ok=True)
    caminho_saida_base.mkdir(parents=True, exist_ok=True)

    manifest = carregar_manifest(caminho_saida_base) if not dry_run else {}
    stats = Estatisticas()

    # Varre recursivamente a pasta de entrada buscando todos os formatos suportados
    # (evita loops se a saída estiver dentro da entrada)
    todos_arquivos: list[Path] = []
    for padrao in FORMATOS_SUPORTADOS:
        todos_arquivos.extend(
            p for p in caminho_entrada.rglob(padrao)
            if caminho_saida_base not in p.parents
        )
    # Garante ordem determinística e remove duplicatas (ex: *.md capturando arquivos já gerados)
    todos_arquivos = sorted(set(todos_arquivos))
    stats.total = len(todos_arquivos)

    if not todos_arquivos:
        log.warning(
            "Nenhum arquivo suportado encontrado em: %s\n"
            "Formatos aceitos: PDF, DOCX, PPTX, XLSX, PNG, JPG, TIFF, BMP, HTML, AsciiDoc, Markdown",
            caminho_entrada
        )
        return

    # Filtra arquivos baseando-se no Manifest histórico (Idempotência)
    a_processar: list[tuple[Path, str]] = []
    for arquivo in todos_arquivos:
        conteudo_hash = hash_arquivo(arquivo)
        entrada_manifest = manifest.get(conteudo_hash)

        if entrada_manifest and entrada_manifest.get("status") == "ok":
            stats.ignorados += 1
            continue
        elif entrada_manifest and entrada_manifest.get("status") == "falha":
            log.info("Tentando re-processar arquivo que falhou anteriormente: %s", arquivo.name)

        a_processar.append((arquivo, conteudo_hash))

    if not a_processar:
        log.info("Processamento concluído. Todos os %d arquivo(s) já estão convertidos.", stats.ignorados)
        return

    if dry_run:
        log.info("=== MODO SIMULAÇÃO (DRY RUN) ===")
        log.info("Arquivos na fila de processamento (%d):", len(a_processar))
        for arquivo, _ in a_processar:
            log.info("  - %s", arquivo.name)
        log.info("Arquivos pulados (já convertidos): %d", stats.ignorados)
        return

    log.info(
        "Fila: %d novos | Pulados: %d | Threads Ativas: %d",
        len(a_processar), stats.ignorados, max_workers,
    )

    inicio = time.perf_counter()
    interrompido = False

    def sinal_handler(sig, frame) -> None:
        nonlocal interrompido
        if not interrompido:
            log.warning("\nSinal de interrupção detectado. Finalizando tarefas em andamento...")
            interrompido = True

    signal.signal(signal.SIGINT, sinal_handler)
    signal.signal(signal.SIGTERM, sinal_handler)

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futuros: dict[Future[tuple[str, str | None]], Path] = {
                executor.submit(processar_arquivo, arquivo, caminho_saida_base): arquivo
                for arquivo, _ in a_processar
            }

            bar = None
            if HAS_TQDM:
                bar = tqdm(total=len(futuros), unit="arq", desc="Convertendo", ncols=80)

            for i, futuro in enumerate(as_completed(futuros), 1):
                if interrompido:
                    for f in futuros:
                        f.cancel()
                    break

                try:
                    conteudo_hash, erro = futuro.result()
                except Exception as exc:
                    arquivo = futuros[futuro]
                    erro = f"Erro crítico na thread de execução: {exc}"
                    conteudo_hash = hash_arquivo(arquivo)

                arquivo = futuros[futuro]
                stat = arquivo.stat()

                if erro:
                    stats.falhas.append((arquivo.name, erro))
                    manifest[conteudo_hash] = {
                        "path": str(arquivo.resolve()),
                        "mtime": int(stat.st_mtime),
                        "size": stat.st_size,
                        "status": "falha",
                        "erro": erro,
                    }
                    log.error("%s -> FALHA: %s", arquivo.name, erro)
                else:
                    stats.sucessos.append(arquivo.name)
                    manifest[conteudo_hash] = {
                        "path": str(arquivo.resolve()),
                        "mtime": int(stat.st_mtime),
                        "size": stat.st_size,
                        "status": "ok",
                    }

                if bar:
                    bar.update(1)
                elif i % 5 == 0:
                    log.info("Progresso: [%d/%d] concluídos...", i, len(futuros))
                    salvar_manifest(caminho_saida_base, manifest)

            if bar:
                bar.close()

    finally:
        salvar_manifest(caminho_saida_base, manifest)

    tempo_total = time.perf_counter() - inicio

    if interrompido:
        log.warning("Processo abortado de forma controlada pelo usuário.")

    log.info("=" * 60)
    log.info("RESUMO DO PROCESSAMENTO")
    log.info("=" * 60)
    log.info(
        "Total analisado: %d | Na Fila: %d | Ignorados: %d | Sucessos: %d | Falhas: %d | Tempo: %ds",
        stats.total, len(a_processar), stats.ignorados, len(stats.sucessos), len(stats.falhas), int(tempo_total)
    )
    if stats.falhas:
        log.warning("Falhas registradas:")
        for nome, erro in stats.falhas:
            log.warning("  - %s: %s", nome, erro)
    log.info("=" * 60)


def obter_argumentos() -> argparse.Namespace:
    """Configura o interpretador de argumentos de linha de comando (CLI)."""
    # Estratégia 1: Define caminhos dinâmicos relativos à pasta onde o script está localizado
    diretorio_script = Path(__file__).resolve().parent
    entrada_padrao = diretorio_script / "pdf_entrada"
    saida_padrao = diretorio_script / "markdown_saida"

    # Sobrescreve com variáveis de ambiente (Estratégia 3) se elas estiverem configuradas no sistema/.env
    env_entrada = os.getenv("CONVERTER_INPUT_DIR")
    env_saida = os.getenv("CONVERTER_OUTPUT_DIR")
    env_workers = os.getenv("CONVERTER_WORKERS")

    parser = argparse.ArgumentParser(
        description="Pipeline concorrente e idempotente para conversão de PDF em Markdown estruturado."
    )

    parser.add_argument(
        "entrada",
        type=str,
        nargs="?",
        default=env_entrada or str(entrada_padrao),
        help=f"Caminho do diretório contendo os PDFs (padrão: {env_entrada or entrada_padrao})"
    )

    parser.add_argument(
        "saida",
        type=str,
        nargs="?",
        default=env_saida or str(saida_padrao),
        help=f"Caminho de destino dos Markdowns estruturados (padrão: {env_saida or saida_padrao})"
    )

    parser.add_argument(
        "--workers", "-w",
        type=int,
        default=int(env_workers) if env_workers else 4,
        help="Número de threads paralelas de processamento (padrão: 4)"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simula a execução listando quais arquivos seriam processados"
    )

    parser.add_argument(
        "--log",
        type=str,
        default=os.getenv("CONVERTER_LOG_FILE") or str(diretorio_script / "conversao.log"),
        help="Caminho para salvar o arquivo físico de log"
    )

    return parser.parse_args()


if __name__ == "__main__":
    args = obter_argumentos()

    # Inicializa o log no arquivo configurado
    configurar_logging(caminho_log=Path(args.log))

    # Executa o conversor principal com os caminhos dinamicamente validados
    converter_alta_precisao(
        caminho_entrada=Path(args.entrada),
        caminho_saida_base=Path(args.saida),
        max_workers=args.workers,
        dry_run=args.dry_run,
    )