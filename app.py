from __future__ import annotations

import os

import streamlit as st
from streamlit_gsheets import GSheetsConnection

from auth import require_login
from content_store import delete_saved_book, list_saved_books, load_saved_book, save_book_content
from epub_reader import extract_epub
from progress_store import (
    delete_book_progress,
    load_book_progress,
    load_user_progress,
    save_book_progress,
)
from speech import generate_audio


PROGRESS_WORKSHEET = os.getenv("EPUB_PROGRESS_WORKSHEET", "audiobooks_progress")
CONTENT_WORKSHEET = os.getenv("EPUB_CONTENT_WORKSHEET", "audiobooks_content")

VOICES = {
    "pt-BR-AntonioNeural": "Antônio — voz masculina",
    "pt-BR-FranciscaNeural": "Francisca — voz feminina",
}

RATES = {
    "0,85×": -15,
    "1×": 0,
    "1,15×": 15,
    "1,3×": 30,
    "1,5×": 50,
}


@st.cache_data(show_spinner=False)
def parse_epub(content: bytes, filename: str) -> dict:
    return extract_epub(content, filename)


def reset_book_session() -> None:
    for key in (
        "active_book",
        "chapter_index",
        "segment_index",
        "voice",
        "rate",
        "completed",
    ):
        st.session_state.pop(key, None)


def mark_upload_unsaved() -> None:
    st.session_state.saved_upload_book_id = None


def initialize_position(book: dict, saved: dict | None, owner_id: str) -> None:
    identity = f"{owner_id}:{book['book_id']}"
    if st.session_state.get("active_book") == identity:
        return

    st.session_state.active_book = identity
    st.session_state.chapter_index = int(saved.get("chapter_index", 0)) if saved else 0
    st.session_state.segment_index = int(saved.get("segment_index", 0)) if saved else 0
    st.session_state.voice = (
        str(saved.get("voice", "pt-BR-AntonioNeural"))
        if saved
        else "pt-BR-AntonioNeural"
    )
    st.session_state.rate = int(saved.get("rate", 0)) if saved else 0
    st.session_state.completed = bool(saved.get("completed", False)) if saved else False

    chapter_index = min(st.session_state.chapter_index, len(book["chapters"]) - 1)
    st.session_state.chapter_index = max(0, chapter_index)
    chapter = book["chapters"][st.session_state.chapter_index]
    segment_index = min(st.session_state.segment_index, len(chapter["segments"]) - 1)
    st.session_state.segment_index = max(0, segment_index)


def calculate_progress(book: dict, chapter_index: int, segment_index: int) -> float:
    completed_chars = sum(
        chapter["char_count"] for chapter in book["chapters"][:chapter_index]
    )
    completed_chars += sum(
        len(segment)
        for segment in book["chapters"][chapter_index]["segments"][:segment_index]
    )
    if not book["total_chars"]:
        return 0.0
    return round(completed_chars / book["total_chars"] * 100, 2)


def reconcile_saved_position(book: dict, saved: dict) -> tuple[int, int]:
    """Map a saved position to a newly extracted book structure."""
    saved_title = str(saved.get("current_chapter_title", "")).strip().casefold()
    chapter_index = next(
        (
            index
            for index, chapter in enumerate(book["chapters"])
            if saved_title and chapter["title"].strip().casefold() == saved_title
        ),
        min(int(saved.get("chapter_index", 0)), len(book["chapters"]) - 1),
    )
    chapter_index = max(0, chapter_index)
    segment_index = min(
        max(0, int(saved.get("segment_index", 0))),
        len(book["chapters"][chapter_index]["segments"]) - 1,
    )
    return chapter_index, segment_index


def persist(
    conn: GSheetsConnection,
    book: dict,
    owner_id: str,
    completed: bool = False,
) -> None:
    percentage = 100.0 if completed else calculate_progress(
        book,
        st.session_state.chapter_index,
        st.session_state.segment_index,
    )
    save_book_progress(
        conn=conn,
        worksheet=PROGRESS_WORKSHEET,
        owner_id=owner_id,
        book=book,
        chapter_index=st.session_state.chapter_index,
        segment_index=st.session_state.segment_index,
        progress_percent=percentage,
        voice=st.session_state.voice,
        rate=st.session_state.rate,
        completed=completed,
    )
    st.session_state.completed = completed


