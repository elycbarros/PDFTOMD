import os
import re
import json
import time
import logging
from pathlib import Path
from typing import List, Dict, Any

def chamar_gemini_com_retry(model, prompt, generation_config=None, max_retries=10) -> Any:
    """Executa chamadas ao Gemini tratando erros de rate limit (429/ResourceExhausted) com backoff exponencial."""
    delay = 15  # Delay inicial em segundos
    for tentativa in range(max_retries):
        try:
            if generation_config:
                return model.generate_content(prompt, generation_config=generation_config)
            else:
                return model.generate_content(prompt)
        except Exception as e:
            erro_str = str(e).lower()
            # Captura 429 ou esgotamento de recursos
            if "429" in erro_str or "exhausted" in erro_str or "quota" in erro_str or "limit" in erro_str:
                log.warning("Rate limit do Gemini atingido (429). Aguardando %d segundos antes de tentar novamente (Tentativa %d/%d)...", delay, tentativa + 1, max_retries)
                time.sleep(delay)
                delay = min(delay * 2, 60)  # Backoff exponencial até 60s
            else:
                raise e
    # Última tentativa sem tratamento para expor o erro final se todas as tentativas falharem
    if generation_config:
        return model.generate_content(prompt, generation_config=generation_config)
    else:
        return model.generate_content(prompt)

# Configuração de Logs
log = logging.getLogger("converter.skill")

# Limiar de tamanho para resumos de capítulos em tokens (aproximadamente)
MAX_SUMMARY_TOKENS = 1200

# Templates de prompt para o Gemini
PROMPT_CAPITULO = """
Você é um engenheiro de software sênior e especialista técnico.
Sua tarefa é ler o texto do capítulo de um livro técnico fornecido abaixo e destilá-lo em uma Skill de Agente altamente estruturada e concisa.

Instruções importantes para a resposta:
1. FOCO EM ESTRUTURA E PRÁTICA: Evite resumos narrativos ou vagos ("o livro explica..."). Em vez disso, extraia regras práticas de decisão ("Use X se Y"), conceitos-chave definidos com precisão, frameworks, padrões de arquitetura e exemplos de código reais que estejam no texto.
2. DENSIDADE: Mantenha o conteúdo denso e útil. O resumo deve ter entre 800 e 1200 tokens (cerca de 500 a 800 palavras).
3. FORMATO MARKDOWN: Use cabeçalhos claros (##), listas e tabelas.
4. Mantenha os termos técnicos no idioma do texto original (Português/Inglês).

Texto do capítulo:
---
{texto_capitulo}
---

Gere o arquivo markdown destilado deste capítulo. Comece diretamente com o título do capítulo no nível ##.
"""

PROMPT_GLOSSARIO_CHEATSHEET_INDEX = """
Você é um engenheiro de software sênior.
Você receberá os resumos estruturados de todos os capítulos de um livro/documento técnico que foram gerados anteriormente.

Sua tarefa é gerar três seções consolidadas para a Skill do Agente:

1. GLOSSÁRIO (glossary.md): Uma lista de todos os termos técnicos e conceitos cruciais com definições diretas e curtas.
2. CHEATSHEET (cheatsheet.md): Um guia rápido de referência, contendo comandos, tabelas de decisão, regras rápidas e diretrizes de design/implementação.
3. ARQUIVO MESTRE (SKILL.md): O arquivo mestre de configuração da Skill. Ele deve incluir:
   - Uma seção com os Modelos Mentais centrais do livro (as lições mais importantes de arquitetura/design).
   - O Sumário/Índice de Capítulos mapeando quais tópicos estão em qual arquivo de capítulo (ex: ch01-introducao.md, ch02-prevencao.md).
   - Instruções para o modelo sobre como carregar e ler os capítulos sob demanda.

Aqui estão os resumos dos capítulos:
---
{resumos_capitulos}
---

Por favor, gere a resposta estruturada em JSON com três chaves obrigatórias:
"glossary": "conteúdo markdown do glossário...",
"cheatsheet": "conteúdo markdown do cheatsheet...",
"skill_master": "conteúdo markdown do arquivo SKILL.md..."
"""

