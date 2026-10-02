from __future__ import annotations

import hashlib
from io import BytesIO
import posixpath
import re
import unicodedata
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from bs4 import BeautifulSoup
from ebooklib import epub


SEGMENT_MAX_CHARS = 2800

STRUCTURAL_TITLES = {
    "capa",
    "cover",
    "creditos",
    "credits",
    "direitos autorais",
    "ficha catalografica",
    "folha de rosto",
    "indice",
    "navigation",
    "sumario",
    "table of contents",
    "title page",
}

STRUCTURAL_FILE_NAMES = {
    "cover",
    "copyright",
    "credits",
    "nav",
    "navigation",
    "titlepage",
    "toc",
}

CHAPTER_HEADING_PATTERN = re.compile(
    r"^(?:capitulo|chapter|parte|part|livro|book)\b|^(?:\d{1,3}|[ivxlcdm]{1,8})$"
)


def _repair_missing_manifest_items(content: bytes) -> tuple[bytes, list[str]]:
    """Remove manifest entries for absent accessory files.

    Some EPUBs reference obsolete templates, styles, fonts or images that are
    not actually present in the ZIP. Readers usually ignore them, but
    EbookLib raises KeyError while loading the manifest. Missing documents in
    the spine are never removed because that would hide lost book content.
    """
    try:
        source_buffer = BytesIO(content)
        with ZipFile(source_buffer, "r") as source:
            names = set(source.namelist())
            lower_names = {name.lower(): name for name in names}
            container_name = "META-INF/container.xml"
            if container_name not in names:
                return content, []

            container_root = ElementTree.fromstring(source.read(container_name))
            rootfile = container_root.find(".//{*}rootfile")
            if rootfile is None:
                return content, []
            opf_name = rootfile.attrib.get("full-path", "").lstrip("/")
            if not opf_name or opf_name not in names:
                return content, []

            opf_root = ElementTree.fromstring(source.read(opf_name))
            manifest = opf_root.find(".//{*}manifest")
            if manifest is None:
                return content, []

            spine_ids = {
                item.attrib.get("idref")
                for item in opf_root.findall(".//{*}spine/{*}itemref")
                if item.attrib.get("idref")
            }
            opf_directory = posixpath.dirname(opf_name)
            removed: list[str] = []
            changed = False

            for item in list(manifest):
                if not item.tag.endswith("item"):
                    continue
                href = item.attrib.get("href", "")
                clean_href = unquote(href.split("#", 1)[0].split("?", 1)[0])
                archive_name = posixpath.normpath(
                    posixpath.join(opf_directory, clean_href)
                ).lstrip("./")
                if archive_name in names:
                    continue

                actual_name = lower_names.get(archive_name.lower())
                if actual_name:
                    relative_name = posixpath.relpath(actual_name, opf_directory or ".")
                    item.set("href", relative_name)
                    changed = True
                    continue

                item_id = item.attrib.get("id")
                media_type = item.attrib.get("media-type", "")
                is_document = media_type in {"application/xhtml+xml", "text/html"}
                if item_id in spine_ids or is_document:
                    raise ValueError(
                        f"O EPUB está sem um capítulo declarado no pacote: {archive_name}"
                    )

                manifest.remove(item)
                removed.append(archive_name)
                changed = True

            if not changed:
                return content, []

            repaired_opf = ElementTree.tostring(
                opf_root,
                encoding="utf-8",
                xml_declaration=True,
            )
            output = BytesIO()
            with ZipFile(output, "w") as target:
                for entry in source.infolist():
                    entry_content = repaired_opf if entry.filename == opf_name else source.read(entry)
                    target.writestr(entry, entry_content)
            return output.getvalue(), removed
    except BadZipFile as exc:
        raise ValueError("O arquivo enviado não é um EPUB válido.") from exc


def _metadata_value(book: epub.EpubBook, namespace: str, name: str, default: str) -> str:
    values = book.get_metadata(namespace, name)
    if not values:
        return default
    value = str(values[0][0]).strip()
    return value or default


def _clean_document(content: bytes) -> tuple[str, str]:
    soup = BeautifulSoup(content, "html.parser")
    for element in soup(["script", "style", "nav", "svg"]):
        element.decompose()

    # Prefer a visible heading. Many EPUBs repeat the book name in every HTML
    # <title>, while the real chapter label appears in <h1> or <h2>.
    heading = soup.find(["h1", "h2", "h3"]) or soup.find("title")
    title = heading.get_text(" ", strip=True) if heading else ""
    body = soup.body or soup
    blocks: list[str] = []

    for element in body.find_all(["h1", "h2", "h3", "h4", "p", "li", "blockquote"]):
        text = re.sub(r"\s+", " ", element.get_text(" ", strip=True)).strip()
        if text and (not blocks or text != blocks[-1]):
            blocks.append(text)

    if not blocks:
        fallback = re.sub(r"\s+", " ", body.get_text(" ", strip=True)).strip()
        blocks = [fallback] if fallback else []
    return title, "\n\n".join(blocks)