def move_position(book: dict, direction: int) -> bool:
    chapter_index = st.session_state.chapter_index
    segment_index = st.session_state.segment_index
    chapter = book["chapters"][chapter_index]

    if direction > 0:
        if segment_index + 1 < len(chapter["segments"]):
            st.session_state.segment_index += 1
            return False
        if chapter_index + 1 < len(book["chapters"]):
            st.session_state.chapter_index += 1
            st.session_state.segment_index = 0
            return False
        return True

    if segment_index > 0:
        st.session_state.segment_index -= 1
    elif chapter_index > 0:
        st.session_state.chapter_index -= 1
        previous = book["chapters"][st.session_state.chapter_index]
        st.session_state.segment_index = len(previous["segments"]) - 1
    return False


def get_connection() -> GSheetsConnection:
    try:
        return st.connection("gsheets", type=GSheetsConnection)
    except Exception as exc:
        st.error(
            "Não foi possível acessar a planilha. Confira as credenciais do conector "
            f"Google Sheets. Detalhe: {exc}"
        )
        st.stop()


def render_player(conn: GSheetsConnection, owner_id: str, book: dict) -> None:
    try:
        saved_progress = load_book_progress(
            conn,
            worksheet=PROGRESS_WORKSHEET,
            owner_id=owner_id,
            book_id=book["book_id"],
        )
    except Exception as exc:
        st.error(f"Não foi possível carregar o progresso: {exc}")
        return

    initialize_position(book, saved_progress, owner_id)

    if book.get("ignored_resources"):
        ignored_names = ", ".join(book["ignored_resources"][:3])
        remaining = len(book["ignored_resources"]) - 3
        suffix = f" e outros {remaining}" if remaining > 0 else ""
        st.warning(
            "O EPUB continha recursos acessórios ausentes. Eles foram ignorados sem "
            f"alterar os capítulos: {ignored_names}{suffix}."
        )

    if book.get("unresolved_toc_targets"):
        st.info(
            "Algumas referências do sumário interno não coincidiram com a ordem "
            "do EPUB. Todo o texto encontrado foi mantido e validado."
        )

    with st.sidebar:
        st.divider()
        st.write(f"**{book['title']}**")
        st.caption(book["author"])

        chapter_options = list(range(len(book["chapters"])))
        selected_chapter = st.selectbox(
            "Capítulo ou seção",
            options=chapter_options,
            index=st.session_state.chapter_index,
            format_func=lambda index: book["chapters"][index]["title"],
            key=(
                f"chapter_{owner_id}_{book['book_id']}_"
                f"{st.session_state.chapter_index}"
            ),
        )
        if selected_chapter != st.session_state.chapter_index:
            st.session_state.chapter_index = selected_chapter
            st.session_state.segment_index = 0
            st.session_state.completed = False
            persist(conn, book, owner_id)
            st.rerun()

        voice_ids = list(VOICES)
        selected_voice = st.selectbox(
            "Voz",
            options=voice_ids,
            index=voice_ids.index(st.session_state.voice)
            if st.session_state.voice in voice_ids
            else 0,
            format_func=lambda voice_id: VOICES[voice_id],
            key=f"voice_{book['book_id']}",
        )
        st.session_state.voice = selected_voice

        rate_labels = list(RATES)
        current_rate_label = next(
            (label for label, value in RATES.items() if value == st.session_state.rate),
            "1×",
        )
        selected_rate_label = st.selectbox(
            "Velocidade da voz",
            options=rate_labels,
            index=rate_labels.index(current_rate_label),
            key=f"rate_{book['book_id']}",
        )
        st.session_state.rate = RATES[selected_rate_label]

    chapter = book["chapters"][st.session_state.chapter_index]
    st.session_state.segment_index = min(
        st.session_state.segment_index, len(chapter["segments"]) - 1
    )
    segment = chapter["segments"][st.session_state.segment_index]

    progress = 100.0 if st.session_state.completed else calculate_progress(
        book,
        st.session_state.chapter_index,
        st.session_state.segment_index,
    )
    st.progress(progress / 100, text=f"{progress:.1f}% concluído".replace(".", ","))

    header_left, header_right = st.columns([4, 1])
    with header_left:
        st.caption(
            f"Seção {st.session_state.chapter_index + 1} de {len(book['chapters'])} · "
            f"trecho {st.session_state.segment_index + 1} de {len(chapter['segments'])}"
        )
        st.header(chapter["title"])
    with header_right:
        if saved_progress:
            st.metric(
                "Salvo na planilha",
                f"{float(saved_progress['progress_percent']):.1f}%",
            )

    with st.spinner("Gerando o áudio deste trecho…"):
        try:
            audio_bytes = generate_audio(
                segment,
                voice=st.session_state.voice,
                rate=st.session_state.rate,
            )
        except Exception as exc:
            st.error(f"Não foi possível gerar o áudio: {exc}")
            return

    st.audio(audio_bytes, format="audio/mp3", width="stretch")

    with st.expander("Ver o texto deste trecho"):
        st.write(segment)

    previous_col, save_col, next_col = st.columns([1, 1.2, 1])

    with previous_col:
        if st.button("Trecho anterior", use_container_width=True):
            move_position(book, -1)
            persist(conn, book, owner_id)
            st.rerun()

    with save_col:
        if st.button("Salvar este ponto", type="secondary", use_container_width=True):
            persist(conn, book, owner_id)
            st.success("Progresso salvo no Google Sheets.")

    with next_col:
        if st.button("Concluir e avançar", type="primary", use_container_width=True):
            finished = move_position(book, 1)
            persist(conn, book, owner_id, completed=finished)
            if finished:
                st.balloons()
                st.success("Livro concluído e registrado na planilha.")
            else:
                st.rerun()