def extrair_bookmarks_pdf(pdf_path: Path) -> List[Dict[str, Any]]:
    """
    Tenta extrair o sumário (bookmarks/outline) nativo do PDF usando pypdf.
    Retorna uma lista de dicionários contendo {'title': str, 'page': int}.
    """
    try:
        from pypdf import PdfReader
        reader = PdfReader(pdf_path)
        outline = reader.outline
        if not outline:
            return []
            
        pages_map = []
        
        def _parse_outline(item):
            if isinstance(item, list):
                for sub_item in item:
                    _parse_outline(sub_item)
            else:
                try:
                    page_num = reader.get_destination_page_number(item)
                    # pypdf retorna 0-indexed, somamos 1 para bater com a página real
                    pages_map.append({"title": item.title, "page": page_num + 1})
                except Exception:
                    pass
                    
        _parse_outline(outline)
        pages_map.sort(key=lambda x: x["page"])
        
        # Filtra duplicados mantendo a primeira ocorrência da página
        vistos = set()
        bookmarks_filtrados = []
        for b in pages_map:
            if b["page"] not in vistos:
                vistos.add(b["page"])
                bookmarks_filtrados.append(b)
                
        return bookmarks_filtrados
    except Exception as e:
        log.warning("Falha ao extrair bookmarks do PDF %s: %s", pdf_path.name, e)
        return []

def segmentar_por_headings(markdown_content: str) -> List[Dict[str, Any]]:
    """
    Fallback: Segmenta o Markdown procurando cabeçalhos principais (# ou ##)
    que representem capítulos ou seções principais.
    """
    linhas = markdown_content.splitlines()
    capitulos = []
    
    # Regex para capturar possíveis títulos de capítulos/seções
    pattern = re.compile(r"^(?:#|##)\s+(.*)$")
    
    texto_acumulado = []
    titulo_atual = "Introdução"
    
    for linha in linhas:
        match = pattern.match(linha)
        if match:
            # Salva o capítulo anterior antes de iniciar o novo
            if texto_acumulado:
                capitulos.append({
                    "title": titulo_atual,
                    "text": "\n".join(texto_acumulado).strip()
                })
                texto_acumulado = []
            titulo_atual = match.group(1).strip()
        texto_acumulado.append(linha)
        
    if texto_acumulado:
        capitulos.append({
            "title": titulo_atual,
            "text": "\n".join(texto_acumulado).strip()
        })
        
    return capitulos