def _normalized_label(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    without_accents = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return re.sub(r"[^a-z0-9]+", " ", without_accents.lower()).strip()


def _normalized_href(value: str) -> str:
    clean = unquote((value or "").split("#", 1)[0].split("?", 1)[0])
    return posixpath.normpath(clean).lstrip("./")


def _is_structural_document(title: str, file_name: str, properties=None) -> bool:
    label = _normalized_label(title)
    stem = _normalized_label(Path(file_name).stem).replace(" ", "")
    property_names = {_normalized_label(str(value)) for value in (properties or [])}

    if label in STRUCTURAL_TITLES or label.startswith("copyright"):
        return True
    if stem in STRUCTURAL_FILE_NAMES:
        return True
    return bool(property_names & {"cover image", "nav"})


def _looks_like_chapter_heading(title: str) -> bool:
    return bool(CHAPTER_HEADING_PATTERN.match(_normalized_label(title)))


def _flatten_toc(nodes) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []

    def visit(node) -> None:
        if isinstance(node, (tuple, list)):
            if len(node) == 2 and not isinstance(node[0], (str, bytes)):
                visit(node[0])
                visit(node[1])
                return
            for child in node:
                visit(child)
            return

        href = getattr(node, "href", None) or getattr(node, "file_name", None)
        title = getattr(node, "title", None)
        if href:
            entries.append((str(href), str(title or "").strip()))

    visit(nodes)
    return entries


def _find_document_index(href: str, documents: list[dict]) -> int | None:
    target = _normalized_href(href)
    if not target:
        return None

    exact = [
        index
        for index, document in enumerate(documents)
        if document["normalized_name"] == target
    ]
    if len(exact) == 1:
        return exact[0]

    suffix = [
        index
        for index, document in enumerate(documents)
        if document["normalized_name"].endswith(f"/{target}")
        or target.endswith(f"/{document['normalized_name']}")
    ]
    if len(suffix) == 1:
        return suffix[0]

    basename = posixpath.basename(target)
    basename_matches = [
        index
        for index, document in enumerate(documents)
        if posixpath.basename(document["normalized_name"]) == basename
    ]
    return basename_matches[0] if len(basename_matches) == 1 else None


def _unresolved_toc_targets(book: epub.EpubBook, documents: list[dict]) -> list[str]:
    unresolved: list[str] = []
    for href, title in _flatten_toc(book.toc):
        if "://" in href or _find_document_index(href, documents) is not None:
            continue
        clean_href = _normalized_href(href)
        if Path(clean_href).suffix.lower() not in {".xhtml", ".html", ".htm"}:
            continue
        if _is_structural_document(title, clean_href):
            continue
        unresolved.append(clean_href)
    return list(dict.fromkeys(unresolved))


def _spine_documents(book: epub.EpubBook) -> list[dict]:
    documents: list[dict] = []
    for spine_position, spine_entry in enumerate(book.spine):
        item_id = spine_entry[0] if isinstance(spine_entry, tuple) else spine_entry
        item = book.get_item_with_id(item_id)
        if item is None or not hasattr(item, "get_content"):
            continue

        file_name = str(item.get_name() or "")
        document_title, text = _clean_document(item.get_content())
        # EPUBs sometimes keep only the chapter heading in one XHTML file and
        # the body in the next one. Even a very short document must therefore
        # remain in the reading order.
        if not text.strip():
            continue

        documents.append(
            {
                "spine_position": spine_position,
                "file_name": file_name,
                "normalized_name": _normalized_href(file_name),
                "title": document_title or str(getattr(item, "title", "") or "").strip(),
                "text": text,
                "properties": list(getattr(item, "properties", []) or []),
            }
        )
    return documents


def _chapter_from_documents(title: str, documents: list[dict], index: int) -> dict | None:
    usable = [
        document
        for document in documents
        if not _is_structural_document(
            document["title"], document["file_name"], document["properties"]
        )
    ]
    if not usable:
        return None

    text = "\n\n".join(document["text"] for document in usable).strip()
    segments = split_text(text)
    if not segments:
        return None

    chapter_title = title.strip() or usable[0]["title"] or f"Seção {index + 1}"
    return {
        "index": index,
        "spine_position": usable[0]["spine_position"],
        "title": chapter_title,
        "segments": segments,
        "char_count": sum(len(segment) for segment in segments),
        "source_documents": [document["file_name"] for document in usable],
    }


def _chapters_from_toc(book: epub.EpubBook, documents: list[dict]) -> list[dict]:
    anchors_by_index: dict[int, dict] = {}
    for href, title in _flatten_toc(book.toc):
        document_index = _find_document_index(href, documents)
        if document_index is None:
            continue
        document = documents[document_index]
        structural = _is_structural_document(
            title or document["title"],
            document["file_name"],
            document["properties"],
        )
        current = anchors_by_index.get(document_index)
        if current is None or (current["structural"] and not structural):
            anchors_by_index[document_index] = {
                "title": title or document["title"],
                "structural": structural,
            }

    # Recover chapter boundaries omitted from a malformed or simplified TOC.
    # A strong heading in the spine is a safer boundary than merging its text
    # into the previous chapter.
    for document_index, document in enumerate(documents):
        if document_index in anchors_by_index:
            continue
        if _looks_like_chapter_heading(document["title"]):
            anchors_by_index[document_index] = {
                "title": document["title"],
                "structural": False,
            }

    anchor_indexes = sorted(anchors_by_index)
    if not anchor_indexes:
        return []

    chapters: list[dict] = []
    for position, start in enumerate(anchor_indexes):
        anchor = anchors_by_index[start]
        if anchor["structural"]:
            continue
        end = (
            anchor_indexes[position + 1]
            if position + 1 < len(anchor_indexes)
            else len(documents)
        )
        chapter = _chapter_from_documents(
            anchor["title"], documents[start:end], len(chapters)
        )
        if chapter:
            chapters.append(chapter)
    return chapters


def _recover_unassigned_documents(
    chapters: list[dict], documents: list[dict]
) -> list[dict]:
    assigned = {
        file_name
        for chapter in chapters
        for file_name in chapter.get("source_documents", [])
    }
    recovered: list[dict] = []
    for document in documents:
        if document["file_name"] in assigned or _is_structural_document(
            document["title"], document["file_name"], document["properties"]
        ):
            continue
        chapter = _chapter_from_documents(document["title"], [document], 0)
        if chapter:
            recovered.append(chapter)

    merged = chapters + recovered
    merged.sort(key=lambda chapter: chapter["spine_position"])
    for index, chapter in enumerate(merged):
        chapter["index"] = index
    return merged


def _validate_document_coverage(chapters: list[dict], documents: list[dict]) -> None:
    expected = {
        document["file_name"]
        for document in documents
        if not _is_structural_document(
            document["title"], document["file_name"], document["properties"]
        )
    }
    covered = {
        file_name
        for chapter in chapters
        for file_name in chapter.get("source_documents", [])
    }
    missing = sorted(expected - covered)
    if missing:
        preview = ", ".join(missing[:5])
        suffix = f" e outros {len(missing) - 5}" if len(missing) > 5 else ""
        raise ValueError(
            "A validação encontrou partes textuais que não foram incorporadas: "
            f"{preview}{suffix}. O livro não foi salvo para evitar perda de conteúdo."
        )


def _chapters_from_spine(documents: list[dict]) -> list[dict]:
    chapters: list[dict] = []
    for document in documents:
        chapter = _chapter_from_documents(
            document["title"], [document], len(chapters)
        )
        if chapter:
            chapters.append(chapter)
    return chapters


def split_text(text: str, max_chars: int = SEGMENT_MAX_CHARS) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    segments: list[str] = []
    current = ""

    for paragraph in paragraphs:
        sentences = re.split(r"(?<=[.!?…:;])\s+", paragraph)
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            pieces = (
                [sentence[index : index + max_chars] for index in range(0, len(sentence), max_chars)]
                if len(sentence) > max_chars
                else [sentence]
            )
            for piece in pieces:
                candidate = f"{current} {piece}".strip()
                if current and len(candidate) > max_chars:
                    segments.append(current.strip())
                    current = piece
                else:
                    current = candidate
        if current:
            current += "\n"

    if current.strip():
        segments.append(current.strip())
    return segments


def extract_epub(content: bytes, filename: str = "livro.epub") -> dict:
    book_id = hashlib.sha256(content).hexdigest()
    repaired_content, ignored_resources = _repair_missing_manifest_items(content)
    book = epub.read_epub(BytesIO(repaired_content), options={"ignore_ncx": False})

    title = _metadata_value(book, "DC", "title", Path(filename).stem)
    author = _metadata_value(book, "DC", "creator", "Autor não informado")
    documents = _spine_documents(book)
    unresolved_toc_targets = _unresolved_toc_targets(book, documents)
    chapters = _chapters_from_toc(book, documents)
    structure_source = "toc" if chapters else "spine"
    if not chapters:
        chapters = _chapters_from_spine(documents)
    chapters = _recover_unassigned_documents(chapters, documents)
    _validate_document_coverage(chapters, documents)

    if not chapters:
        raise ValueError("Não foi encontrado texto organizado no EPUB.")

    return {
        "book_id": book_id,
        "title": title,
        "author": author,
        "chapters": chapters,
        "total_chars": sum(chapter["char_count"] for chapter in chapters),
        "ignored_resources": ignored_resources,
        "structure_source": structure_source,
        "content_document_count": sum(
            1
            for document in documents
            if not _is_structural_document(
                document["title"], document["file_name"], document["properties"]
            )
        ),
        "covered_document_count": len(
            {
                file_name
                for chapter in chapters
                for file_name in chapter.get("source_documents", [])
            }
        ),
        "unresolved_toc_targets": unresolved_toc_targets,
    }
