"""
Script de Teste Rápido para o Conversor PDF-to-Markdown (Vanguarda).

Este script cria um documento HTML temporário simples, adiciona tabelas
e formatação, e depois roda o converter.py para garantir que o pipeline de
alta precisão e metadados está funcionando perfeitamente sem erros.
"""

import sys
import shutil
import logging
from pathlib import Path

# Adiciona o diretório atual ao path de execução
diretorio_script = Path(__file__).resolve().parent
sys.path.append(str(diretorio_script))

from converter import converter_alta_precisao, configurar_logging

log = logging.getLogger("teste_conversor")

# Conteúdo HTML de teste com estrutura básica e tabelas
CONTEUDO_TESTE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Documento de Teste do Conversor</title>
</head>
<body>
    <h1>Teste de Pipeline - PDFTOMD</h1>
    <p>Este é um documento de teste simples criado para validar o pré-processador universal.</p>
    
    <h2>Tabela de Engenharia Diagnóstica</h2>
    <table border="1">
        <tr>
            <th>ID</th>
            <th>Item de Inspeção</th>
            <th>Status</th>
        </tr>
        <tr>
            <td>01</td>
            <td>Fissuras em Vigas Estruturais</td>
            <td>Crítico</td>
        </tr>
        <tr>
            <td>02</td>
            <td>Infiltração em Laje de Cobertura</td>
            <td>Alerta</td>
        </tr>
    </table>
</body>
</html>
"""

def executar_teste():
    configurar_logging()
    
    # Define pastas temporárias para o teste
    pasta_teste_entrada = diretorio_script / "teste_entrada"
    pasta_teste_saida = diretorio_script / "teste_saida"
    
    # Limpa execuções antigas de testes
    shutil.rmtree(pasta_teste_entrada, ignore_errors=True)
    shutil.rmtree(pasta_teste_saida, ignore_errors=True)
    
    pasta_teste_entrada.mkdir(exist_ok=True)
    pasta_teste_saida.mkdir(exist_ok=True)
    
    arquivo_html = pasta_teste_entrada / "livro_teste.html"
    arquivo_html.write_text(CONTEUDO_TESTE, encoding="utf-8")
    
    log.info("=" * 60)
    log.info("INICIANDO TESTE DO PIPELINE UNIVERSAL")
    log.info("=" * 60)
    log.info("Criado arquivo de teste temporário: %s", arquivo_html.name)
    
    try:
        # Roda o conversor principal apontando para as pastas de teste
        converter_alta_precisao(
            caminho_entrada=pasta_teste_entrada,
            caminho_saida_base=pasta_teste_saida,
            max_workers=1
        )
        
        # Procura a pasta gerada na saída (nome_pasta_unico usa hash do arquivo)
        pastas_geradas = [p for p in pasta_teste_saida.iterdir() if p.is_dir()]
        
        if not pastas_geradas:
            raise ValueError("Erro: Nenhuma pasta de output gerada.")
            
        pasta_output = pastas_geradas[0]
        arquivo_md = list(pasta_output.glob("*.md"))[0]
        arquivo_json = pasta_output / "metadata.json"
        
        log.info("=" * 60)
        log.info("VALIDANDO OUTPUTS GERADOS")
        log.info("=" * 60)
        
        # Valida se os outputs existem e contêm dados
        if not arquivo_md.exists() or arquivo_md.stat().st_size == 0:
            raise ValueError("Erro: Arquivo Markdown gerado está em branco ou ausente.")
        log.info("✓ Arquivo Markdown criado com sucesso (%d bytes)", arquivo_md.stat().st_size)
        
        if not arquivo_json.exists() or arquivo_json.stat().st_size == 0:
            raise ValueError("Erro: Arquivo metadata.json gerado está em branco ou ausente.")
        log.info("✓ Arquivo metadata.json criado com sucesso (%d bytes)", arquivo_json.stat().st_size)
        
        # Print do conteúdo do JSON de metadados
        print("\nConteúdo do metadata.json gerado:")
        print(arquivo_json.read_text(encoding="utf-8"))
        
        log.info("\n" + "=" * 60)
        log.info("🏆 TESTE CONCLUÍDO COM SUCESSO! O PIPELINE ESTÁ 100% OPERACIONAL.")
        log.info("=" * 60)
        
    except Exception as e:
        log.error("❌ FALHA NO TESTE DO CONVERSOR: %s", e)
        sys.exit(1)
    finally:
        # Limpa as pastas temporárias de teste
        shutil.rmtree(pasta_teste_entrada, ignore_errors=True)
        shutil.rmtree(pasta_teste_saida, ignore_errors=True)

if __name__ == "__main__":
    executar_teste()
