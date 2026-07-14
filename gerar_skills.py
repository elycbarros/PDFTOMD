"""
Gerador de Skills de Agente a partir de Markdowns existentes.

Este script é totalmente independente do converter.py e da conversão de PDFs.
Ele lê os arquivos Markdown já gerados e usa a API do Gemini para criar Skills
estruturadas prontas para usar no ChatGPT, Gemini, NotebookLM, etc.

Uso:
  # Gera uma Skill individual para cada PDF convertido:
  python3 gerar_skills.py

  # Gera uma Skill unificada por subpasta (Abordagem A / Skill Temática):
  python3 gerar_skills.py --temas-auto

  # Especifica pastas personalizadas:
  python3 gerar_skills.py --entrada pdf_entrada/ --markdowns markdown_saida/ --saida skills_saida/

Pré-requisito:
  - Os PDFs já devem estar convertidos em Markdown (rodar converter.py antes)
  - A chave GEMINI_API_KEY deve estar configurada no arquivo .env
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from pathlib import Path

# Carrega variáveis de ambiente do arquivo .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

log = logging.getLogger("gerar_skills")

# Constantes (espelha o converter.py para compatibilidade de nomes de pasta)
HASH_CHUNK = 65536
HASH_LEN = 8
MANIFEST_FILE = "manifest.json"


# ==============================================================================
# HELPERS DE COMPATIBILIDADE COM O CONVERTER.PY
# (Replicados aqui para manter o script 100% independente)
# ==============================================================================

def hash_arquivo(arquivo: Path) -> str:
    """Calcula o hash SHA-256 de um arquivo usando buffer de 64KB."""
    h = hashlib.sha256()
    with open(arquivo, "rb") as f:
        while chunk := f.read(HASH_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def nome_pasta_unico(stem: str, hash_curto: str) -> str:
    """Gera o nome único de pasta usado pelo converter.py (stem_hash)."""
    stem_limpo = stem.replace(" ", "_").replace("-", "_")
    import re
    stem_limpo = re.sub(r"[^\w]", "_", stem_limpo)
    return f"{stem_limpo}_{hash_curto}"


def configurar_logging() -> None:
    """Configura o sistema de log unificado (Console)."""
    logger = logging.getLogger("gerar_skills")
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)
    logger.addHandler(sh)
    logger.propagate = False


def _verificar_api_key() -> bool:
    """Verifica se a GEMINI_API_KEY está configurada."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        log.error("=" * 60)
        log.error("GEMINI_API_KEY não encontrada!")
        log.error("Configure a chave no arquivo .env:")
        log.error("  GEMINI_API_KEY=sua_chave_aqui")
        log.error("Obtenha sua chave em: https://aistudio.google.com/")
        log.error("=" * 60)
        return False
    log.info("✓ API Key do Gemini encontrada.")
    return True


# ==============================================================================
# MODO 1: Skill Individual por PDF
# ==============================================================================

def gerar_skills_individuais(
    caminho_entrada: Path,
    caminho_markdowns: Path,
    caminho_skills_saida: Path,
    forcar: bool = False,
) -> None:
    """
    Para cada PDF em caminho_entrada, localiza o Markdown correspondente
    em caminho_markdowns e gera uma Skill individual via Gemini.
    """
    from skill_compiler import compilar_skill

    pdfs = list(caminho_entrada.rglob("*.pdf"))
    if not pdfs:
        log.warning("Nenhum PDF encontrado em '%s'.", caminho_entrada)
        return

    log.info("=" * 60)
    log.info("MODO: Skill Individual por PDF")
    log.info("PDFs encontrados: %d", len(pdfs))
    log.info("=" * 60)

    processados = 0
    ignorados = 0

    for pdf in pdfs:
        conteudo_hash = hash_arquivo(pdf)
        nome_limpo = nome_pasta_unico(pdf.stem, conteudo_hash[:HASH_LEN])
        pasta_md = caminho_markdowns / nome_limpo
        arquivo_md = pasta_md / f"{nome_limpo}.md"

        if not arquivo_md.exists():
            log.warning("Markdown não encontrado para '%s'. Execute converter.py primeiro.", pdf.name)
            continue

        pasta_skill = caminho_skills_saida / nome_limpo
        skill_mestre = pasta_skill / "SKILL.md"

        if skill_mestre.exists() and not forcar:
            log.info("Skill já existe para '%s'. Use --forcar para regenerar.", pdf.name)
            ignorados += 1
            continue

        log.info("Gerando skill para: %s", pdf.name)
        compilar_skill(pdf, arquivo_md, pasta_skill)
        processados += 1

    log.info("=" * 60)
    log.info("Concluído! Skills geradas: %d | Ignoradas (já existiam): %d", processados, ignorados)
    log.info("=" * 60)


# ==============================================================================
# MODO 2: Skill Temática por Subpasta (Abordagem A)
# ==============================================================================

