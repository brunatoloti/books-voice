from __future__ import annotations

import asyncio

import edge_tts
import streamlit as st


@st.cache_data(show_spinner=False, max_entries=300)
def generate_audio(text: str, voice: str, rate: int) -> bytes:
    async def synthesize() -> bytes:
        communication = edge_tts.Communicate(
            text=text,
            voice=voice,
            rate=f"{rate:+d}%",
        )
        audio_parts: list[bytes] = []
        async for chunk in communication.stream():
            if chunk["type"] == "audio":
                audio_parts.append(chunk["data"])
        if not audio_parts:
            raise RuntimeError("O serviço de voz não retornou áudio.")
        return b"".join(audio_parts)

    return asyncio.run(synthesize())