def segmentar_por_paginas(markdown_content: str, bookmarks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Divide o texto markdown que contém marcações <!-- PAGE_BREAK --> baseando-se
    nos números de página fornecidos pelos bookmarks do PDF.
    """
    paginas = markdown_content.split("<!-- PAGE_BREAK -->")
    capitulos = []
    
    for i, book in enumerate(bookmarks):
        inicio_pag = book["page"] - 1 # 0-indexed para nossa lista de páginas
        
        # Define o fim do capítulo (página de início do próximo ou fim do documento)
        if i + 1 < len(bookmarks):
            fim_pag = bookmarks[i+1]["page"] - 1
        else:
            fim_pag = len(paginas)
            
        # Garante limites seguros
        inicio_pag = max(0, min(inicio_pag, len(paginas)))
        fim_pag = max(inicio_pag, min(fim_pag, len(paginas)))
        
        texto_capitulo = "\n".join(paginas[inicio_pag:fim_pag]).strip()
        
        # Limpa placeholders de quebras de página restantes no texto
        texto_capitulo = texto_capitulo.replace("<!-- PAGE_BREAK -->", "").strip()
        
        if texto_capitulo:
            capitulos.append({
                "title": book["title"],
                "text": texto_capitulo
            })
            
    return capitulos

def normalizar_slug(texto: str) -> str:
    """Gera um slug amigável para nomes de arquivo a partir de um título."""
    texto = texto.lower()
    # Remove acentos básicos (substituições manuais simples)
    substituicoes = {
        'á': 'a', 'à': 'a', 'ã': 'a', 'â': 'a', 'ä': 'a',
        'é': 'e', 'è': 'e', 'ê': 'e', 'ë': 'e',
        'í': 'i', 'ì': 'i', 'î': 'i', 'ï': 'i',
        'ó': 'o', 'ò': 'o', 'õ': 'o', 'ô': 'o', 'ö': 'o',
        'ú': 'u', 'ù': 'u', 'û': 'u', 'ü': 'u',
        'ç': 'c', 'ñ': 'n'
    }
    for orig, dest in substituicoes.items():
        texto = texto.replace(orig, dest)
        
    texto = re.sub(r"[^a-z0-9\s-]", "", texto)
    texto = re.sub(r"[\s-]+", "-", texto)
    return texto.strip("-")

def compilar_skill(pdf_path: Path, markdown_path: Path, saida_skill_base: Path) -> None:
    """
    Lê o Markdown gerado, divide em capítulos e usa o Gemini para destilar 
    e gerar a pasta de Skill estruturada.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        log.warning("GEMINI_API_KEY não configurada no .env. Ignorando geração de Skill.")
        return
        
    log.info("Iniciando compilação da Skill do Agente para: %s", pdf_path.name)
    
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        # Usamos gemini-2.5-flash por ser rápido, moderno e suportado
        model = genai.GenerativeModel("gemini-2.0-flash")
    except Exception as e:
        log.error("Falha ao inicializar a biblioteca google-generativeai: %s", e)
        return
        
    if not markdown_path.exists():
        log.error("Arquivo Markdown original não encontrado em %s", markdown_path)
        return
        
    markdown_content = markdown_path.read_text(encoding="utf-8")
    
    # 1. Segmenta o livro em capítulos
    bookmarks = extrair_bookmarks_pdf(pdf_path)
    capitulos = []
    
    if bookmarks and "<!-- PAGE_BREAK -->" in markdown_content:
        log.info("Dividindo livro por páginas usando bookmarks nativos do PDF...")
        capitulos = segmentar_por_paginas(markdown_content, bookmarks)
    else:
        log.info("Bookmarks não encontrados ou sem quebras de página. Segmentando por títulos markdown...")
        capitulos = segmentar_por_headings(markdown_content)
        
    # Se ainda assim não encontrar divisões, faz segmentação por tamanho
    if not capitulos:
        log.warning("Não foi possível segmentar o arquivo estruturalmente. Dividindo em blocos de tamanho fixo.")
        chunk_size = 20000
        for i in range(0, len(markdown_content), chunk_size):
            bloco_texto = markdown_content[i:i+chunk_size]
            capitulos.append({
                "title": f"Parte {i // chunk_size + 1}",
                "text": bloco_texto
            })
            
    log.info("Total de capítulos/seções mapeados: %d", len(capitulos))
    
    # Cria diretório de saída da Skill
    pasta_chapters = saida_skill_base / "chapters"
    pasta_chapters.mkdir(parents=True, exist_ok=True)
    
    resumos_compilados = []
    
    # 2. Processa cada capítulo com o Gemini
    for idx, cap in enumerate(capitulos, 1):
        titulo = cap["title"]
        texto = cap["text"]
        
        # Ignora blocos pequenos demais (geralmente sujeira ou páginas vazias)
        if len(texto.strip()) < 150:
            continue
            
        slug = f"ch{idx:02d}-{normalizar_slug(titulo)}"
        log.info("[%d/%d] Destilando capítulo: %s -> %s.md", idx, len(capitulos), titulo, slug)
        
        prompt = PROMPT_CAPITULO.format(texto_capitulo=texto[:100000]) # Garante limite seguro de tokens de input por capítulo
        
        try:
            response = chamar_gemini_com_retry(model, prompt)
            resumo_md = response.text.strip()
            
            # Grava o arquivo do capítulo destilado
            arquivo_cap = pasta_chapters / f"{slug}.md"
            arquivo_cap.write_text(resumo_md, encoding="utf-8")
            
            resumos_compilados.append(f"### Arquivo: chapters/{slug}.md\n\n{resumo_md}")
        except Exception as e:
            log.error("Erro ao destilar capítulo %s via API: %s", titulo, e)
            
    if not resumos_compilados:
        log.error("Nenhum capítulo pôde ser destilado. Cancelando geração de arquivos mestre.")
        return
        
    # 3. Gera os arquivos globais de Skill (glossary, cheatsheet, SKILL.md)
    log.info("Gerando arquivos mestres (SKILL.md, glossary.md, cheatsheet.md)...")
    resumos_totais = "\n\n---\n\n".join(resumos_compilados)
    prompt_consolida = PROMPT_GLOSSARIO_CHEATSHEET_INDEX.format(resumos_capitulos=resumos_totais[:100000])
    
    try:
        # Pede resposta em JSON estruturado para extrairmos os 3 arquivos limpos
        response = chamar_gemini_com_retry(
            model,
            prompt_consolida,
            generation_config={"response_mime_type": "application/json"}
        )
        dados = json.loads(response.text)
        
        # Grava os arquivos finais
        (saida_skill_base / "glossary.md").write_text(dados.get("glossary", ""), encoding="utf-8")
        (saida_skill_base / "cheatsheet.md").write_text(dados.get("cheatsheet", ""), encoding="utf-8")
        (saida_skill_base / "SKILL.md").write_text(dados.get("skill_master", ""), encoding="utf-8")
        
        log.info("✓ Skill do Agente compilada com sucesso em: %s", saida_skill_base)
    except Exception as e:
        log.error("Erro ao gerar arquivos mestres da Skill: %s", e)
        # Fallback de gravação simples caso falhe o JSON estruturado
        try:
            response = chamar_gemini_com_retry(model, prompt_consolida)
            (saida_skill_base / "SKILL_COMPILADO_RAW.md").write_text(response.text, encoding="utf-8")
            log.warning("✓ Salvo backup bruto em SKILL_COMPILADO_RAW.md devido a falha de formato JSON.")
        except Exception as fallback_err:
            log.error("Falha crítica no fallback: %s", fallback_err)


# ==============================================================================
# MODO TEMÁTICO: Compila uma Skill unificada a partir de múltiplos MDs (Abordagem A)
# ==============================================================================

PROMPT_TEMA = """
Você é um especialista técnico e analista de conhecimento multidisciplinar.
Você recebeu os conteúdos de MÚLTIPLOS livros/documentos sobre um mesmo tema: "{tema}".

Cada livro pode trazer uma perspectiva diferente sobre o mesmo assunto — alguns podem ser mais
teóricos, outros mais práticos; alguns podem discordar entre si sobre certos pontos.

Sua tarefa é gerar uma Skill de Agente UNIFICADA que sintetize o conhecimento de TODOS esses livros.

Instruções:
1. CONSENSO: Identifique os conceitos e regras que TODOS os livros concordam. Esses são os fundamentos sólidos da área.
2. DIVERGÊNCIAS: Quando houver posições conflitantes entre livros, registre explicitamente as posições de cada fonte e NÃO escolha um "vencedor".
3. TÓPICOS CRUZADOS: Organize o conhecimento por TÓPICOS DO TEMA (não por livro). Ex: em "Direito de Família", organize por "Divórcio", "Guarda", "Alimentos" — não por "Livro A" e "Livro B".
4. FONTES: Sempre que citar uma regra ou conceito importante, identifique de qual livro veio (use o nome do arquivo como referência).
5. DENSIDADE: O conteúdo deve ser denso, prático e útil. Priorize regras de decisão ("Use X se Y"), não narrativas.
6. FORMATO MARKDOWN: Use cabeçalhos claros (##, ###), listas, tabelas comparativas quando houver divergência.

Conteúdos dos livros:
---
{conteudo_livros}
---

Gere a resposta estruturada em JSON com quatro chaves obrigatórias:
"skill_master": "conteúdo markdown do arquivo SKILL.md (modelos mentais centrais + índice temático)...",
"glossary": "conteúdo markdown do glossário unificado de termos técnicos...",
"cheatsheet": "conteúdo markdown do cheatsheet com regras rápidas, tabelas e decisões práticas...",
"divergencias": "conteúdo markdown listando onde os livros DIVERGEM e as posições de cada um..."
"""


def compilar_skill_tematica(
    tema_slug: str,
    arquivos_md: List[Path],
    saida_skill_base: Path,
) -> None:
    """
    Lê múltiplos arquivos Markdown de livros sobre o mesmo tema e usa o Gemini para
    gerar uma Skill unificada que cruza e sintetiza o conhecimento de todos eles.
    
    Args:
        tema_slug:       Nome do tema (vem do nome da subpasta, ex: 'direito-de-familia').
        arquivos_md:     Lista de caminhos para os arquivos .md gerados pelo converter.
        saida_skill_base: Pasta onde a Skill temática será salva.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        log.warning("GEMINI_API_KEY não configurada no .env. Ignorando geração de Skill Temática.")
        return

    if not arquivos_md:
        log.warning("Nenhum arquivo Markdown encontrado para o tema '%s'. Ignorando.", tema_slug)
        return

    log.info("=" * 60)
    log.info("Iniciando compilação de Skill TEMÁTICA: '%s'", tema_slug)
    log.info("Fontes (%d livros):", len(arquivos_md))
    for arq in arquivos_md:
        log.info("  - %s", arq.name)
    log.info("=" * 60)

    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.0-flash")
    except Exception as e:
        log.error("Falha ao inicializar a biblioteca google-generativeai: %s", e)
        return

    # Lê e agrega todos os conteúdos Markdown
    blocos_livros = []
    for arq in arquivos_md:
        if arq.exists() and arq.stat().st_size > 100:
            nome_livro = arq.stem.rsplit("_", 1)[0]  # Remove o hash do nome
            conteudo = arq.read_text(encoding="utf-8")
            # Limita cada livro a 80.000 chars para não estourar o contexto
            blocos_livros.append(f"## LIVRO: {nome_livro}\n\n{conteudo[:80000]}")
        else:
            log.warning("Arquivo '%s' não encontrado ou vazio, ignorando.", arq.name)

    if not blocos_livros:
        log.error("Nenhum conteúdo válido encontrado para o tema '%s'.", tema_slug)
        return

    conteudo_agregado = "\n\n---\n\n".join(blocos_livros)
    # Limita o total de input ao modelo (segurança contra contextos gigantes)
    conteudo_agregado = conteudo_agregado[:300000]

    # Cria diretório de saída
    saida_skill_base.mkdir(parents=True, exist_ok=True)

    # Gera a Skill temática com o prompt especializado
    prompt = PROMPT_TEMA.format(tema=tema_slug, conteudo_livros=conteudo_agregado)

    log.info("Enviando conteúdo para síntese temática ao Gemini (%d caracteres)...", len(conteudo_agregado))

    try:
        response = chamar_gemini_com_retry(
            model,
            prompt,
            generation_config={"response_mime_type": "application/json"}
        )
        dados = json.loads(response.text)

        # Grava os 4 arquivos da Skill temática
        (saida_skill_base / "SKILL.md").write_text(dados.get("skill_master", ""), encoding="utf-8")
        (saida_skill_base / "glossary.md").write_text(dados.get("glossary", ""), encoding="utf-8")
        (saida_skill_base / "cheatsheet.md").write_text(dados.get("cheatsheet", ""), encoding="utf-8")
        (saida_skill_base / "divergencias.md").write_text(dados.get("divergencias", ""), encoding="utf-8")

        log.info("✓ Skill Temática '%s' compilada com sucesso em: %s", tema_slug, saida_skill_base)
    except Exception as e:
        log.error("Erro ao gerar Skill Temática '%s': %s", tema_slug, e)
        # Fallback bruto
        try:
            response = chamar_gemini_com_retry(model, prompt)
            (saida_skill_base / "SKILL_TEMA_RAW.md").write_text(response.text, encoding="utf-8")
            log.warning("✓ Salvo backup bruto em SKILL_TEMA_RAW.md para o tema '%s'.", tema_slug)
        except Exception as fallback_err:
            log.error("Falha crítica na geração temática: %s", fallback_err)
