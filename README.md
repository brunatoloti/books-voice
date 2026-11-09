# EPUB em voz — Streamlit + Google Sheets

Aplicação em Streamlit que transforma arquivos EPUB em áudio por seção e registra o progresso em uma planilha do Google Sheets.

## Recursos

- upload de arquivos `.epub`;
- seções identificadas pelo sumário interno do EPUB, com alternativa baseada no *spine*;
- união dos arquivos que pertencem ao mesmo capítulo;
- descarte de páginas estruturais, como capa, créditos e sumário;
- validação de cobertura antes do salvamento, impedindo o cadastro caso alguma parte textual fique de fora;
- divisão automática de seções longas em trechos menores;
- vozes brasileiras masculina e feminina;
- cinco velocidades de voz;
- áudio gerado sob demanda com cache do Streamlit;
- progresso registrado por livro, seção e trecho;
- identificação do livro pelo conteúdo do arquivo, mesmo que ele seja renomeado;
- criação automática da aba `audiobooks_progress`, caso ainda não exista;
- compatibilidade com Streamlit Community Cloud.
- processamento do EPUB e do áudio diretamente em memória, sem arquivos temporários.
- reparo em memória de manifestos que apontam para recursos acessórios ausentes.
- login individual com `streamlit-authenticator`;
- cadastro do nome do livro e do autor no envio do EPUB;
- dashboard individual com totais, progresso médio, gráfico e histórico dos livros.
- biblioteca pessoal carregada após o login, sem novo envio do EPUB;
- conteúdo compactado em uma segunda aba do Google Sheets;
- exclusão protegida por confirmação.

## Como o progresso funciona

O player nativo do Streamlit não envia ao Python o segundo atual da reprodução. Por isso, cada seção é dividida em trechos de aproximadamente 2.800 caracteres. Ao clicar em **Concluir e avançar**, **Trecho anterior**, trocar de seção ou usar **Salvar este ponto**, a aplicação atualiza a planilha.

A retomada ocorre no início do trecho salvo. Normalmente, cada trecho corresponde a poucos minutos de áudio.

## Instalação

Requer Python 3.10 ou superior.

### Forma rápida

- Windows: execute `iniciar_windows.bat`.
- macOS: execute `iniciar_macos.command`. Se houver bloqueio na primeira abertura, clique com o botão direito no arquivo e escolha **Abrir**.

### Forma manual

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

macOS ou Linux:

```bash
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Configuração do Google Sheets

A aplicação usa o conector `gsheets`:

```python
connection = st.connection("gsheets", type=GSheetsConnection)
```

A conta de serviço precisa ter permissão de edição na planilha.

Para configurar um projeto separado:

1. Crie `.streamlit/secrets.toml`.
2. Preencha os dados da conta de serviço e a planilha.
3. Compartilhe a planilha com o e-mail informado em `client_email`, permitindo edição.

Nunca envie `secrets.toml` ao GitHub. O arquivo já está incluído no `.gitignore`.

No Streamlit Community Cloud, copie as mesmas configurações para **Settings → Secrets**.

## Configuração do login

No mesmo arquivo de Secrets, adicione a configuração de autenticação:

```toml
[auth.cookie]
name = "epub_voz_auth"
key = "uma-chave-longa-e-aleatoria"
expiry_days = 30

[auth.credentials.usernames.bruna]
email = "bruna@exemplo.com"
name = "Bruna"
password = "uma-senha-forte"
```

Crie uma seção `[auth.credentials.usernames.<usuario>]` para cada pessoa. O
`streamlit-authenticator` transforma automaticamente as senhas definidas nos
Secrets em hashes durante a inicialização. Os dados de cada usuário são
separados na planilha pelo nome de usuário usado no login. O formulário aceita
tanto o usuário quanto o e-mail, sem diferença entre letras maiúsculas e
minúsculas.

Depois de editar os Secrets no Streamlit Community Cloud, salve as alterações e
reinicie o aplicativo. Não deixe no ambiente publicado os valores que começam
com `SUBSTITUA_`, presentes apenas no arquivo de exemplo.

## Aba criada na planilha

O nome padrão é `audiobooks_progress`. As colunas são:

| Coluna | Conteúdo |
| --- | --- |
| `owner_id` | identificador do usuário ou perfil |
| `book_id` | SHA-256 do EPUB |
| `title` | título do livro |
| `author` | autor |
| `chapter_index` | capítulo atual, começando em zero |
| `segment_index` | trecho atual, começando em zero |
| `total_chapters` | quantidade de capítulos do EPUB |
| `current_chapter_title` | título do capítulo atual |
| `progress_percent` | percentual aproximado |
| `voice` | voz selecionada |
| `rate` | velocidade configurada |
| `completed` | conclusão do livro |
| `started_at` | primeiro registro do livro em UTC |
| `updated_at` | data e hora da atualização em UTC |

### Conteúdo dos audiolivros

A aba `audiobooks_content` guarda os capítulos extraídos. O conteúdo completo é
serializado em JSON, compactado com gzip e dividido em blocos de até 40 mil
caracteres. Cada bloco ocupa uma linha e é vinculado ao usuário e ao hash do
EPUB.

Depois do primeiro cadastro, o menu **Meus audiolivros** reconstrói o livro a
partir desses blocos. O arquivo `.epub` não precisa ser enviado novamente.

Livros cadastrados antes da correção da estrutura precisam ser enviados uma
vez novamente em **Adicionar livro**. O conteúdo salvo será atualizado e o
ponto anterior será associado pelo título da seção sempre que possível.

Durante o cadastro, a quantidade de arquivos de texto validados é exibida.
Referências divergentes do sumário geram um aviso, mas não bloqueiam o livro:
a ordem interna é usada como alternativa e todos os arquivos textuais são
conferidos antes do salvamento. O cadastro só é interrompido quando alguma
parte relevante realmente não puder ser incorporada.

Essa aba não deve ser editada manualmente, pois remover ou alterar um bloco pode
impedir a reconstrução do conteúdo.

## Personalização

As variáveis abaixo são opcionais:

```bash
EPUB_PROGRESS_WORKSHEET=audiobooks_progress
EPUB_CONTENT_WORKSHEET=audiobooks_content
```

O nome de usuário autenticado separa automaticamente os registros de cada
pessoa na mesma aba.

## Limitações

- A geração inicial de cada trecho exige internet. Depois, o resultado fica no cache enquanto a instância estiver ativa.
- Imagens, fontes e diagramação do EPUB não são armazenadas; a plataforma salva os capítulos necessários para a narração.
- EPUBs protegidos por DRM não são compatíveis.
