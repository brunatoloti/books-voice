from __future__ import annotations

import hashlib
import html
from collections import Counter
from io import BytesIO
from pathlib import Path
import re

from ebooklib import epub
from pypdf import PdfReader


CONVERTER_API_VERSION = 2

HEADING_PATTERN = re.compile(
    r"^(?:cap[ií]tulo|chapter|parte|part|livro|book|se[cç][aã]o|section|"
    r"pr[oó]logo|prologue|ep[ií]logo|epilogue|introdu[cç][aã]o|introduction|"
    r"conclus[aã]o|conclusion|ap[eê]ndice|appendix)\b",
    re.IGNORECASE,
)

LIST_ITEM_PATTERN = re.compile(
    r"^\s*(?P<marker>[•▪◦●*-]|\d+[.)]|[a-zA-Z][.)])\s+(?P<text>.+)$"
)


def _metadata_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip().lstrip("\ufeff")


def _normalize_page_text(text: str) -> str:
    """Turn visual PDF lines into paragraphs suitable for speech."""
    text = (
        (text or "")
        .replace("\x00", "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )
    text = re.sub(r"(?<=\w)-\n(?=[a-záàâãéêíóôõúç])", "", text)

    paragraphs: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        lines = [re.sub(r"\s+", " ", line).strip() for line in block.splitlines()]
        paragraph = " ".join(line for line in lines if line).strip()
        if paragraph:
            paragraphs.append(paragraph)
    return "\n\n".join(paragraphs)


def _normalize_layout_text(text: str) -> str:
    text = (
        (text or "")
        .replace("\x00", "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\t", "    ")
    )
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    normalized: list[str] = []
    blank = False
    for line in lines:
        if line.strip():
            normalized.append(line)
            blank = False
        elif not blank:
            normalized.append("")
            blank = True
    return "\n".join(normalized)


def _remove_repeated_margins(pages: list[str]) -> list[str]:
    """Remove headers and footers repeated on most PDF pages."""
    candidates: Counter[str] = Counter()
    page_lines: list[list[str]] = []
    for page in pages:
        lines = page.splitlines()
        page_lines.append(lines)
        visible = [line for line in lines if line.strip()]
        margins = visible[:2] + visible[-2:]
        candidates.update(
            {
                re.sub(r"\s+", " ", line).strip()
                for line in margins
                if len(re.sub(r"\s+", " ", line).strip()) >= 3
            }
        )

    populated_pages = sum(bool(page.strip()) for page in pages)
    threshold = max(3, (populated_pages + 1) // 2)
    repeated = {text for text, count in candidates.items() if count >= threshold}
    if not repeated:
        return pages

    cleaned_pages: list[str] = []
    for lines in page_lines:
        visible_indexes = [index for index, line in enumerate(lines) if line.strip()]
        margin_indexes = set(visible_indexes[:2] + visible_indexes[-2:])
        cleaned = [
            line
            for index, line in enumerate(lines)
            if not (
                index in margin_indexes
                and re.sub(r"\s+", " ", line).strip() in repeated
            )
        ]
        cleaned_pages.append(_normalize_layout_text("\n".join(cleaned)))
    return cleaned_pages


def extract_pdf(content: bytes, password: str = "") -> dict:
    """Extract text and basic metadata from a PDF held in memory."""
    if not content:
        raise ValueError("O arquivo PDF está vazio.")

    try:
        reader = PdfReader(BytesIO(content), strict=False)
        if reader.is_encrypted and reader.decrypt(password or "") == 0:
            raise ValueError("O PDF é protegido. Informe a senha correta para convertê-lo.")

        metadata = reader.metadata or {}
        pages: list[str] = []
        layout_pages: list[str] = []
        for page in reader.pages:
            plain_text = page.extract_text() or ""
            try:
                layout_text = page.extract_text(
                    extraction_mode="layout",
                    layout_mode_space_vertically=True,
                ) or plain_text
            except (TypeError, ValueError):
                layout_text = plain_text
            pages.append(_normalize_page_text(plain_text))
            layout_pages.append(_normalize_layout_text(layout_text))
        layout_pages = _remove_repeated_margins(layout_pages)
    except ValueError:
        raise
    except Exception as exc:
        if exc.__class__.__name__ in {"FileNotDecryptedError", "WrongPasswordError"}:
            raise ValueError(
                "O PDF é protegido. Informe a senha correta para convertê-lo."
            ) from exc
        raise ValueError("Não foi possível ler o PDF. Verifique se o arquivo é válido.") from exc

    if not pages:
        raise ValueError("O PDF não possui páginas.")

    text_page_count = sum(bool(page) for page in pages)
    if text_page_count == 0:
        raise ValueError(
            "Nenhum texto selecionável foi encontrado. PDFs digitalizados como imagem "
            "precisam passar por OCR antes da conversão."
        )

    return {
        "source_id": hashlib.sha256(content).hexdigest(),
        "title": _metadata_text(metadata.get("/Title")),
        "author": _metadata_text(metadata.get("/Author")),
        "pages": pages,
        "layout_pages": layout_pages,
        "total_pages": len(pages),
        "text_page_count": text_page_count,
        "empty_page_numbers": [
            index for index, page in enumerate(pages, start=1) if not page
        ],
        "total_characters": sum(len(page) for page in pages),
    }


def _page_html(text: str) -> str:
    paragraphs = [
        f"<p>{html.escape(paragraph)}</p>"
        for paragraph in text.split("\n\n")
        if paragraph.strip()
    ]
    return "\n".join(paragraphs)


def _join_wrapped_lines(lines: list[str]) -> str:
    paragraph = ""
    for raw_line in lines:
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        if paragraph.endswith("-") and re.match(r"^[a-záàâãéêíóôõúç]", line):
            paragraph = f"{paragraph[:-1]}{line}"
        else:
            paragraph = f"{paragraph} {line}".strip()
    return paragraph


def _looks_like_heading(line: str) -> bool:
    text = re.sub(r"\s+", " ", line).strip()
    if not text or len(text) > 120 or re.fullmatch(r"\d{1,4}", text):
        return False
    if HEADING_PATTERN.match(text):
        return True

    letters = [character for character in text if character.isalpha()]
    if len(letters) >= 3:
        uppercase_ratio = sum(character.isupper() for character in letters) / len(letters)
        if uppercase_ratio >= 0.75:
            return True

    words = text.split()
    title_words = sum(word[:1].isupper() for word in words if word[:1].isalpha())
    return (
        len(text) <= 60
        and 1 <= len(words) <= 8
        and title_words >= max(1, len(words) * 0.6)
        and not text.endswith((".", "!", "?", ";", ":", ","))
    )


def _list_html(lines: list[str]) -> str | None:
    matches = [LIST_ITEM_PATTERN.match(line) for line in lines]
    item_count = sum(match is not None for match in matches)
    if item_count < 2 or item_count < len(lines) / 2:
        return None

    items: list[str] = []
    markers: list[str] = []
    current = ""
    for line, match in zip(lines, matches):
        if match:
            if current:
                items.append(current)
            markers.append(match.group("marker"))
            current = match.group("text").strip()
        elif current:
            current = f"{current} {line.strip()}".strip()
    if current:
        items.append(current)

    ordered = bool(markers) and all(
        re.fullmatch(r"(?:\d+|[a-zA-Z])[.)]", marker) for marker in markers
    )
    tag = "ol" if ordered else "ul"
    content = "".join(f"<li>{html.escape(item)}</li>" for item in items)
    return f"<{tag}>{content}</{tag}>"


def _formatted_page_html(layout_text: str, fallback_text: str) -> str:
    source = layout_text.strip() or fallback_text.strip()
    if not source:
        return ""

    blocks = [block for block in re.split(r"\n\s*\n", source) if block.strip()]
    rendered: list[str] = []
    for block in blocks:
        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue

        if len(lines) == 1 and re.fullmatch(r"\s*\d{1,4}\s*", lines[0]):
            continue
        if len(lines) == 1 and _looks_like_heading(lines[0]):
            rendered.append(f"<h2>{html.escape(lines[0].strip())}</h2>")
            continue

        list_content = _list_html(lines)
        if list_content:
            rendered.append(list_content)
            continue

        column_lines = sum(bool(re.search(r"\S\s{3,}\S", line)) for line in lines)
        if len(lines) >= 2 and column_lines >= len(lines) / 2:
            columns = "<br/>".join(html.escape(line) for line in lines)
            rendered.append(
                f'<p class="layout-block">{columns}</p>'
            )
            continue

        average_length = sum(len(line.strip()) for line in lines) / len(lines)
        if len(lines) >= 3 and average_length <= 45:
            verse = "<br/>".join(html.escape(line.strip()) for line in lines)
            rendered.append(f'<p class="verse">{verse}</p>')
            continue

        paragraph = _join_wrapped_lines(lines)
        if paragraph:
            rendered.append(f"<p>{html.escape(paragraph)}</p>")

    return "\n".join(rendered) or _page_html(fallback_text)


def _line_layout_page_html(layout_text: str, fallback_text: str) -> str:
    source = layout_text.strip() or fallback_text.strip()
    if not source:
        return ""
    blocks = [block for block in re.split(r"\n\s*\n", source) if block.strip()]
    return "\n".join(
        f'<p class="preserved-lines">{html.escape(block.strip()).replace(chr(10), "<br/>")}</p>'
        for block in blocks
    )


def create_epub(
    pdf: dict,
    title: str,
    author: str,
    pages_per_section: int = 10,
    formatting_mode: str = "reflow",
) -> bytes:
    """Create an EPUB in memory from data returned by :func:`extract_pdf`."""
    title = title.strip()
    author = author.strip()
    if not title:
        raise ValueError("Informe o nome do livro.")
    if not author:
        raise ValueError("Informe o autor do livro.")
    if pages_per_section < 1:
        raise ValueError("A quantidade de páginas por seção deve ser maior que zero.")
    if formatting_mode not in {"reflow", "lines"}:
        raise ValueError("O modo de formatação do EPUB é inválido.")

    pages = list(pdf.get("pages") or [])
    layout_pages = list(pdf.get("layout_pages") or pages)
    if not any(page.strip() for page in pages):
        raise ValueError("O PDF não possui texto para converter.")

    book = epub.EpubBook()
    book.set_identifier(f"pdf-{pdf['source_id']}")
    book.set_title(title)
    book.set_language("pt-BR")
    book.add_author(author)

    stylesheet = epub.EpubItem(
        uid="book-style",
        file_name="styles/book.css",
        media_type="text/css",
        content=(
            "body { font-family: serif; line-height: 1.55; margin: 5%; max-width: 44em; }\n"
            "h1 { margin: 0 0 1.5em; text-align: center; }\n"
            "h2 { margin: 1.8em 0 .8em; break-after: avoid; page-break-after: avoid; }\n"
            "p { margin: .7em 0; text-align: justify; orphans: 2; widows: 2; }\n"
            "ul, ol { margin: .8em 0 1em 1.4em; padding: 0; }\n"
            "li { margin: .35em 0; }\n"
            ".pdf-page + .pdf-page { break-before: page; page-break-before: always; }\n"
            ".page-number { color: #666; font-size: .75em; text-align: right; "
            "margin: 1.5em 0 1em; }\n"
            ".layout-block, .preserved-lines { font-family: monospace; font-size: .85em; "
            "white-space: pre-wrap; overflow-wrap: anywhere; text-align: left; }\n"
            ".verse { text-align: left; margin-left: 1.5em; }"
        ),
    )
    book.add_item(stylesheet)

    sections: list[epub.EpubHtml] = []
    for start in range(0, len(pages), pages_per_section):
        end = min(start + pages_per_section, len(pages))
        section_number = len(sections) + 1
        section_title = (
            f"Página {start + 1}"
            if end == start + 1
            else f"Páginas {start + 1}–{end}"
        )
        page_blocks: list[str] = []
        for page_number in range(start + 1, end + 1):
            page_text = pages[page_number - 1]
            if not page_text.strip():
                continue
            layout_text = (
                layout_pages[page_number - 1]
                if page_number - 1 < len(layout_pages)
                else page_text
            )
            page_content = (
                _formatted_page_html(layout_text, page_text)
                if formatting_mode == "reflow"
                else _line_layout_page_html(layout_text, page_text)
            )
            page_blocks.append(
                f'<section class="pdf-page" data-page="{page_number}">'
                f'<div class="page-number">Página {page_number}</div>'
                f"{page_content}</section>"
            )
        if not page_blocks:
            continue

        chapter = epub.EpubHtml(
            title=section_title,
            file_name=f"section_{section_number:04d}.xhtml",
            lang="pt-BR",
        )
        chapter.content = f"<h1>{html.escape(section_title)}</h1>\n" + "\n".join(
            page_blocks
        )
        chapter.add_item(stylesheet)
        book.add_item(chapter)
        sections.append(chapter)

    if not sections:
        raise ValueError("Não foi possível criar seções com o texto extraído.")

    book.toc = tuple(sections)
    book.spine = ["nav", *sections]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    output = BytesIO()
    epub.write_epub(output, book, {"raise_exceptions": True})
    return output.getvalue()


def epub_filename(pdf_filename: str) -> str:
    stem = Path(pdf_filename).stem.strip() or "livro"
    safe_stem = re.sub(r"[\\/:*?\"<>|]+", "-", stem).strip(" .") or "livro"
    return f"{safe_stem}.epub"