def render_library(conn: GSheetsConnection, owner_id: str) -> None:
    st.title("Meus audiolivros")
    st.caption("Escolha um título salvo para continuar do ponto registrado.")

    try:
        books = list_saved_books(conn, CONTENT_WORKSHEET, owner_id)
    except Exception as exc:
        st.error(f"Não foi possível carregar os audiolivros: {exc}")
        return

    if books.empty:
        st.info("Nenhum audiolivro foi salvo. Use ‘Adicionar livro’ no menu lateral.")
        return

    labels = {
        str(row["book_id"]): f"{row['title']} — {row['author']}"
        for _, row in books.iterrows()
    }
    book_ids = list(labels)
    selected_id = st.selectbox(
        "Audiolivro",
        options=book_ids,
        format_func=lambda book_id: labels[book_id],
        key="library_book_selector",
    )

    try:
        book = load_saved_book(conn, CONTENT_WORKSHEET, owner_id, selected_id)
    except Exception as exc:
        st.error(f"Não foi possível recuperar o conteúdo salvo: {exc}")
        return
    if book is None:
        st.error("O audiolivro selecionado não foi encontrado.")
        return

    st.subheader(book["title"])
    st.caption(f"{book['author']} · {len(book['chapters'])} seções")
    render_player(conn, owner_id, book)

    with st.expander("Excluir este audiolivro"):
        confirmed = st.checkbox(
            "Confirmo a exclusão do conteúdo e do progresso.",
            key=f"delete_confirm_{selected_id}",
        )
        if st.button(
            "Excluir definitivamente",
            disabled=not confirmed,
            key=f"delete_{selected_id}",
        ):
            try:
                delete_saved_book(conn, CONTENT_WORKSHEET, owner_id, selected_id)
                delete_book_progress(conn, PROGRESS_WORKSHEET, owner_id, selected_id)
            except Exception as exc:
                st.error(f"Não foi possível excluir o audiolivro: {exc}")
                return
            reset_book_session()
            st.success("Audiolivro excluído.")
            st.rerun()


def render_add_book(conn: GSheetsConnection, owner_id: str) -> None:
    st.title("Adicionar livro")
    st.caption("O EPUB é necessário somente neste primeiro cadastro.")

    uploaded_file = st.file_uploader("Arquivo EPUB", type=["epub"])
    if uploaded_file is None:
        st.info("Selecione um EPUB para extrair e salvar as seções.")
        return

    try:
        book = parse_epub(uploaded_file.getvalue(), uploaded_file.name)
    except Exception as exc:
        st.error(f"Não foi possível abrir o EPUB: {exc}")
        return

    if st.session_state.get("metadata_book_id") != book["book_id"]:
        st.session_state.metadata_book_id = book["book_id"]
        st.session_state.book_name_input = book["title"]
        st.session_state.book_author_input = book["author"]
        st.session_state.saved_upload_book_id = None

    name_col, author_col = st.columns(2)
    with name_col:
        book_name = st.text_input(
            "Nome do livro",
            key="book_name_input",
            on_change=mark_upload_unsaved,
        )
    with author_col:
        book_author = st.text_input(
            "Autor",
            key="book_author_input",
            on_change=mark_upload_unsaved,
        )

    if not book_name.strip() or not book_author.strip():
        st.warning("Informe o nome do livro e o autor para continuar.")
        return

    book["title"] = book_name.strip()
    book["author"] = book_author.strip()
    st.caption(
        f"{len(book['chapters'])} seções · "
        f"{sum(len(chapter['segments']) for chapter in book['chapters'])} trechos · "
        f"{book.get('covered_document_count', len(book['chapters']))} arquivos de texto validados"
    )

    if st.button("Salvar audiolivro", type="primary", use_container_width=True):
        try:
            with st.spinner("Salvando o conteúdo na planilha…"):
                save_book_content(
                    conn,
                    worksheet=CONTENT_WORKSHEET,
                    owner_id=owner_id,
                    book=book,
                    file_name=uploaded_file.name,
                )
                saved = load_book_progress(
                    conn,
                    worksheet=PROGRESS_WORKSHEET,
                    owner_id=owner_id,
                    book_id=book["book_id"],
                )
                if saved is None:
                    save_book_progress(
                        conn=conn,
                        worksheet=PROGRESS_WORKSHEET,
                        owner_id=owner_id,
                        book=book,
                        chapter_index=0,
                        segment_index=0,
                        progress_percent=0,
                        voice="pt-BR-AntonioNeural",
                        rate=0,
                        completed=False,
                    )
                else:
                    chapter_index, segment_index = reconcile_saved_position(book, saved)
                    progress_percent = calculate_progress(
                        book, chapter_index, segment_index
                    )
                    save_book_progress(
                        conn=conn,
                        worksheet=PROGRESS_WORKSHEET,
                        owner_id=owner_id,
                        book=book,
                        chapter_index=chapter_index,
                        segment_index=segment_index,
                        progress_percent=(
                            100.0 if bool(saved.get("completed", False)) else progress_percent
                        ),
                        voice=str(saved.get("voice", "pt-BR-AntonioNeural")),
                        rate=int(saved.get("rate", 0)),
                        completed=bool(saved.get("completed", False)),
                    )
        except Exception as exc:
            st.error(f"Não foi possível salvar o audiolivro: {exc}")
            return
        reset_book_session()
        st.session_state.saved_upload_book_id = book["book_id"]
        st.success("Audiolivro salvo. Nos próximos acessos, o EPUB não será necessário.")

    if st.session_state.get("saved_upload_book_id") == book["book_id"]:
        st.divider()
        render_player(conn, owner_id, book)


