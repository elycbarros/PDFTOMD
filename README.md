# 📄 PDF to Markdown High-Performance Pipeline

Este repositório contém um pipeline concorrente, robusto e idempotente para converter em massa arquivos PDF para Markdown estruturado de alta qualidade. Ele utiliza a poderosa engine **Docling (IBM)** para extrair textos via OCR avançado e reconstruir tabelas complexas de maneira precisa.

O projeto foi estruturado com foco em **boas práticas de engenharia de software**, tornando-o totalmente portátil, tolerante a falhas e pronto para ser executado em qualquer sistema operacional (Windows, macOS ou Linux).

---

## ⚡ Diferenciais Técnicos

* **Idempotência e Controle de Estado:** O script gera um arquivo `manifest.json`. Ele calcula o hash SHA-256 de cada PDF e sabe exatamente quais arquivos já foram processados com sucesso. Se você rodar o script novamente, ele ignora os convertidos, economizando processamento de IA.
* **Concorrência Thread-Safe:** Executa conversões paralelas usando `ThreadPoolExecutor`. Como os componentes de OCR do Docling não compartilham recursos facilmente em paralelo, utilizamos *Thread-Local Storage* (`threading.local`) para garantir isolamento de recursos de memória por thread.
* **Escrita Atômica:** O estado do progresso é salvo primeiramente em um arquivo temporário e depois substituído no sistema de arquivos. Isso impede a corrupção do manifest em caso de queda de energia ou fechamento abrupto do terminal.
* **Graceful Shutdown:** Captura sinais do sistema (`SIGINT` / `Ctrl+C`). Se interrompido, ele cancela as tarefas futuras de forma limpa, aguarda as threads atuais finalizarem a escrita em disco e salva o progresso atual.

---

## 🚀 Como Configurar e Instalar

### 1. Clonar o Repositório
```bash
git clone [https://github.com/seu-usuario/seu-repositorio.git](https://github.com/seu-usuario/seu-repositorio.git)
cd seu-repositorio