def gerar_skills_tematicas(
    caminho_entrada: Path,
    caminho_markdowns: Path,
    caminho_skills_saida: Path,
    forcar: bool = False,
) -> None:
    """
    Varre as subpastas do caminho_entrada. Cada subpasta é um tema.
    Agrega os Markdowns de todos os PDFs de cada tema e gera uma Skill unificada.
    """
    from skill_compiler import compilar_skill_tematica

    subpastas = [p for p in caminho_entrada.iterdir() if p.is_dir()]

    if not subpastas:
        log.warning(
            "Nenhuma subpasta de tema encontrada em '%s'.\n"
            "Organize seus PDFs em subpastas por tema:\n"
            "  pdf_entrada/\n"
            "    ├── inventarios/\n"
            "    │     ├── livro1.pdf\n"
            "    │     └── livro2.pdf\n"
            "    └── divorcio/\n"
            "          └── livro3.pdf",
            caminho_entrada
        )
        return

    log.info("=" * 60)
    log.info("MODO: Skill Temática por Subpasta")
    log.info("Temas encontrados: %d", len(subpastas))
    for s in subpastas:
        log.info("  - %s", s.name)
    log.info("=" * 60)

    import re
    def _slugify(texto: str) -> str:
        texto = texto.lower()
        subs = {'á':'a','à':'a','ã':'a','â':'a','é':'e','è':'e','ê':'e',
                'í':'i','ì':'i','î':'i','ó':'o','ò':'o','õ':'o','ô':'o',
                'ú':'u','ù':'u','û':'u','ç':'c','ñ':'n'}
        for o, d in subs.items():
            texto = texto.replace(o, d)
        texto = re.sub(r"[^a-z0-9\s-]", "", texto)
        texto = re.sub(r"[\s-]+", "-", texto)
        return texto.strip("-")

    for subpasta in subpastas:
        tema_slug = _slugify(subpasta.name)
        pdfs_do_tema = list(subpasta.rglob("*.pdf"))

        if not pdfs_do_tema:
            log.info("Subpasta '%s' não tem PDFs. Ignorando.", subpasta.name)
            continue

        # Verifica se a skill temática já existe
        pasta_skill_tema = caminho_skills_saida / tema_slug
        skill_mestre = pasta_skill_tema / "SKILL.md"
        if skill_mestre.exists() and not forcar:
            log.info("Skill temática '%s' já existe. Use --forcar para regenerar.", tema_slug)
            continue

        # Coleta os arquivos Markdown dos livros desse tema
        arquivos_md_tema: list[Path] = []
        for pdf in pdfs_do_tema:
            conteudo_hash = hash_arquivo(pdf)
            nome_limpo = nome_pasta_unico(pdf.stem, conteudo_hash[:HASH_LEN])
            arquivo_md = caminho_markdowns / nome_limpo / f"{nome_limpo}.md"
            if arquivo_md.exists():
                arquivos_md_tema.append(arquivo_md)
            else:
                log.warning(
                    "Markdown do livro '%s' não encontrado. Execute converter.py para convertê-lo.",
                    pdf.name
                )

        if arquivos_md_tema:
            log.info("Compilando skill temática '%s' com %d livros...", tema_slug, len(arquivos_md_tema))
            compilar_skill_tematica(tema_slug, arquivos_md_tema, pasta_skill_tema)
        else:
            log.warning("Nenhum Markdown disponível para o tema '%s'. Ignorando.", tema_slug)

    log.info("=" * 60)
    log.info("Geração de Skills Temáticas concluída!")
    log.info("=" * 60)


# ==============================================================================
# CLI
# ==============================================================================

def obter_argumentos() -> argparse.Namespace:
    diretorio_script = Path(__file__).resolve().parent

    parser = argparse.ArgumentParser(
        description=(
            "Gerador de Skills de Agente a partir dos Markdowns convertidos.\n"
            "Pré-requisito: rodar converter.py para converter os PDFs primeiro."
        ),
        formatter_class=argparse.RawTextHelpFormatter
    )

    parser.add_argument(
        "--entrada",
        type=str,
        default=os.getenv("CONVERTER_INPUT_DIR") or str(diretorio_script / "pdf_entrada"),
        help="Pasta contendo os PDFs originais (padrão: ./pdf_entrada)"
    )

    parser.add_argument(
        "--markdowns",
        type=str,
        default=os.getenv("CONVERTER_OUTPUT_DIR") or str(diretorio_script / "markdown_saida"),
        help="Pasta com os Markdowns gerados pelo converter.py (padrão: ./markdown_saida)"
    )

    parser.add_argument(
        "--saida",
        type=str,
        default=str(diretorio_script / "skills_saida"),
        help="Pasta onde as Skills serão salvas (padrão: ./skills_saida)"
    )

    parser.add_argument(
        "--temas-auto",
        action="store_true",
        dest="temas_auto",
        help=(
            "Gera Skills temáticas: uma por subpasta do diretório de entrada.\n"
            "Organize os PDFs em subpastas por tema:\n"
            "  pdf_entrada/inventarios/livro.pdf\n"
            "  pdf_entrada/divorcio/livro.pdf"
        )
    )

    parser.add_argument(
        "--forcar",
        action="store_true",
        help="Regenera Skills mesmo que já existam."
    )

    return parser.parse_args()


if __name__ == "__main__":
    configurar_logging()
    args = obter_argumentos()

    if not _verificar_api_key():
        sys.exit(1)

    caminho_entrada = Path(args.entrada).resolve()
    caminho_markdowns = Path(args.markdowns).resolve()
    caminho_saida = Path(args.saida).resolve()

    if not caminho_entrada.exists():
        log.error("Pasta de entrada não encontrada: %s", caminho_entrada)
        sys.exit(1)

    if not caminho_markdowns.exists():
        log.error(
            "Pasta de Markdowns não encontrada: %s\n"
            "Execute primeiro: python3 converter.py",
            caminho_markdowns
        )
        sys.exit(1)

    if args.temas_auto:
        gerar_skills_tematicas(caminho_entrada, caminho_markdowns, caminho_saida, forcar=args.forcar)
    else:
        gerar_skills_individuais(caminho_entrada, caminho_markdowns, caminho_saida, forcar=args.forcar)