def render_dashboard(conn: GSheetsConnection, owner_id: str) -> None:
    st.title("Dashboard")
    st.caption("Acompanhe seus audiolivros e o avanço registrado na planilha.")

    try:
        books = load_user_progress(conn, PROGRESS_WORKSHEET, owner_id)
    except Exception as exc:
        st.error(f"Não foi possível carregar o dashboard: {exc}")
        return

    if books.empty:
        st.info("Você ainda não possui audiolivros registrados.")
        return

    completed = int(books["completed"].sum())
    in_progress = len(books) - completed
    average = float(books["progress_percent"].mean())
    completed_chapters = int(
        books.apply(
            lambda row: row["total_chapters"]
            if row["completed"] and row["total_chapters"] > 0
            else row["chapter_index"],
            axis=1,
        ).sum()
    )

    columns = st.columns(4)
    columns[0].metric("Audiolivros", len(books))
    columns[1].metric("Concluídos", completed)
    columns[2].metric("Em andamento", in_progress)
    columns[3].metric("Progresso médio", f"{average:.1f}%".replace(".", ","))

    st.subheader("Progresso por audiolivro")
    chart = books[["title", "progress_percent"]].set_index("title")
    st.bar_chart(chart, horizontal=True, height=max(260, min(560, len(chart) * 55)))

    st.caption(f"Seções concluídas: {completed_chapters}")
    table = books.copy()
    table["status"] = table["completed"].map(
        {True: "Concluído", False: "Em andamento"}
    )
    table["updated_at"] = table["updated_at"].dt.tz_convert(
        "America/Sao_Paulo"
    ).dt.strftime("%d/%m/%Y %H:%M")
    table = table.rename(
        columns={
            "title": "Livro",
            "author": "Autor",
            "progress_percent": "Progresso",
            "current_chapter_title": "Seção atual",
            "status": "Status",
            "updated_at": "Última atividade",
        }
    )
    st.dataframe(
        table[
            [
                "Livro",
                "Autor",
                "Progresso",
                "Seção atual",
                "Status",
                "Última atividade",
            ]
        ],
        hide_index=True,
        use_container_width=True,
        column_config={
            "Progresso": st.column_config.ProgressColumn(
                "Progresso", min_value=0, max_value=100, format="%.1f%%"
            )
        },
    )


st.set_page_config(page_title="EPUB em voz", page_icon="🎧", layout="wide")

authenticator, username, display_name = require_login()

with st.sidebar:
    st.write(f"Olá, **{display_name}**")
    page = st.radio(
        "Navegação",
        ["Meus audiolivros", "Adicionar livro", "Dashboard"],
    )
    authenticator.logout(
        button_name="Sair",
        location="sidebar",
        key="epub_voz_logout",
        use_container_width=True,
    )

connection = get_connection()

if page == "Adicionar livro":
    render_add_book(connection, username)
elif page == "Dashboard":
    render_dashboard(connection, username)
else:
    render_library(connection, username)
